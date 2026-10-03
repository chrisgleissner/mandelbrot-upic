"""Tests for the Upic display paths (48 MHz / 64 MHz) and the speed probe.

These run the compiled code from build/mandelupic.prg (release) and
build/mandelupic-force48.prg (test-only, see Makefile) in a
cycle-counting 6502 emulator attached to a model of the Ultimate 64's
CPU timing (tests/machine.py). Build both first: `make test` does that.

Since v1.2.0 the display is ultimate_upic_lib (lib/ultimate-uci-oscar64):
upic_select_display_path() calls uii_upic_init(UII_UPIC_AUTO), which
measures the top speed with uii_turbo_probe_max() and generates the line
renderer into uii_upic_code[]; uii_upic_frame_asm draws a frame with it.

What is checked:
- On an Elite II / C64U model the probe picks the 64 MHz path, and every
  one of the 384 pixels of a row is exactly one dot wide (pixel k on dot
  first + k) -- the exact pitch of Aleksi Eeben's Upic v1.3 renderer.
- On an Ultimate 64 / Elite I model it picks the 48 MHz path: pixels 4m,
  4m+2 and 4m+3 of all 96 groups, on dots 4m (two dots wide), 4m+2 and
  4m+3, starting on the same dot as the 64 MHz path. A group takes 24
  cycles = 4.085 dots, so inside a 16-pixel span a pixel can land one dot
  off; every 16 pixels (94 cycles, one patched group) it is exact.
  Measured on hardware (2026-10-02, VIC video stream): both paths put
  pixel 0 on the first visible dot and every color-bar edge (multiples
  of 24 pixels) on the same dot.
- The force48 build shows the 48 MHz pixel order on an Elite II at speed
  index 14 (its cycles are spread differently there, so the pitch is not
  the real 48 MHz one), and does not link the probe.
- Every row stays inside one raster line, rows land on consecutive
  lines, and the next line's $D012 poll finishes in time.
- The probe gets the right answer when the post-reset forced 1 MHz
  window ends at various points, when the speed register reads 1 MHz at
  the start, and gives up (64 MHz renderer, class 2) on a machine that
  never leaves 1 MHz.
- upic_frame_quarters is 3 on the 48 MHz path, 4 on the 64 MHz path.
- Negative control: the 64 MHz renderer run at 48 MHz overruns its
  lines, so the row checks can fail.
"""

import unittest

from machine import (DOTS_PER_LINE, ELITE2, U64, Machine, load_build)

COLUMNS = 192
ROWS = 256
RELOC_COLUMNS = 8          # -dUII_UPIC_RELOC_COLS in the Makefile


def column_page(c):
    """High byte of byte column c (memmap.h): 0-7 at $E000, rest at $1000 + c * 256."""
    return 0xE0 + c if c < RELOC_COLUMNS else 0x10 + c


def pixel_byte(c, y):
    return (c * 37 + y * 11 + 5) & 0xFF


class RowTimingError(AssertionError):
    pass


def find(mem, start, end, pattern):
    for a in range(start, end - len(pattern)):
        if list(mem[a:a + len(pattern)]) == pattern:
            return a
    raise AssertionError('pattern %s not found' % pattern)


def select(image, symbols, ratio, **kw):
    """Run upic_select_display_path() on a fresh machine; return it."""
    m = Machine(image, ratio, **kw)
    m.call(symbols['upic_select_display_path'][0], max_cycles=10**9)
    return m


def render(mem, symbols, ratio, window=None):
    """Fill the picture, draw one frame with the generated renderer on a
    fresh machine, return (machine, pixel writes, line reads). window =
    (first row, rows) sets the library's display window directly."""
    m = Machine(mem, ratio, d031=0x8F, start_line=250)
    if window:
        first, rows = window
        line = 0x18 + first
        m.mem[symbols['uii_upic_win_first'][0]] = first
        m.mem[symbols['uii_upic_win_end'][0]] = (first + rows) & 0xFF
        m.mem[symbols['uii_upic_win_lo'][0]] = line & 0xFF
        m.mem[symbols['uii_upic_win_hi'][0]] = line >> 8
    for c in range(COLUMNS):
        base = column_page(c) << 8
        for y in range(ROWS):
            m.mem[base + y] = pixel_byte(c, y)
    frame = symbols['uii_upic_frame_asm'][0]
    code_lo, code_hi = symbols['uii_upic_code']
    # `line: lda $d012 / lw: cmp $d012 / beq lw`: the per-line poll.
    line_pc = find(m.mem, frame, frame + 80, [0xAD, 0x12, 0xD0, 0xCD, 0x12, 0xD0, 0xF0])
    # The generated code ends with `lda #0 / sta $d020 / rts`: not a pixel.
    end_sta = find(m.mem, code_lo, code_hi, [0xA9, 0x00, 0x8D, 0x20, 0xD0, 0x60]) + 2
    m.call(frame)
    pixels = [(t, v) for t, v, pc in m.d020_writes
              if code_lo <= pc < code_hi and pc != end_sta]
    line_reads = [t for t, pc in m.d012_reads if pc == line_pc]
    return m, pixels, line_reads


def row_dots(m, pixels, per_row, rows_drawn=ROWS):
    """Dot (from the start of the raster line) of every pixel write, per
    row; checks that rows are on consecutive lines."""
    if len(pixels) != per_row * rows_drawn:
        raise RowTimingError('%d pixel writes, expected %d' % (len(pixels), per_row * rows_drawn))
    first_line = m.line_of(pixels[0][0])
    rows = []
    for k in range(rows_drawn):
        row = pixels[k * per_row:(k + 1) * per_row]
        line = m.line_of(row[0][0])
        if line != first_line + k:
            raise RowTimingError('row %d painted on line %d, expected %d' % (k, line, first_line + k))
        if m.line_of(row[-1][0]) != line:
            raise RowTimingError('row %d runs into the next raster line' % k)
        rows.append([m.dot_of(t) - line * DOTS_PER_LINE for t, _ in row])
    return rows


def check_slack(m, pixels, line_reads, per_row):
    """The next row's $D012 poll must finish before its line ends."""
    for k in range(ROWS - 1):
        line = m.line_of(pixels[k * per_row][0])
        if k + 1 < len(line_reads):
            if (line + 1) * m.ticks_per_line - 1 - line_reads[k + 1] < 0:
                raise RowTimingError('row %d: next $D012 poll is late' % k)


def expected_64(first):
    return [first + k for k in range(2 * COLUMNS)]


def expected_48(first):
    return [first + 4 * g + q for g in range(COLUMNS // 2) for q in (0, 2, 3)]


class ReleaseBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.symbols = load_build('mandelupic')
        cls.class_addr = cls.symbols['upic_probe_class'][0]
        cls.quarters = cls.symbols['upic_frame_quarters'][0]
        cls.m64 = select(cls.image, cls.symbols, ELITE2)
        cls.m48 = select(cls.image, cls.symbols, U64)
        cls.r64 = render(cls.m64.mem, cls.symbols, ELITE2)
        cls.r48 = render(cls.m48.mem, cls.symbols, U64)
        cls.dots64 = row_dots(cls.r64[0], cls.r64[1], 384)
        cls.dots48 = row_dots(cls.r48[0], cls.r48[1], 288)

    def test_elite2_gets_the_64mhz_path(self):
        self.assertEqual(self.m64.mem[self.class_addr], 1)
        self.assertEqual(self.m64.mem[self.quarters], 4)

    def test_u64_gets_the_48mhz_path(self):
        self.assertEqual(self.m48.mem[self.class_addr], 0)
        self.assertEqual(self.m48.mem[self.quarters], 3)

    def test_64mhz_exact_pitch(self):
        first = self.dots64[0][0]
        for k, row in enumerate(self.dots64):
            self.assertEqual(row, expected_64(first), 'row %d' % k)

    def test_48mhz_exact_pitch(self):
        first = self.dots48[0][0]
        want = expected_48(first)
        for k, row in enumerate(self.dots48):
            # Exact every 16 pixels (every 4th group = 12 writes)...
            self.assertEqual(row[::12], want[::12], 'row %d' % k)
            # ...and every pixel within one dot.
            off = max(abs(a - b) for a, b in zip(row, want))
            self.assertLessEqual(off, 1, 'row %d' % k)

    def test_paths_start_on_the_same_dot(self):
        # On hardware both put pixel 0 on the first visible dot.
        self.assertEqual(self.dots48[0][0], self.dots64[0][0])

    def test_lines_have_time_left(self):
        check_slack(*self.r64, 384)
        check_slack(*self.r48, 288)

    def test_window_shows_only_the_band(self):
        # Live view Bar (upic_viewer.c): rows 124-131 only, each on the
        # raster line it has in the full picture, same dots. Also a window
        # below raster line 255 (rows 240-255).
        for first, rows in ((124, 8), (240, 16)):
            for mem, ratio, per_row, full in ((self.m64.mem, ELITE2, 384, self.r64),
                                              (self.m48.mem, U64, 288, self.r48)):
                m, pixels, reads = render(mem, self.symbols, ratio, (first, rows))
                dots = row_dots(m, pixels, per_row, rows)
                full_dots = self.dots64 if per_row == 384 else self.dots48
                self.assertEqual(dots, full_dots[first:first + rows], (first, ratio))
                band_line = m.line_of(pixels[0][0])
                full_line = full[0].line_of(full[1][first * per_row][0])
                self.assertEqual(band_line, full_line, (first, ratio))
                self.assertEqual([v for _, v in pixels[:per_row]],
                                 [v for _, v in full[1][first * per_row:(first + 1) * per_row]])

    def test_64mhz_code_overruns_at_48mhz(self):
        # Negative control: the 64 MHz renderer is too slow for a 48 MHz
        # line, and the row checks must notice.
        with self.assertRaises(RowTimingError):
            m, pixels, reads = render(self.m64.mem, self.symbols, U64)
            row_dots(m, pixels, 384)

    def test_probe_survives_forced_1mhz_after_reset(self):
        # The U64 runs the CPU at 1 MHz for a few seconds after a reset; the
        # probe usually starts inside that window. End it at several points,
        # including in the middle of a probe loop (64764 phi2 plus up to two
        # frames of raster sync).
        loop_phi2 = 64764 + 2 * 63 * 312
        for ratio, cls in ((ELITE2, 1), (U64, 0)):
            for fraction in (0.0, 0.3, 0.6, 0.95, 1.4):
                end = int(fraction * loop_phi2) * 8 * ratio
                m = select(self.image, self.symbols, ratio, slow_until=end)
                self.assertEqual(m.mem[self.class_addr], cls,
                                 'ratio %d, window ends at %.2f' % (ratio, fraction))

    def test_probe_restores_a_lost_speed_setting(self):
        # $D031 reads 1 MHz when the probe starts (something reset it after
        # uii_turbo_fast()); the probe writes it again on every retry.
        for ratio, cls in ((ELITE2, 1), (U64, 0)):
            m = select(self.image, self.symbols, ratio, d031=0x80)
            self.assertEqual(m.mem[self.class_addr], cls, 'ratio %d' % ratio)

    def test_probe_gives_up_at_1mhz(self):
        # Turbo never comes on: after its 256 loops the probe gives up, the
        # class stays 2 and the 64 MHz renderer is built (4 quarters).
        # About 30 s: the library's retry counter can't be cut short.
        m = select(self.image, self.symbols, U64, slow_until=10**12)
        self.assertEqual(m.mem[self.class_addr], 2)
        self.assertEqual(m.mem[self.quarters], 4)


class Force48Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.symbols = load_build('mandelupic-force48')

    def test_probe_is_not_linked(self):
        self.assertNotIn('upic_probe_class', self.symbols)

    def test_48mhz_path_on_elite2(self):
        # Speed index 14 is 48 MHz on an Elite II: the 48 MHz renderer is
        # built, every row shows 288 pixels on its own raster line, in the
        # 4m / 4m+2 / 4m+3 order (checked through the pixel values).
        m = select(self.image, self.symbols, ELITE2)
        self.assertEqual(m.mem[self.symbols['uii_upic_turbo'][0]], 0x8E)
        mm, pixels, reads = render(m.mem, self.symbols, ELITE2)
        row_dots(mm, pixels, 288)
        values = [v for _, v in pixels[:288]]
        want = []
        for g in range(COLUMNS // 2):
            a, b = pixel_byte(2 * g, 0), pixel_byte(2 * g + 1, 0)
            want += [a, b, b >> 4]
        self.assertEqual(values, want)


if __name__ == '__main__':
    unittest.main()
