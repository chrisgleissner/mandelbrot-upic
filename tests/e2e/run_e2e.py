#!/usr/bin/env python3
"""End-to-end test on real Ultimate hardware, against golden images.

Runs build/mandelupic.prg on one or more Ultimate devices over their REST
API, steers it with the keyboard (keys are pressed on the C64 keyboard
matrix, which is what the program reads), captures the picture from the
VIC video stream and compares it with the golden image for that
device's display path:

    golden/64mhz/  C64 Ultimate, Ultimate 64 Elite II   (all 384 pixels)
    golden/48mhz/  Ultimate 64, Ultimate 64 Elite       (3 of every 4)

Every capture must also be stable: several consecutive frames have to be
identical, which catches flicker and rows that miss their raster line.
When one device of each kind takes part, the 48 MHz pictures are also
checked against the 64 MHz ones, pixel for pixel, through the dot maps
the final "pattern" step measures (see geometry.py).

Usage (or `make e2e` / `make e2e-update`, which read E2E_DEVICES from .env):

    tests/e2e/run_e2e.py --device 192.168.1.13 --device c64u
    tests/e2e/run_e2e.py --device u64 --update     # rewrite that path's goldens

The settings the program needs (Command Interface, turbo registers) are
switched on for the run if they are off, without saving them, and put
back afterwards. The device is reset by the run. Python 3 standard
library only.
"""

import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import geometry  # noqa: E402
import png  # noqa: E402
from ultimate import Ultimate, UltimateError  # noqa: E402

GOLDEN = os.path.join(HERE, "golden")
OUT = os.path.join(REPO, "build", "e2e")

# /v1/info "product" -> display path. The probe in upic_viewer.c must
# pick the same one: class 0 = 48 MHz, 1 = 64 MHz.
PRODUCTS = {
    "Ultimate 64": "48mhz",
    "Ultimate 64 Elite": "48mhz",
    "Ultimate 64 Elite II": "64mhz",
    "C64 Ultimate": "64mhz",
}
PROBE_CLASS = {"48mhz": 0, "64mhz": 1}

# The test script. Steps run in order on one program start:
#   ("capture", name)            compare the screen with golden/<path>/<name>.png
#   ("keys", [keys], expect)     tap the keys; expect is ("generate",) when
#                                they start a new picture, else (symbol, value)
#                                that the program's state must reach
#   ("pattern",)                 fill the picture with geometry.pattern_byte()
# Views are chosen for detail and for the code paths they reach, while
# keeping the run short: the overview is the only mirrored (symmetric)
# view; the diagonal pan (A and W held together, one new picture) puts
# the centre on the edge of the period-2 bulb, and the smallest box
# then zooms 16x there, past the fixed-point limit, so the pixel step
# is clamped to 1.
STEPS = [
    ("capture", "overview"),
    ("keys", ["z"], ("box_mode", 1)),
    ("capture", "box"),
    ("keys", ["return"], ("generate",)),
    ("capture", "zoom-2x"),
    ("keys", [["a", "w"]], ("generate",)),
    ("capture", "pan-up-left"),
    ("keys", ["z"] + ["minus"] * 14, ("size_units", 4)),
    ("capture", "box-smallest"),
    ("keys", ["return"], ("generate",)),
    ("capture", "zoom-max"),
    ("pattern",),
    ("capture", "pattern"),
]

STABLE_FRAMES = 8
GENERATE_TIMEOUT = 120.0


class Failure(Exception):
    pass


def load_symbols(prg_path):
    """Symbol addresses from the Oscar64 .lbl file next to the PRG."""
    symbols = {}
    with open(os.path.splitext(prg_path)[0] + ".lbl") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3 and parts[0] == "al":
                symbols[parts[2].lstrip(".")] = int(parts[1], 16)
    return symbols


def wait_for(predicate, timeout, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class DeviceRun:
    def __init__(self, host, port, prg, symbols, update, password):
        self.u = Ultimate(host, password=password)
        self.host = host
        self.port = port
        self.prg = prg
        self.sym = symbols
        self.update = update
        self.frames = {}
        self.failures = []
        self.mode = None
        self.restore = []

    def log(self, text):
        print("[%s %s] %s" % (self.host, self.mode or "?", text), flush=True)

    def fail(self, text):
        self.failures.append(text)
        self.log("FAIL " + text)

    # -- setup ------------------------------------------------------------
    def configure(self):
        product = self.u.info()["product"]
        if product not in PRODUCTS:
            raise Failure("unsupported product %r" % product)
        self.mode = PRODUCTS[product]
        self.log(product)
        changed = False
        for category, item in (("C64 and Cartridge Settings", "Command Interface"),
                               ("U64 Specific Settings", "Turbo Control")):
            current, values = self.u.get_config(category, item)
            wanted = "Enabled" if item == "Command Interface" else \
                next(v for v in values if v.endswith("Turbo Registers"))
            if current != wanted:
                self.u.set_config(category, item, wanted)
                self.restore.append((category, item, current))
                changed = True
        if changed:
            # Give the firmware time to apply the settings before the
            # program starts talking to the command interface.
            time.sleep(3)

    def unconfigure(self):
        for category, item, value in reversed(self.restore):
            try:
                self.u.set_config(category, item, value)
            except UltimateError as e:
                self.log("could not restore %s: %s" % (item, e))

    # -- program state ----------------------------------------------------
    def peek(self, name):
        return self.u.read_memory(self.sym[name], 1)[0]

    def diagnostics(self):
        try:
            return "probe class %d, retries left %d, UCI status %s" % (
                self.peek("upic_probe_class"), self.peek("upic_probe_tries"),
                self.u.read_memory(0xDF1C, 4).hex())
        except UltimateError as e:
            return str(e)

    def generated(self):
        return self.peek("mandel_gen_tenths") != 0xFF

    def start(self):
        # mandel_gen_tenths is written only when a picture is complete
        # (with a TOD value, never $FF), so $FF marks "not done yet".
        image = bytearray(self.prg)
        image[2 + self.sym["mandel_gen_tenths"] - 0x0801] = 0xFF
        t0 = time.monotonic()
        self.u.run_prg(bytes(image))
        if not wait_for(self.generated, GENERATE_TIMEOUT, 0.5):
            raise Failure("no picture after %ds (%s)" % (GENERATE_TIMEOUT, self.diagnostics()))
        self.log("first picture after %.1fs" % (time.monotonic() - t0))
        probe = self.peek("upic_probe_class")
        if probe != PROBE_CLASS[self.mode]:
            raise Failure("speed probe chose class %d, expected %d" % (probe, PROBE_CLASS[self.mode]))

    def keys(self, keys, expect):
        """Tap the keys and wait for their effect. If the effect doesn't
        start within 3 s the taps are sent once more: a tap can be lost,
        and every step here is safe to repeat while it has had no
        effect (a view that hasn't changed, a box that isn't open yet,
        a box already at its smallest size)."""
        if expect[0] == "generate":
            self.u.write_memory(self.sym["mandel_gen_tenths"], [0xFF])
            before = self.u.read_memory(self.sym["mandel_x0"], 8)
            # The view changes as soon as the key is seen, long before
            # the new picture is finished.
            started = lambda: self.u.read_memory(self.sym["mandel_x0"], 8) != before
        else:
            name, value = expect
            started = lambda: self.peek(name) == value
        t0 = time.monotonic()
        for attempt in range(2):
            self.u.tap_keys(keys)
            if wait_for(started, 3):
                break
            self.log("keys %s had no effect%s" % (keys, ", tapping again" if attempt == 0 else ""))
        else:
            raise Failure("keys %s had no effect (%s)" % (keys, self.diagnostics()))
        if expect[0] == "generate":
            if not wait_for(self.generated, GENERATE_TIMEOUT, 0.5):
                raise Failure("keys %s: no picture after %ds" % (keys, GENERATE_TIMEOUT))
            self.log("keys %s: new picture after %.1fs" % (keys, time.monotonic() - t0))
        else:
            time.sleep(0.2)     # a frame or two for the markers to be drawn

    def pattern(self):
        # Byte column c is 256 bytes (one per row) at upic_buffer_reloc +
        # 256*c for the first 8 columns and at upic_buffer + 256*(c-8)
        # after that; both ranges are written in 4 KB pieces.
        reloc = b"".join(bytes([geometry.pattern_byte(c)]) * 256 for c in range(8))
        main = b"".join(bytes([geometry.pattern_byte(c)]) * 256 for c in range(8, geometry.COLUMNS))
        self.u.pause()
        try:
            for base, data in ((self.sym["upic_buffer_reloc"], reloc), (self.sym["upic_buffer"], main)):
                for i in range(0, len(data), 4096):
                    self.u.write_memory(base + i, data[i:i + 4096])
        finally:
            self.u.resume()
        time.sleep(0.2)

    # -- capture ----------------------------------------------------------
    def capture(self, name, stream):
        frames = stream.frames(STABLE_FRAMES)
        distinct = len(set(tuple(f) for f in frames))
        frame = frames[0]
        self.frames[name] = frame
        os.makedirs(os.path.join(OUT, self.mode), exist_ok=True)
        png.write(os.path.join(OUT, self.mode, name + ".png"), frame, self.palette)
        if distinct != 1:
            self.fail("%s: %d different frames out of %d (flicker)" % (name, distinct, STABLE_FRAMES))
            return
        golden = os.path.join(GOLDEN, self.mode, name + ".png")
        if self.update:
            os.makedirs(os.path.dirname(golden), exist_ok=True)
            png.write(golden, frame, self.palette)
            self.log("%s: golden written" % name)
            return
        if not os.path.exists(golden):
            self.fail("%s: no golden image %s (run with --update)" % (name, os.path.relpath(golden, REPO)))
            return
        expected, _ = png.read(golden)
        bad = [(y, x) for y in range(min(len(frame), len(expected)))
               for x in range(len(frame[y])) if frame[y][x] != expected[y][x]]
        if len(frame) != len(expected):
            self.fail("%s: %d lines, golden has %d" % (name, len(frame), len(expected)))
        elif bad:
            diff = [bytearray(len(row)) for row in frame]
            for y, x in bad:
                diff[y][x] = 1
            png.write(os.path.join(OUT, self.mode, name + "-diff.png"), diff,
                      [(0, 0, 0), (255, 0, 0)])
            self.fail("%s: %d pixels differ from the golden, first at row %d dot %d (see %s)" % (
                name, len(bad), bad[0][0], bad[0][1], os.path.relpath(os.path.join(OUT, self.mode), REPO)))
        else:
            self.log("%s: matches golden" % name)

    # -- run --------------------------------------------------------------
    def run(self):
        try:
            self.configure()
            self.palette = [tuple(self.prg[2 + self.sym["mandelbrot_palette"] - 0x0801 + 3 * i:][:3])
                            for i in range(16)]
            self.start()
            with self.u.video_stream(self.port) as stream:
                for step in STEPS:
                    if step[0] == "capture":
                        self.capture(step[1], stream)
                    elif step[0] == "keys":
                        self.keys(step[1], step[2])
                    else:
                        self.pattern()
        except (Failure, UltimateError) as e:
            self.fail(str(e))
        finally:
            try:
                self.unconfigure()
            except UltimateError as e:
                self.log("restoring settings failed: %s" % e)


def cross_check(runs):
    """Compare the 48 MHz captures with the 64 MHz ones, if both ran."""
    by_mode = {r.mode: r for r in runs if "pattern" in r.frames}
    if set(by_mode) != {"48mhz", "64mhz"}:
        return []
    r48, r64 = by_mode["48mhz"], by_mode["64mhz"]
    try:
        map48 = geometry.pixel_map(r48.frames["pattern"][120], "48mhz")
        map64 = geometry.pixel_map(r64.frames["pattern"][120], "64mhz")
    except ValueError as e:
        return ["cross-check: %s" % e]
    failures = []
    for name in r48.frames:
        if name in r64.frames:
            bad = geometry.compare_48_to_64(r48.frames[name], r64.frames[name], map48, map64)
            if bad:
                failures.append("cross-check %s: %d dots differ between %s and %s, first at row %d dot %d"
                                % (name, len(bad), r48.host, r64.host, bad[0][0], bad[0][1]))
            else:
                print("[cross-check] %s: 48 MHz picture matches the 64 MHz one" % name, flush=True)
    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", action="append", default=[],
                    help="host name or IP of an Ultimate (repeatable); default: $E2E_DEVICES")
    ap.add_argument("--prg", default=os.path.join(REPO, "build", "mandelupic.prg"))
    ap.add_argument("--update", action="store_true", help="write the captures as the new goldens")
    ap.add_argument("--password", default=os.environ.get("ULTIMATE_PASSWORD"))
    args = ap.parse_args()
    devices = args.device or os.environ.get("E2E_DEVICES", "").split()
    if not devices:
        ap.error("no devices: pass --device or set E2E_DEVICES")
    with open(args.prg, "rb") as f:
        prg = f.read()
    symbols = load_symbols(args.prg)
    runs = [DeviceRun(h, 11000 + 10 * i, prg, symbols, args.update, args.password)
            for i, h in enumerate(devices)]
    threads = [threading.Thread(target=r.run) for r in runs]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    failures = [f for r in runs for f in ("%s: %s" % (r.host, x) for x in r.failures)]
    failures += cross_check(runs)
    print("\n%d device(s), %.0fs: %s" % (len(runs), time.monotonic() - t0,
                                          "FAILED" if failures else "passed"))
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
