# End-to-end test on real hardware

`make e2e` runs the release PRG (`build/mandelupic.prg`) on one or more
real Ultimate devices, drives it through a fixed sequence of key
presses, captures the picture from the Ultimate's VIC video stream and
compares each capture with a golden image. The host-side tests in
`tests/` check timing in an emulator; this test checks the picture the
real hardware produces, which no emulator can do for this program
(VICE does not emulate the Ultimate's UCI or turbo hardware).

A run with one Ultimate 64 / Elite I and one C64 Ultimate / Elite II
in parallel takes about 2.5 minutes. Python 3 standard library only.

## Requirements

- Ultimate firmware with the keyboard input API
  (`POST /v1/machine:input`) and the video stream
  (`PUT /v1/streams/video:start`). Firmware 3.15 on an Ultimate 64
  Elite and 1.2RC on a C64 Ultimate were used to create the goldens.
- Supported products (from `GET /v1/info`): Ultimate 64 and Ultimate
  64 Elite (48 MHz path), Ultimate 64 Elite II (reported as
  `Ultimate 64-II` by firmware 3.15a) and C64 Ultimate (64 MHz path). Any other product fails with "unsupported product".
- The host must be on the same LAN as the devices and must receive
  multicast UDP. The video stream is sent to multicast group
  `239.0.1.64`. Each device gets its own UDP port (11000 for the first
  device, 11010 for the second, and so on; `--port` changes the first), and packets from any other
  source address are ignored, so several devices can stream at the
  same time. A host firewall has to allow incoming UDP on those ports.
  Unicast streaming is not used because the device intermittently
  fails to resolve the host's address ("Network Host Resolve Error").
- If the devices have a network password, set `ULTIMATE_PASSWORD` in
  the environment (or pass `--password` to `run_e2e.py`). Make does not
  pass values from `.env` to the script's environment.

The run resets each device and starts the PRG on it. It needs the
Command Interface enabled and the turbo registers selected (`Turbo
Control` set to the product's own "... Turbo Registers" value). If
either setting is off, the run switches it on for the session only
(not saved to flash) and restores the previous value afterwards.

## Running

Set the devices in `.env` (see `.env.example`), as host names or IPs
separated by spaces:

```
E2E_DEVICES = 192.168.1.13 c64u
```

Then:

```
make e2e             # compare with the goldens
make e2e-update      # write the captures as the new goldens
```

Both targets build `build/mandelupic.prg` first if needed (not
`README.pdf`). To run the script directly:

```
tests/e2e/run_e2e.py --device 192.168.1.13 --device c64u
tests/e2e/run_e2e.py --device u64 --update
tests/e2e/run_e2e.py --device u64 --prg build/other.prg
```

The script reads symbol addresses from the Oscar64 `.lbl` file next to
the PRG, so a PRG and its `.lbl` must come from the same build.

The script prints one line per step and device, then a summary. It
exits with status 1 if any check failed. Every capture is written to
`build/e2e/<path>/<name>.png` (`<path>` is `48mhz` or `64mhz`), whether
it passed or not.

## What a run checks

For each device:

1. The product name selects the expected display path. After the
   first picture is complete, the speed probe's result
   (`upic_probe_class`: 0 = 48 MHz, 1 = 64 MHz) must match it.
2. Each capture must be stable: 8 consecutive complete frames from the
   video stream must be identical. This catches flicker and rows that
   miss their raster line.
3. Each capture must match `golden/<path>/<name>.png` exactly, dot for
   dot. On a mismatch the run also writes
   `build/e2e/<path>/<name>-diff.png`, with the differing dots in red,
   and reports how many dots differ and where the first one is.

When one 48 MHz and one 64 MHz device take part, the run also
cross-checks every 48 MHz capture against the 64 MHz capture of the
same name. The 48 MHz path shows 3 of every 4 pixels (pixel 4m two
dots wide), so the comparison goes through dot maps measured from the
final "pattern" step (`geometry.py`): the pattern gives every pixel a
different colour from its neighbours, so the captured row shows which
picture pixel each dot displays. Since v1.2.0 both paths have an exact
pitch and fill all 384 dots; `tests/test_e2e_goldens.py` also checks the
measured maps against that geometry.

## Steps

The steps run in order on one program start (`STEPS` in
`run_e2e.py`):

| Step | Keys | Covers |
|---|---|---|
| `overview` | (none) | The first complete picture after start: the default view, the only mirrored (symmetric) view in the run |
| `box` | `Z` | Box mode: the 4 corner markers drawn into the picture at the default box size |
| `zoom-2x` | `RETURN` | Confirming the box: a new picture at 2x zoom, generated without the symmetry shortcut |
| `pan-up-left` | `A`+`W` held together | Panning in browse mode, both axes in one step (one new picture); moves the centre onto the edge of the period-2 bulb |
| `box-smallest` | `Z`, then `-` 14 times | Box mode again, shrunk to its smallest size (`size_units` = 4) |
| `zoom-max` | `RETURN` | Zooming 16x from there, past the fixed-point precision limit, so the pixel step is clamped to 1 |
| `pattern` | (none) | The stripe pattern written into the picture buffer over the REST API while the machine is paused; used for the dot maps above |

Keys are pressed on the C64 keyboard matrix through
`POST /v1/machine:input`, which is what the program reads. The
firmware holds each tapped key for about 60 ms with a 40 ms gap, so a
program that polls the matrix once per frame sees every press and
release. A tap can still be lost. If a key step has no visible effect
within 3 seconds, its taps are sent once more; every key step in the
run is harmless to repeat while it has had no effect (a view that has
not changed, a box that is not open yet, a box already at its smallest
size).

A key step waits for one of two things:

- `("generate",)`: the view coordinates (`mandel_x0` onwards) change,
  and then the new picture completes (`mandel_gen_tenths` is set, up
  to 120 seconds).
- `(symbol, value)`: a byte of program state reaches the given value,
  for example `("box_mode", 1)`.

## Golden images

`golden/48mhz/` holds the goldens for the Ultimate 64 / Elite I path
(3 of every 4 pixels shown), `golden/64mhz/` those for the C64
Ultimate / Elite II path. Each is an 8-bit palette PNG of 384 x 272
dots, the size of the video stream's frame, with colour indexes 0-15.
The PNG palette is the program's default gradient, read from the PRG,
so an image viewer shows the goldens in the program's own colours.

To update goldens after an intended change to the picture:

1. Run `make e2e-update` with at least one device of each kind in
   `E2E_DEVICES`. It rewrites only the goldens of the display paths
   the listed devices use.
2. Run `make e2e` again with both kinds of device, so the new goldens
   are compared and cross-checked.
3. Run `make test`. `tests/test_e2e_goldens.py` checks the committed
   goldens without hardware: every capture exists for both paths with
   the expected size and colour range, each 48 MHz golden matches its
   64 MHz golden through the pattern dot maps, the pattern goldens show
   the exact geometry of each path, and a comparison of two different
   pictures fails (negative control).

## Adding a step

1. Add the step to `STEPS` in `run_e2e.py`, before `("pattern",)`:
   the pattern step overwrites the picture buffer, so it has to stay
   last. A key step is `("keys", [keys], expect)` followed by a
   `("capture", name)` step. Key names are the firmware's (for example
   `"z"`, `"return"`, `"minus"`); a nested list is a chord, its keys
   held together.
2. Choose `expect` so that the step's effect is visible in program
   state as soon as the key is seen, and so that sending the taps a
   second time is harmless if the first taps had no effect. The retry
   in `DeviceRun.keys()` depends on both. Symbols come from the PRG's
   `.lbl` file.
3. Create the goldens with `make e2e-update` on one device of each
   kind, then run `make e2e` and `make test`.
   `test_e2e_goldens.py` takes the capture names from `STEPS`, so it
   checks the new goldens without changes.

## Troubleshooting

- `no picture after 120s (probe class C, last probe L lines, UCI
  status XXXXXXXX)`: the program did not complete its first picture.
  The diagnostics are read from the running machine:
  - `upic_probe_class`: 0 or 1 once the speed probe has chosen the
    48 MHz or 64 MHz path; 2 while it is still running, or after it gave
    up following 256 loops without turbo (check `Turbo Control`; the
    program then runs at 1 MHz).
  - `uii_turbo_probe_result`: raster lines the last probe loop took (the
    probe is the library's `uii_turbo_probe_max()`): about 16 at 64 MHz,
    21-22 at 48 MHz, 92 for a loop entirely at 1 MHz. 0 means the
    program has not reached the probe yet and is stuck earlier, in the
    UCI detection or palette push.
  - The UCI status is the 4 bytes at `$DF1C`-`$DF1F`; the first is the
    status register (see the UCI library manual, `lib/ultimate-uci-oscar64/docs/UCILIB_MANUAL.md` §2). A non-zero `STATE`
    (bits 4-5) while the program has not reached the probe points at a
    stuck UCI handshake.
- `got N of 8 video frames`: the video stream does not reach the host.
  Check that the host is on the same LAN, that multicast is not
  filtered between the device and the host, and that the firewall
  allows the UDP port.
- `keys [...] had no effect`: the key taps were sent twice without the
  expected change in program state. Check that the firmware has the
  input API and that the program was in the state the step expects.
- `speed probe chose class C, expected E`: the probe picked the wrong
  display path for the product.
- `N different frames out of 8 (flicker)`: the picture was not stable
  while it was captured; rows missing their raster line cause this.
- `N pixels differ from the golden`: compare `build/e2e/<path>/<name>.png`
  with the golden and look at `<name>-diff.png`. If the change is
  intended, update the goldens as described above.
- `cross-check NAME: N dots differ`: the 48 MHz and 64 MHz devices
  showed different pictures for the same step.

## Running from WSL2

WSL2's default NAT networking never delivers the video stream to Linux:
the device sends to the Windows host, not to the WSL VM's private
address. Set up once (tested on Windows 11 with WSL 2.7.10, firmware
3.15a):

1. `C:\Users\<you>\.wslconfig`:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   then `wsl --shutdown` from PowerShell (or reboot). Check with
   `wslinfo --networking-mode` (prints `mirrored`); WSL then has the
   PC's own LAN address.
2. Open the stream ports in the Hyper-V firewall, which blocks inbound
   traffic into WSL by default. In an admin PowerShell:
   ```powershell
   New-NetFirewallHyperVRule -Name C64VideoStream -DisplayName "C64 Ultimate stream" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 11000-11010 -Action Allow
   ```
   The rule is persistent. 11000-11010 covers two devices (port 11000 +
   10 per device); widen it for more.
3. In mirrored mode WSL shares port numbers with Windows. A Windows
   program bound to the same UDP port (for example OBS Studio receiving
   the Ultimate's stream on 11000) makes the run fail to bind; close it
   while testing, or run one device at a time with a free port inside
   the rule's range (`--port 11005`). The device sends each stream to one destination at a
   time anyway.

## Files

| File | Contents |
|---|---|
| `run_e2e.py` | The test: steps, checks, golden comparison, cross-check |
| `ultimate.py` | Minimal client for the Ultimate REST API and the VIC video stream |
| `geometry.py` | Stripe pattern, dot maps and the 48 MHz / 64 MHz comparison |
| `png.py` | Minimal palette PNG reader and writer |
| `golden/` | Golden images, one directory per display path |
