# Host-side tests

`make test` builds `build/mandelupic.prg` and the test-only
`build/mandelupic-force48.prg`, then runs:

```
python3 -m unittest discover -s tests -v
```

Python 3 standard library only, no packages to install. The tests read
the PRGs and their Oscar64 `.map` files from `build/` and the
end-to-end golden images from `e2e/golden/`. The 22 tests take about
40 seconds.

The end-to-end test on real hardware (`make e2e`) lives in `e2e/`; see
`e2e/README.md`. It is not part of `make test`.

| File | Contents |
|---|---|
| `mos6502.py` | Cycle-counting NMOS 6502 emulator (documented opcodes, page-crossing and branch penalties, the dummy read of indexed accesses). Raises on undocumented opcodes and decimal mode. |
| `machine.py` | Loads a PRG plus `.map` and models the Ultimate 64's turbo CPU timing, raster counter and `$D020`/`$D031` registers. |
| `test_turbo_modes.py` | 18 tests for the 48 MHz / 64 MHz display paths and the speed probe in `upic_select_display_path()`. |
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

The model is checked against real hardware in three places:

- The bisection recorded in `render_frame()`'s comment: delay `$87`
  works on an Elite II and `$A5` skews.
  `test_model_matches_hardware_bisection` requires the model to agree.
  The model also predicts that `$88` would already fail, which has not
  been tried on hardware.
- The bisection of the first 48 MHz layout (`lda col,y / sta $d020,x /
  jmp next`, 2026-09-21) on an Ultimate 64 Elite: delay 56 works and 57
  puts every row two raster lines apart.
  `test_model_matches_u64_hardware_bisection` requires the model to
  agree, which it does only because it models the dummy read.
- The position of the 48 MHz path's pixels relative to the 64 MHz
  path's, measured on an Ultimate 64 Elite against a C64 Ultimate for
  delays 98, 99 and 100 (mean error -0.80, +0.05 and +0.90 dots).
  `test_48mhz_delay_centres_the_pixels` checks the model's positions.

## What the tests check

`test_turbo_modes.py`:

- The release PRG's 64 MHz render code is byte-for-byte the v1.0.3
  instruction layout, and `render_frame`'s `dly`/`trb` operands are at
  the offsets the patcher and tests assume.
- `render_frame`'s delay loop does not cross a page (which would add a
  cycle per pass), and `nybbles` is page-aligned.
- `upic_select_display_path()` changes nothing on an Elite II. On a
  U64 it rebuilds the render code into exactly the expected bytes,
  which the test writes out instruction by instruction rather than
  with the patcher's own copy rule, and sets `upic_frame_quarters` to
  3. This holds from several starting raster lines, when the forced
  1 MHz window ends at various points during the probe, and when the
  speed register reads 1 MHz at the start (the probe writes it again
  on every retry).
- On a machine that never leaves 1 MHz the probe gives up and keeps
  the 64 MHz path (the retry budget is cut from 256 to 3 loops in the
  test to keep it fast). With a budget of one loop, a U64 at full speed
  also keeps the 64 MHz path: a single 48 MHz reading is not enough.
- A full 256-row frame at 64 MHz (Elite II) and, after the rebuild, at
  48 MHz (U64 at index 15; Elite II at index 14 in the force48 build):
  every row stays inside one raster line, rows land on consecutive
  lines, the next row's `$D012` poll finishes before its line ends,
  and pixels are 1-2 dots wide.
- The 48 MHz path shows pixels 4m, 4m+2 and 4m+3 of every 4, leaves
  at least 25 cycles of the line unused, starts 1 dot left of the
  64 MHz picture, and reaches at least as far right as the 64 MHz
  path's last visible pixel (376 dots after its first dot). With
  `UPIC_DELAY_48` every shown pixel is within 1.5 dots of where the
  64 MHz path shows it and the mean error is below 0.25 dot; one delay
  pass more or less makes the largest error bigger.
- Negative controls: delay `$A5` at 64 MHz, the unpatched code at
  48 MHz, and the first 48 MHz layout at its original delay (96) all
  fail the row checks.

`test_e2e_goldens.py` (the images `make e2e` compares against):

- Every capture in `e2e/run_e2e.py`'s `STEPS` has a golden for both
  display paths, 384 x 272 with colour indexes 0-15.
- Each 48 MHz golden shows the same picture as the 64 MHz golden,
  compared through the dot maps measured from the "pattern" goldens.
- The pattern goldens show the pixels each path is meant to show, in
  order; the 48 MHz picture starts 1 dot left of the 64 MHz one, pixel
  4m+1 is absent from it, and both reach the right edge.
- Negative control: two different pictures fail the comparison.

Mutations tried when the tests were first written (2026-09-21), each
caught: leaving the delay unpatched, and disabling the probe's
rejection of forced-1 MHz results (an Elite II then gets the 48 MHz
path). Mutations tried on 2026-09-28, each caught: removing the
dummy-read model from `mos6502.py` (2 tests fail), removing the class
increment on the probe's give-up path (1 test fails), and removing the
probe's per-retry write of the speed register (1 test fails).
