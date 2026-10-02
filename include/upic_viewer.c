/*****************************************************************
Mandelbrot Upic -- Upic picture display (implementation)

The display itself is ultimate_upic_lib (ultimate-uci-oscar64 1.3.0):
Aleksi Eeben's Upic border-color raster technique
(https://csdb.dk/release/?id=263889), with the exact one-dot pixel pitch
of his Upic v1.3 line renderer (every 8th pixel pair from an immediate
operand patched before each line) and Christian Gleissner's 48 MHz path
(mandelbrot-upic PR #2), given the same exact pitch in the library.

History: v1.0.0-v1.1.1 had this project's own port of Upic v1.1 here,
without the per-line patch step, so a pixel was 64/63 of a dot wide at
64 MHz and pixels 370-383 fell beyond the right edge; v1.1.0 added
Christian's 48 MHz patcher to that code. See docs/UPIC_VIEWER.md.
******************************************************************/

#include <c64/keyboard.h>
#include "rombank.h"
#include "ultimate_turbo_lib.h"
#include "upic_viewer.h"

unsigned char upic_frame_quarters = 4;

#ifndef UPIC_FORCE_48MHZ
// Probe result: 0 = 48 MHz, 1 = 64 MHz, 2 = no result (the probe gave
// up after 256 loops; the 64 MHz renderer is used then). volatile: only
// written here, so Oscar64 would otherwise drop the variable -- but
// tests/ and tests/e2e read it from memory.
static volatile unsigned char upic_probe_class = 2;
#endif

// ---------------------------------------------------------------
// Startup: choose the display path (initcode, see memmap.h)
// ---------------------------------------------------------------
#pragma code(initcode)
#pragma data(initdata)

void upic_select_display_path(void)
{
#ifdef UPIC_FORCE_48MHZ
	// Test-only build (`make force48`): always the 48 MHz path, at speed
	// index 14 -- 48 MHz on an Elite II / C64 Ultimate -- so the path can
	// be checked on a 64 MHz machine.
	uii_upic_turbo = TURBO_SPEED_48MHZ | TURBO_BADLINES_OFF;
	uii_upic_init(UII_UPIC_48MHZ);
	upic_frame_quarters = 3;
#else
	// uii_upic_init(UII_UPIC_AUTO) runs uii_turbo_probe_max(): it times a
	// loop against the raster counter, rejects loops run in the forced
	// 1 MHz window after a reset, needs two agreeing loops, and gives up
	// after 256 (about 20 s at 1 MHz, e.g. started without the .cfg).
	char path = uii_upic_init(UII_UPIC_AUTO);
	upic_probe_class = path == UII_UPIC_48MHZ ? 0
	                 : path == UII_UPIC_64MHZ ? 1
	                 : 2;
	if (path == UII_UPIC_48MHZ)
		upic_frame_quarters = 3;
#endif
}

#pragma code(code)
#pragma data(data)

// ---------------------------------------------------------------
// One frame
// ---------------------------------------------------------------

char upic_show_frame(void)
{
	// ROM banking is set up once for the whole program (rombank.h); the
	// call is idempotent. Columns 0-7 and the generated renderer are
	// under the KERNAL ROM area.
	rombank_out();
	uii_upic_show_frame();
	keyb_poll();
	return key_pressed(KSCAN_SPACE);
}
