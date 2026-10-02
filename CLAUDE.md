# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Mandelbrot Upic** — an Ultimate 64 demo that generates a
Mandelbrot fractal on-device at 64 MHz turbo (48 MHz on an original
Ultimate 64 / Elite I, where the display shows 3 of every 4 pixels,
auto-detected at startup -- see `docs/UPIC_VIEWER.md`'s 48 MHz
section), packs it directly into Upic format (a 16-color, 384x256 border-color raster picture
technique), displays it live as it renders, and lets the user
interactively pan and zoom into any region of the result. Targets
**Ultimate firmware 3.15 or newer only** (no fallback path for older
firmware). Confirmed on an Ultimate 64 Elite II (64 MHz path) and an
Ultimate 64 Elite (48 MHz path), both firmware 3.15a; a C64 Ultimate on
firmware 1.2RC was tested by PR #2's author (see `README.md`'s known
open point about the `.cfg`'s turbo setting name there).

**Status**: v1.1.0, feature-complete. See `README.md` for controls and
installation, `docs/ARCHITECTURE.md` for the project layout,
`docs/MANDELBROT_ALGORITHM.md` for the fractal generator's design,
`docs/UPIC_VIEWER.md` for the display technique, and
`docs/ZOOM_FEATURE.md` for the interactive pan/zoom control scheme.
See `CREDITS.md` for full attribution.

## Toolchain (summary — see `docs/ARCHITECTURE.md` and the global
`~/.claude/CLAUDE.md`'s Oscar64 section for detail)

**Oscar64**, a C99/C++ cross-compiler targeting 6502/C64 — see
`docs/OSCAR64_MANUAL.md` (project copy of the canonical manual) before re-researching compiler
behavior.

- `make` / `make all` — compiles to `build/mandelupic.prg`,
  regenerates `README.pdf`, builds the release ZIP
- `make deploy` — FTP the compiled `.prg` to the Ultimate device set in
  `.env` (copy from `.env.example`, sets `ULTIP1`)
- `make docs` — regenerates `README.pdf` via pandoc
- `make test` — builds the release and `force48` PRGs and runs the
  host-side tests in `tests/` (Python 3 standard library only)
- `make force48` — test-only PRG that always uses the 48 MHz display
  path, for checking it on an Elite II / C64U
- `make e2e` / `make e2e-update` — end-to-end test on the real devices
  in `.env`'s `E2E_DEVICES`: compares (or rewrites) the golden images
  in `tests/e2e/golden/`. Resets the devices it runs on. See
  `tests/e2e/README.md`

## Firmware 3.15+ features this demo is built around

- **UCI cartridge-side auto-enable**: `uii_wait_for_uci()` in
  `include/ultimate_common_lib.c` sends the unlock sequence when the
  UCI isn't already mapped (sending it while the UCI is mapped caused
  a start-up hang -- see `docs/UCILIB_MANUAL.md`). On an Ultimate 64 Elite
  (firmware 3.15) the unlock did not bring the UCI up with "Command
  Interface" disabled, so the palette is not pushed there;
  `config/MandelbrotUpic-U64E2.cfg` enables the interface, alongside
  U64 turbo registers.
- **Palette control**: `uii_getpalette()`/`uii_setpalette()`/
  `uii_setpalettecolor()`/`uii_resetpalette()`, wrapping UCI control
  commands `$51`-`$54` (`GET_PALETTE`/`SET_PALETTE`/
  `SET_PALETTE_COLOR`/`RESET_PALETTE`) — this is how the generated
  fractal's palette gets pushed to real hardware colors.

Full protocol reference: `docs/UCILIB_MANUAL.md`. Since this demo requires
firmware 3.15+ unconditionally, there's no need to guard these calls
behind a version/capability check.

## Memory layout

The shared `upiccode`/`moddata`/`modbss` code/data/bss pool
(`$E800`-`$FFFF`) is this project's tightest memory budget -- see
`docs/ZOOM_FEATURE.md`'s memory-layout section before adding anything
there. Oscar64's linker can, in rare cases, silently wrap an object's
address past `$10000` back down near `$0000` instead of raising a
placement error. Always verify actual object placement via the
build's own `.map` file after changing anything in this pool -- a
clean build alone is not sufficient evidence of correct placement this
close to the boundary.

The default `main` region (`$0853`-`$1800`) has 323 bytes free in the
current build: BSS ends at `$166D` and the stack section starts at
`$17B0`, with `stacksize` 80 (the compiler's minimum is 68). It had 0
bytes free, with `stacksize` cut to 72, until `mandelbrot_generate()`'s
symmetry check stopped linking Oscar64's 32-bit division runtime
(about 310 bytes). A 32-bit division anywhere in the program links
that runtime back in. Check the `.map` after adding anything there; the linker
reports "Cannot place stack section" when it overflows.

Turbo CPU timing (sub-slots per phi2, the VIC's share, the 1 MHz
window after reset) is summarised in `docs/UPIC_VIEWER.md` and
implemented in `tests/machine.py`; read those before reasoning about
cycle budgets.

`render_frame()` is a named `__asm` block so `upic_select_display_path()` can
patch its `dly`/`trb` operands by label. `tests/test_turbo_modes.py`
asserts those operands' offsets and that its delay loop doesn't cross
a page, so keep those tests passing after touching it.

Interrupts are masked globally for the program's entire lifetime (see
`main.c`'s own comment) -- this program has no functional need for a
real interrupt, and this avoids a real class of bug where a same-tick
hardware interrupt chains into genuine KERNAL/JiffyDOS ROM code while
this program's own direct-CIA keyboard polling is active.

## Testing

No emulator automation exists for the whole program -- VICE specifically
doesn't emulate the Ultimate's own UCI/turbo hardware this project
depends on. Graphics- or control-affecting changes have to be
confirmed on real Ultimate hardware: by eye, or with `make e2e`
(`tests/e2e/`), which captures the picture from the VIC video stream
and compares it with golden images.

`make test` runs host-side unit tests (`tests/`): the compiled
renderer, 48 MHz patcher and speed probe run in a small cycle-counting
6502 emulator against a model of the U64's turbo CPU timing, and the
committed e2e goldens are checked for consistency. Run it after any
change to `upic_viewer.c`, `turbo.c` or region layout.

## Code conventions

This project uses generously detailed comments throughout, not the
terse default style -- explaining WHY a design choice was made (a
hardware constraint, a memory-budget fight, a non-obvious interaction)
is valued here, since this codebase pushes close to several real
hardware and toolchain limits where that reasoning matters for future
changes.

## License

GPLv3 (see `LICENSE`). Any code added to this repository should be
compatible with GPLv3 licensing. Note: the fixed-point algorithm
*design* this project implements is informed by 0x444454/mandelbr8
(CC BY 4.0, see `CREDITS.md`) but no code from that project is used —
this is a from-scratch C reimplementation, so no license conflict.
