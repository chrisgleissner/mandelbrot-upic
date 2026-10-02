# Changelog

## [1.1.0]

One PRG for both turbo ceilings: Ultimate 64 Elite II / C64U (64 MHz)
and original Ultimate 64 / Elite I (48 MHz).

Contributed by **Christian Gleissner**
([chrisgleissner](https://github.com/chrisgleissner),
[pull request #2](https://github.com/xahmol/mandelbrot-upic/pull/2)):
the 48 MHz display path and its startup speed probe, the UCI start-up
hang fix, and the host-side and end-to-end test suites below. See
`CREDITS.md`.

- At startup, `upic_select_display_path()` (`upic_viewer.c`) times a
  fixed 64,764-cycle loop against the VIC-II raster counter: 16.3 PAL
  lines at 64 MHz, 21.9 at 48 MHz. It retries while the forced 1 MHz
  mode (a few seconds after every reset) is still active, and only
  accepts a result that two loops in a row agree on. Each retry writes
  the turbo speed register again, so a speed setting that was reset
  after `turbo_fast()` is restored. If no result is accepted within 256
  loops (about 20 s at 1 MHz, for example when the program is started
  without its `.cfg`), the probe gives up and keeps the 64 MHz path
  instead of waiting forever with a black screen.
- On a 48 MHz machine it rebuilds the Upic line renderer in place to
  show pixels 4m, 4m+2 and 4m+3 of every 4, 8 cycles each: 288 of the
  384 pixels per line, about 1.36 dots wide. Each pair of byte columns
  A, B becomes `lda A,y / sta $d020 / ldx B,y / stx $d020 /
  lda nybbles,x / sta $d020`, with no indexed access to I/O space.
  94 of the 96 pairs are drawn; the last two would fall outside the
  visible area. The render delay changes from 135 to 99 loop passes,
  which leaves 38 cycles of the line to spare. Measured on an Ultimate
  64 Elite against a C64 Ultimate, every shown pixel is within 1.5 dots
  of where the 64 MHz path shows the same pixel, and the picture starts
  1 dot further left. On a 64 MHz machine nothing is patched and the
  render code runs as in v1.0.3.
- Live frames during generation are paced by work done instead of one
  per column: one frame per column on the 64 MHz path, as before, and
  one per 3/4 column on the 48 MHz path (`upic_frame_quarters`). A
  column takes about 1.34 times as long at 48 MHz; with this pacing an
  Ultimate 64 Elite and a C64 Ultimate both show the picture in 26% of
  frames during generation.
- Fixed a start-up hang in the UCI library (`ultimate_common_lib.c`,
  inherited from the upstream library): the program stopped with a
  black screen before the first picture in 6 of 15 starts on an
  Ultimate 64 Elite and 1 of 15 on a C64 Ultimate. The firmware 3.15
  unlock is now only sent when the UCI isn't already mapped, the
  write-only control register is assigned instead of read-modified,
  the ERROR check tests bit 3 instead of bit 2, `uii_sendcommand()`
  waits until the command has been taken, and the wait for idle
  releases an orphaned reply. After the fix no start hung in 30 starts
  on the Ultimate 64 Elite and 20 on the C64 Ultimate. See
  `UCILIBMANUAL.md`.
- `make force48` builds a test-only PRG that always takes the 48 MHz
  path at speed index 14 (48 MHz on Elite II / C64U), so the path can
  be checked on a 64 MHz machine.
- New host-side tests (`make test`, `tests/`): a cycle-counting 6502
  emulator runs the compiled renderer, patcher and probe from both
  PRGs against a model of the U64's turbo CPU timing. The emulator
  models the dummy read of an indexed store, which costs a wait state
  when it reads a VIC register. The model reproduces the
  hardware-bisected render delay at 64 MHz (`$87` fits, `$A5` skews)
  and the pixel positions measured at 48 MHz. The tests also check the
  committed end-to-end golden images for consistency.
- New end-to-end test on real hardware (`make e2e`, `tests/e2e/`): runs
  the release PRG on one or more Ultimate devices, drives it with key
  presses over the REST API, captures the picture from the VIC video
  stream and compares it with golden images for each display path.
  With one 48 MHz and one 64 MHz device it also checks that both show
  the same picture.
- `turbo.h`: corrected the `TURBO_SPEED_*` names for indexes 6-13 to
  the firmware's actual speed table, and documented that the table
  differs on an original Ultimate 64 / Elite I (index 14 = 40 MHz,
  index 15 = 48 MHz there).
- `mandelbrot_generate()`'s symmetry check uses an unsigned 16-bit
  shift instead of a 32-bit division, which was the only use of
  Oscar64's 32-bit division runtime. Without this change the 48 MHz
  code does not fit the default `main` code region at `stacksize` 80.
  The change frees about 400 bytes there: `stacksize` stays at 80 and
  the region has 323 bytes free.
- `make deploy2` deploys to an optional second Ultimate device
  (`ULTIP2` in `.env`, storage port `ULTUSB2`, defaulting to `ULTUSB`).
  The deploy reachability checks now probe the device's FTP root, so a
  device without the install directory yet is no longer reported as
  unreachable.
- `make e2e` recognises the product name `Ultimate 64-II`, which
  firmware 3.15a reports for an Ultimate 64 Elite II.

## [1.0.3]

Rebalances the escape-time color mapping again and repositions white
in every gradient, following up on v1.0.2's own fix.

- Extended every gradient from 14 to 15 usable colors: `mandel_color()`
  now also emits white (previously reserved exclusively for the zoom
  feature's corner markers), matching DDT/0x444454's own mandelbr8
  approach of using every color including white for the gradient
  itself. `mandel_color_table[]` now spreads 32 escaping iteration
  counts across 15 colors instead of 14, needing only two 3-count
  merge bins instead of four.
- Moved white from an endpoint (bordering true black at every fractal
  boundary) to a genuine mid-gradient peak, at the same index (8) in
  all four palettes -- fire and amethyst become symmetric mirrors of
  their own ascending arc around it, sunset keeps its original two-
  different-arcs shape, and rainbow's original "white cap" becomes a
  mid-cycle flash instead. `ZOOM_MARKER_COLOR_INDEX` (`zoom.h`) is now
  `8`, a single constant shared by every palette.
- Known, accepted tradeoff: a corner marker can now land on a picture
  pixel of its own exact color (white) and be hard to spot there. An
  outlined marker would close that gap but there isn't shared-pool
  memory to bring one back right now (18 bytes free) -- see
  `docs/ZOOM_FEATURE.md` and `docs/MANDELBROT_ALGORITHM.md`'s
  "Palettes" section for the full writeup.
- Confirmed on real hardware: the previously-merged shade improvement
  from v1.0.2 remains, and white now reads as a highlight a little
  in from each fractal boundary rather than a stark white/black edge.

## [1.0.2]

Fixes a genuinely missing color shade, reported after v1.0.1 shipped.

- Fixed `mandel_color()`'s iteration-count-to-palette-index mapping:
  the old formula (`1 + iter*14/32`) merged escaping iteration counts
  0-2 into a single shade -- the only one of 14 shades covering three
  counts instead of two, and the most common/visible one, since low
  counts dominate any view's exterior background. Replaced with a
  fixed lookup table (`mandel_color_table[]`) that gives counts 0 and 1
  each their own shade. This also explains why the v1.0.1 noise-
  speckle bug showed up as isolated "islands" rather than pixels at a
  visible color boundary -- see `docs/MANDELBROT_ALGORITHM.md`'s
  "Palettes" section and `CREDITS.md`.
- Confirmed on real hardware: the previously-merged shade is now
  visibly distinct in all 4 palettes.

## [1.0.1]

Real-hardware bug-fix pass, plus a palette rework.

- Fixed a fixed-point overflow causing noise speckles in the generated
  fractal (`fixed_sqr`/`fixed_mul` in `include/mandelbrot.c`). See
  `CREDITS.md`.
- Fixed two zoom-box off-by-one bugs (`include/zoom.c`): a
  self-contradictory clamp at the largest box size, and the right/
  bottom corner markers landing one cell past the box's true edge.
- Fixed the picture's horizontal alignment (`render_frame()`'s timing
  pad in `include/upic_viewer.c`) so the box mode's left-edge corner
  markers no longer fall partly off-screen.
- Reworked all 4 color gradients for distinctness: every gradient's
  adjacent steps (including the black/white boundary steps) are now
  comfortably separated, and no two gradients read as near-identical
  at the same escape-iteration band. The former `ice` gradient was
  replaced by `amethyst` (black -> deep violet -> vivid magenta -> hot
  pink -> pale pink). See `docs/MANDELBROT_ALGORITHM.md`'s "Palettes"
  section for the full reasoning.
- Confirmed working on firmware 3.15a in addition to 3.15.

## [1.0.0]

Initial release.

- Fixed-point (Q5.11) Mandelbrot escape-time generator, with a
  quarter-square multiply table, cardioid/period-2-bulb early-skip,
  and real-axis mirror symmetry for views that support it. See
  `docs/MANDELBROT_ALGORITHM.md`.
- Live picture build-up: the fractal displays column by column as it
  generates, not just once complete.
- Upic border-color raster display (384x256, 16 colors). See
  `docs/UPIC_VIEWER.md`.
- Interactive pan and zoom: browse mode (WASD/cursor panning, gradual
  zoom-out) and box mode (move/resize a selection box, confirm to zoom
  in), with corner markers drawn directly into the picture buffer. See
  `docs/ZOOM_FEATURE.md`.
- 4 selectable color gradients (default blue/orange, fire, ice,
  rainbow), cycled live without leaving the current view.
- Ultimate 64 Elite 2 configuration file
  (`config/MandelbrotUpic-U64E2.cfg`), deployed/zipped as
  `mandelupic.cfg` alongside `mandelupic.prg` so the Ultimate's own
  firmware auto-loads it, enabling the Command Interface and U64 turbo
  registers this demo needs.
