"""Ultimate 64 CPU/VIC-II timing model for host-side tests.

Loads a compiled PRG and its Oscar64 .map, and models the Ultimate 64's
turbo CPU timing at the level of detail this project's cycle-exact code
needs. The model describes behaviour, not any particular implementation:

- Each phi2 cycle is divided into as many sub-slots as the board's top
  turbo speed: 64 on Elite II / C64U, 48 on the original Ultimate 64 /
  Elite I. Time is counted in sub-slots.
- The CPU may finish a cycle only in the sub-slots used by the current
  $D031 speed index: k evenly spaced sub-slots per phi2 for a speed of
  k MHz (every sub-slot at the top index, only the last one at index
  0, i.e. 1 MHz).
- The VIC-II uses the first sub-slot of every phi2 for its own memory
  access, so the CPU gets 63 cycles per phi2 at 64 MHz and 47 at
  48 MHz. With the display enabled, badline character fetches also
  take the first sub-slot of the second half of those cycles.
- A $D031 write takes effect two sub-slots after it.
- A VIC register read needs one extra sub-slot. Writes do not. This
  includes the dummy read an indexed store makes (see mos6502.py).
- The raster counter advances at the start of phi2 cycle 1 of each line
  (63 phi2 per PAL line, 312 lines), and at the 311 -> 0 wrap it reads
  311 for one more phi2.
- A $D020 write shows up at the next dot boundary (8 dots per phi2).
- The CPU runs at 1 MHz for a few seconds after a reset, whatever $D031
  says. A test can switch this on up to a given sub-slot.

The model is checked against real hardware in one place: the render
delay bisection recorded in render_frame() (see test_turbo_modes.py).

Not modelled: CIA timing, sprite DMA, NTSC.
"""

import os
import re

from mos6502 import CPU

PHI2_PER_LINE = 63
LINES_PER_FRAME = 312
DOTS_PER_LINE = 8 * PHI2_PER_LINE

# $D031 speed index -> MHz, from the firmware's CPU speed menus
# (1541ultimate software/u64/u64_config.cc, speeds_u64 / speeds_u64ii).
SPEEDS = {
    6: [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48],
    8: [1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48, 64],
}
# Boards are identified by their sub-slots per phi2 divided by 8 (one
# eighth of a phi2 is one dot).
U64 = 6          # original Ultimate 64 / Elite I: 48 sub-slots
ELITE2 = 8       # Ultimate 64 Elite II / C64U: 64 sub-slots

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(REPO, 'build')

_MAP_LINE = re.compile(
    r'^[-0-9a-f]{2}:([0-9a-f]+) - ([0-9a-f]+) : ([A-Za-z_][\w@]*), (\S+)')


def load_build(name):
    """Return (64 KB memory image, {symbol: (start, end)}) for build/<name>."""
    with open(os.path.join(BUILD, name + '.prg'), 'rb') as f:
        prg = f.read()
    load = prg[0] | (prg[1] << 8)
    image = bytearray(0x10000)
    image[load:load + len(prg) - 2] = prg[2:]
    symbols = {}
    with open(os.path.join(BUILD, name + '.map')) as f:
        for line in f:
            m = _MAP_LINE.match(line)
            if m and m.group(3) not in symbols:
                symbols[m.group(3)] = (int(m.group(1), 16), int(m.group(2), 16))
    return image, symbols


def allowed_subslots(ratio, index):
    """Sub-slots c (0..8*ratio-1) in which the CPU may finish a cycle:
    k of them, evenly spaced, for a speed of k MHz."""
    s = 8 * ratio
    k = SPEEDS[ratio][index]
    return [((c + 1) * k) // s != (c * k) // s for c in range(s)]


class Machine:
    def __init__(self, image, ratio, d031=0x8F, start_line=0,
                 slow_until=0):
        self.mem = bytearray(image)
        self.ratio = ratio
        self.P = 8 * ratio
        self.allowed = [allowed_subslots(ratio, i) for i in range(16)]
        self.t = start_line * PHI2_PER_LINE * self.P   # last finished sub-slot
        self.slow_until = slow_until
        self.d011 = 0x1B
        self.speed = d031 & 0x0F
        self.pending = None          # (first sub-slot, new speed index)
        self.d031 = d031
        self.d020_writes = []        # (sub-slot, value, instruction address)
        self.d012_reads = []         # (sub-slot, instruction address)
        self.cpu = CPU(self)

    # -- time -----------------------------------------------------------
    @property
    def ticks_per_line(self):
        return PHI2_PER_LINE * self.P

    def line_of(self, period):
        return period // self.ticks_per_line

    def dot_of(self, period):
        return (period + 1) // self.ratio

    def _speed_at(self, x):
        if self.pending and x >= self.pending[0]:
            self.speed = self.pending[1]
            self.pending = None
        return self.speed

    def _vic_takes(self, x):
        c = x % self.P
        if c == 0:
            return True
        if c == 4 * self.ratio and self.d011 & 0x10:
            line = self._raster(x)
            cycle = (x // self.P) % PHI2_PER_LINE + 1
            if (0x30 <= line <= 0xF7 and (line & 7) == (self.d011 & 7)
                    and 15 <= cycle <= 54):
                return True
        return False

    def _next_cycle(self, prev, vic_read):
        x = prev + 1
        if x < self.slow_until:
            # Only the last sub-slot of each phi2 is allowed; the VIC
            # never uses it or the one before it.
            x += (self.P - 1 - x % self.P)
            if vic_read and x - 1 == prev:
                x += self.P
            return x
        while True:
            c = x % self.P
            ok = self.allowed[self._speed_at(x)][c]
            if ok and not self._vic_takes(x):
                if not vic_read or (x - 1 > prev and not self._vic_takes(x - 1)):
                    return x
            x += 1

    def tick(self, cycles, vic_reads=()):
        for i in range(cycles):
            self.t = self._next_cycle(self.t, i in vic_reads)

    # -- raster ---------------------------------------------------------
    def _raster(self, x):
        phi2 = x // self.P
        line = (phi2 // PHI2_PER_LINE) % LINES_PER_FRAME
        if line == 0 and phi2 % PHI2_PER_LINE == 0:
            return LINES_PER_FRAME - 1      # wrap to 0 is one phi2 late
        return line

    # -- bus ------------------------------------------------------------
    def fetch(self, addr):
        return self.mem[addr & 0xFFFF]

    def read(self, addr):
        if addr == 0xD012:
            self.d012_reads.append((self.t, self.cpu.cur_pc))
            return self._raster(self.t - 1) & 0xFF
        if addr == 0xD011:
            return (self.d011 & 0x7F) | ((self._raster(self.t - 1) >> 8) << 7)
        if addr == 0xD031:
            return self.d031
        if 0xD000 <= addr < 0xE000:
            return 0xFF
        return self.mem[addr]

    def write(self, addr, value):
        if addr == 0xD020:
            self.d020_writes.append((self.t, value, self.cpu.cur_pc))
        elif addr == 0xD011:
            self.d011 = value
        elif addr == 0xD031:
            self.d031 = value
            self._speed_at(self.t)
            self.pending = (self.t + 2, value & 0x0F)
        elif 0xD000 <= addr < 0xE000:
            pass
        else:
            self.mem[addr] = value

    def call(self, addr, **kw):
        self.cpu.call(addr, **kw)
        return self.cpu.a
