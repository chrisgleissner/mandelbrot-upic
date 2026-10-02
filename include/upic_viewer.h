/*****************************************************************
Mandelbrot Upic -- Upic picture display

A thin layer over ultimate_upic_lib (ultimate-uci-oscar64 1.3.0, see
lib/ultimate-uci-oscar64/docs/UPIC_MANUAL.md), which does the actual
display: Aleksi Eeben's Upic border-color raster technique, with an
exact one-dot pixel pitch at 64 MHz (Elite II / C64 Ultimate) and on
Christian Gleissner's 48 MHz path (Ultimate 64 / Elite I, 3 of every 4
pixels). Until v1.1.1 this project had its own copy of the renderer and
the 48 MHz patcher; see docs/UPIC_VIEWER.md for that history.

Picture layout: byte column c (pixels 2c and 2c+1) at $1000 + c * 256,
except columns 0-7 at $E000 + c * 256 (memmap.h). Use upic_column() /
uii_upic_column() for addresses.
******************************************************************/

#ifndef _UPIC_VIEWER_H_
#define _UPIC_VIEWER_H_

#include "ultimate_upic_lib.h"

#define UPIC_WIDTH  384
#define UPIC_HEIGHT 256
#define UPIC_BYTES  49152

// Byte column address (0..191). uii_upic_column() is __noinline in the
// library: see its comment for the Oscar64 -O2 trap that requires it.
#define upic_column(c) uii_upic_column(c)

// Show one frame of the picture (interrupts must be masked; they are, for
// the whole program, see main.c), then poll the keyboard matrix. Returns
// nonzero when SPACE is down.
char upic_show_frame(void);

// Startup only (initcode, see memmap.h): measure the CPU's top speed and
// build the matching Upic renderer. Sets upic_frame_quarters.
void upic_select_display_path(void);

// Live frames during generation: one per 4/upic_frame_quarters byte
// columns of work, so both speeds show the picture in about the same
// share of frames. 4 at 64 MHz, 3 at 48 MHz (a column takes 4/3 as long).
extern unsigned char upic_frame_quarters;

#pragma compile("upic_viewer.c")

#endif
