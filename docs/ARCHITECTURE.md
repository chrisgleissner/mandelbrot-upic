# Architecture

## What this is

An Ultimate 64 demo that generates a Mandelbrot fractal
on-device at 64 MHz turbo (48 MHz on an original Ultimate 64 / Elite I,
detected at startup -- see `UPIC_VIEWER.md`), packs it directly into Upic format (a
16-color, 384x256 border-color raster picture technique), displays it
live as it renders, and lets the user interactively pan and zoom into
any region of the result. See `CREDITS.md` for the Upic technique's
own attribution.

## Program flow

1. Bank ROM out permanently (`rombank_out()`) and mask interrupts
   globally for the rest of the program's lifetime (see
   [Interrupts](#interrupts) below).
2. Detect the Ultimate Command Interface (UCI) and push the default
   color palette.
3. Enable turbo at speed index 15 (64 MHz on Elite II / C64U, 48 MHz
   on an original Ultimate 64 / Elite I). `upic_select_display_path()`
   measures which of the two it is and has the Upic library generate
   the matching line renderer: every pixel on its own dot at 64 MHz,
   3 of every 4 pixels (288 of 384 per line) on the same dots at 48 MHz
   (see `docs/UPIC_VIEWER.md`). If the measurement never succeeds
   (turbo stays off), it gives up after 256 probe loops (about 20 s at
   1 MHz) and keeps the 64 MHz renderer. Steps 2 and 3 are
   `program_startup()`, in the startup-only `initcode` region; `main()`
   then clears that region, which becomes picture columns 184-191.
4. Generate the fractal (`mandelbrot_generate()`), showing it live as
   it builds.
5. Loop forever: let the user browse/zoom/cycle the palette or save the
   picture (`zoom_select()`); regenerate at the newly selected view on
   a confirmed zoom or pan, push the new palette on `C`, write a `.upic`
   file on `F1` (`save_picture()`).

There is no exit -- see `docs/ZOOM_FEATURE.md` for why.

## Components

- **`src/main.c`** -- entry point; see [Program flow](#program-flow)
  above.
- **`include/mandelbrot.c`/`.h`** -- the fractal generator (fixed-point
  escape-time iteration, quarter-square multiply, cardioid/bulb early
  skip, selectable color gradients). See
  `docs/MANDELBROT_ALGORITHM.md`.
- **`include/upic_viewer.c`/`.h`** -- thin layer over the library's
  Upic display (path selection, one frame plus keyboard poll). See
  `docs/UPIC_VIEWER.md`.
- **`include/memmap.h`** -- every section and region (the picture's
  split layout, the startup-only region, the `$E800` pool, the `$0200`
  bss region), included first by `main.c`. See `docs/UPIC_VIEWER.md`'s
  memory table.
- **`include/zoom.c`/`.h`** -- interactive pan/zoom/palette-cycle
  control scheme, drawing corner markers directly into the picture
  buffer. See `docs/ZOOM_FEATURE.md`.
- **`include/rombank.c`/`.h`** -- permanent ROM-banking setup shared by
  every module that needs it.
- **`lib/ultimate-uci-oscar64/`** -- the Ultimate libraries, a git
  submodule of https://github.com/xahmol/ultimate-uci-oscar64 pinned to
  a release tag. This project uses `ultimate_common_lib` (UCI protocol:
  palette control, device detection), `ultimate_turbo_lib` (U64 CPU
  speed control and the 48/64 MHz speed probe), `ultimate_upic_lib`
  (the Upic display and `.upic` saving) and `ultimate_dos_lib` (file
  I/O); see the library's `docs/UCILIB_MANUAL.md`,
  `docs/TURBOCONTROL_MANUAL.md` and `docs/UPIC_MANUAL.md`.

## Interrupts

Interrupts are masked globally, once, immediately after `rombank_out()`
in `main()`, and never re-enabled for the rest of the program's
lifetime. This program has no functional need for a real interrupt --
no music, no raster-IRQ effects, every wait loop (including the
picture viewer's own cycle-exact raster sync) is plain busy-polled --
so this costs nothing functionally, and avoids a real class of bug
where a same-tick hardware interrupt chains (via `rombank.c`'s
`mmap_trampoline()`) into genuine KERNAL/JiffyDOS ROM code while this
program's own direct-CIA keyboard polling is active. NMI (the RESTORE
key) isn't maskable this way and remains chained through the
trampoline as a safety net, but isn't otherwise relied on.

## Memory layout

See `docs/UPIC_VIEWER.md`'s own memory-regions table for the full
picture-buffer/code-region split, and `docs/ZOOM_FEATURE.md`'s memory-
layout section for the shared `upiccode` code/data/bss pool's current
budget -- the tightest constraint in this codebase, requiring careful,
verified-not-assumed object placement near its `$10000` boundary.

## Testing

No emulator automation exists for the whole program -- VICE doesn't
emulate the Ultimate's own UCI/turbo hardware this project depends on.
Changes affecting the display or controls have to be confirmed on real
Ultimate hardware.

`make test` runs host-side tests (`tests/`, see `tests/README.md`) that
execute the compiled renderer, the 48 MHz patcher and the speed probe
in a cycle-counting 6502 emulator attached to a model of the U64's
turbo CPU timing.
They check instruction timing and patch contents, not the picture on
a real screen. They also check that the committed end-to-end golden
images are consistent with each other.

`make e2e` runs the end-to-end test in `tests/e2e/` (see
`tests/e2e/README.md`) on real devices: it starts the release PRG
over the Ultimate's REST API, drives it with key presses, captures
the picture from the VIC video stream and compares it with golden
images for the device's display path. With one 48 MHz and one 64 MHz
device it also checks that both show the same picture.
