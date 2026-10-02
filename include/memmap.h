/*****************************************************************
Mandelbrot Upic -- memory map (sections and regions)

Included first by src/main.c, before any library header: the Ultimate
libraries (lib/ultimate-uci-oscar64) are separate translation units that
place code and data in this project's sections through compiler defines
(-dUII_UPIC_GEN=upicgen etc., see the Makefile), so the sections must
exist before those files are compiled.

  $0200-$07FF  ovl1      bss only: UCI buffers, cy2_table (bssovl1)
  $0853-$17FF  main      default code/data/bss/stack
  $1800-$C7FF  upicbuf   picture byte columns 8-183 (reserved, no data)
  $C800-$CFFF  initcode  code that runs once at startup; afterwards the
                         same RAM is picture columns 184-191
  $E000-$E7FF  picreloc  picture byte columns 0-7 (reserved)
  $E800-$FFFF  upiccode  zoom code, sq_table, cy2-free generator data,
                         and the generated Upic line renderer (upicgen)

Column c of the picture is at $1000 + c * 256, except columns 0-7 at
$E000 + c * 256 (-dUII_UPIC_RELOC_COLS=8 -dUII_UPIC_RELOC_BASE=0xE000):
"main" occupies $0853-$17FF, where Upic's standard layout would put
columns 0-7. See docs/ARCHITECTURE.md for the memory budget.
******************************************************************/

#ifndef _MEMMAP_H_
#define _MEMMAP_H_

// Default program region. No heap: nothing reachable in this program
// calls malloc() (the library's file functions use a static command
// buffer, placed in bssovl1 below). Oscar64 computes the worst-case stack
// depth statically and errors if stacksize is too small; the minimum
// measured on 2026-09-21 was 68, 80 keeps a margin.
#pragma heapsize(0)
#pragma stacksize(80)
#pragma region(main, 0x0853, 0x1800, , , {code, data, bss, heap, stack})

// Picture columns 8-183. Reserved address space only: the picture is
// computed on the device, so nothing is stored in the file here.
#pragma section(upicbuf, 0)
#pragma region(upicbuf, 0x1800, 0xc800, , , {upicbuf})

// Startup-only code, in the last 2 KB of the picture (columns 184-191):
// the Upic renderer generator and speed probe (UII_UPIC_INIT), the turbo
// module (UII_TURBO_CODE) and this project's own startup functions. It
// all runs before the first picture is computed; mandelbrot_generate()
// then overwrites this RAM with the picture's last columns, so none of it
// may be called after startup. main() clears these columns once startup
// is done, so the first live frames don't show the code as pixels.
#pragma section(initcode, 0)
#pragma section(initdata, 0)
#pragma region(initcode, 0xc800, 0xd000, , , {initcode, initdata})

// Picture columns 0-7, under the KERNAL ROM (banked out for the whole
// program, see rombank.h).
#pragma section(picreloc, 0)
#pragma region(picreloc, 0xe000, 0xe800, , , {picreloc})

// $E800-$FFFF, under the KERNAL ROM: zoom code, mandelbrot.c's squaring
// table, and the generated Upic line renderer with its nybble table
// (upicgen, bss, 2440 + 256 bytes, page-aligned). This pool is the
// tightest budget in the program -- check the .map after any change here:
// Oscar64 can wrap an object past $10000 back to near $0000 instead of
// reporting a placement error.
#pragma section(upiccode, 0)
#pragma section(modcode, 0)
#pragma section(moddata, 0)
#pragma section(modbss, 0)
#pragma section(upicgen, 0)
#pragma region(upiccode, 0xe800, 0x10000, , , {upicgen, upiccode, modcode, moddata, modbss})

// $0200-$07FF, uninitialised data only: the library's UCI buffers
// (-dUII_COMMON_BSS=bssovl1; the 520-byte command buffer the file
// functions use) and mandelbrot.c's cy2_table. Declared as an Oscar64
// overlay region (index 1) on purpose: a plain #pragma region below $0801
// moved the .prg's load address to $0002 (found 2026-09-09); an overlay
// region doesn't. Never put code or initialised data here, and check
// `xxd -l2 build/mandelupic.prg` (must start 01 08) after changing it.
// Oscar64 also writes an (empty) build/ovl_nybbles.prg for it; unused.
#pragma section(codeovl1, 0)
#pragma section(dataovl1, 0)
#pragma section(bssovl1, 0)
#pragma region(ovl1, 0x0200, 0x0800, , 1, { codeovl1, dataovl1, bssovl1 })
#pragma overlay(ovl_nybbles, 1)

#endif
