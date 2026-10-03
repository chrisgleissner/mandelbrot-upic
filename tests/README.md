# Host-side tests

`make test` builds `build/mandelupic.prg` and the test-only
`build/mandelupic-force48.prg`, then runs:

```
python3 -m unittest discover -s tests -v
```

Python 3 standard library only, no packages to install. The tests read
the PRGs and their Oscar64 `.map` files from `build/` and the
end-to-end golden images from `e2e/golden/`. The 16 tests take about
40 seconds (about 30 of them for the probe's full 256-loop give-up
case).

The end-to-end test on real hardware (`make e2e`) lives in `e2e/`; see
`e2e/README.md`. It is not part of `make test`.

| File | Contents |
|---|---|
| `mos6502.py` | Cycle-counting NMOS 6502 emulator (documented opcodes, page-crossing and branch penalties, the dummy read of indexed accesses). Raises on undocumented opcodes and decimal mode. |
| `machine.py` | Loads a PRG plus `.map` and models the Ultimate 64's turbo CPU timing, raster counter and `$D020`/`$D031` registers. |
| `test_turbo_modes.py` | 13 tests for the 48 MHz / 64 MHz display paths (the library's generated Upic renderer) and the speed probe in `upic_select_display_path()`. |
| `test_e2e_goldens.py` | 4 consistency checks on the committed end-to-end golden images (no hardware needed). |

## Timing model

`machine.py` models the Ultimate 64's turbo CPU timing as behaviour;
its docstring lists the rules. In short:

- Each phi2 cycle offers as many CPU sub-slots as the board's top
  speed (64 on Elite II / C64U, 48 on U64 / Elite I). A speed of k MHz
  lets the CPU use k of them, evenly spaced.
- The VIC-II uses one sub-slot of every phi2 (and one more on badline
  character fetch cycles while the display is on).
- A `$D031` write takes effect two sub-slots later. A VIC register
  read needs one extra sub-slot. `$D012` changes at the start of phi2
  cycle 1.
- The extra sub-slot also applies to the dummy read an NMOS 6502 makes
  one cycle before an indexed access's real access: on every indexed
  store, and on an indexed load only when the index crosses a page.
  `sta $d020,x` therefore reads `$D020` before writing it and pays for
  that read. `mos6502.py` reports which cycles of an instruction read
  `$D000`-`$DFFF`; only the timing of the dummy read is modelled, since
  its data is discarded and has no side effect on the modelled
  registers.
- A `$D020` write shows at the next dot boundary.
- The 1 MHz window after a reset can be switched on up to a given
  sub-slot.

Not modelled: CIA timing, sprite DMA, NTSC. How intermediate speeds
(such as index 14 on an Elite II, used by the force48 build) spread
their cycles within a phi2 is modelled only approximately (evenly).
With that spread every other row of the force48 build starts one phi2
later in the model, while on a real C64 Ultimate every row started on
the same dot (50 frames checked, 2026-09-28). The force48 test
therefore does not require a constant row start.

The model is checked against real hardware through the geometry of
both display paths, measured from the VIC video stream on an Ultimate
64 Elite II and an Ultimate 64 Elite (firmware 3.15a, 2026-10-02): pixel
0 on the first visible dot on both paths, every pixel one dot wide at
64 MHz, and the 48 MHz path exact every 16 pixels. The tests below
require the model to produce exactly that. Earlier checks (v1.1.x) also
had the model reproduce two delay bisections and the 48 MHz position
errors of the old in-project renderer, which the library replaced.

## What the tests check

`test_turbo_modes.py` (since v1.2.0 the display is the library's
`ultimate_upic_lib`; `upic_select_display_path()` calls
`uii_upic_init(UII_UPIC_AUTO)`, which generates the renderer):

- The probe picks the 64 MHz path on an Elite II model and the 48 MHz
  path on a U64 model, and sets `upic_frame_quarters` (4 / 3). It still
  does when the forced 1 MHz window after a reset ends at various points
  during the probe, and when the speed register reads 1 MHz at the
  start. On a machine that never leaves 1 MHz it gives up after its
  full 256 loops (about 30 s in the model: the library keeps its retry
  counter in a local), keeps the 64 MHz renderer and leaves
  `upic_probe_class` at 2.
- A full 256-row frame on both paths: every row stays inside one raster
  line, rows land on consecutive lines, and the next row's `$D012` poll
  finishes before its line ends.
- 64 MHz: pixel k on dot first + k for all 384 pixels of every row.
- 48 MHz: pixels 4m, 4m+2 and 4m+3 of all 96 groups; every 16th pixel
  exactly on its ideal dot, every pixel within one dot of it (a group is
  24 cycles = 4.085 dots), and pixel 0 on the same dot as on the 64 MHz
  path.
- Display window (the live view's Bar, v1.2.0): rows 124-131 and rows
  240-255 (lines past 255) on both paths draw only those rows, each on
  the raster line and dots it has in the full frame.
- force48 build (Elite II at index 14): the 48 MHz renderer is built,
  every row shows 288 pixels on its own line in the 4m / 4m+2 / 4m+3
  order; the probe is not linked.
- Negative control: the 64 MHz renderer run at 48 MHz overruns its
  lines, so the row checks can fail.

`test_e2e_goldens.py` (the images `make e2e` compares against):

- Every capture in `e2e/run_e2e.py`'s `STEPS` has a golden for both
  display paths, 384 x 272 with colour indexes 0-15.
- Each 48 MHz golden shows the same picture as the 64 MHz golden,
  compared through the dot maps measured from the "pattern" goldens.
- The pattern goldens show the exact geometry: at 64 MHz dot x shows
  pixel x for all 384 dots; at 48 MHz pixels 4m, 4m+2 and 4m+3 (4m+1
  absent), every 16th pixel on its ideal dot and every pixel within one
  dot of it, filling all 384 dots.
- Negative control: two different pictures fail the comparison.

Mutation check on 2026-10-02: building with the 48 MHz delay one count
off (`-dUII_UPIC_DELAY_48=53`) fails `test_paths_start_on_the_same_dot`.
Earlier mutation checks (2026-09-21/28) were made on the in-project
renderer and probe that the library replaced: leaving the delay
unpatched, disabling the probe's rejection of forced-1 MHz results,
removing the dummy-read model from `mos6502.py`, removing the probe's
give-up class and its per-retry write of the speed register -- each was
caught.
