"""Consistency checks on the end-to-end golden images (no hardware needed).

tests/e2e/run_e2e.py captures the goldens from real devices; these tests
keep the committed set consistent whenever it is regenerated:

- Every golden exists for both display paths (48 MHz and 64 MHz), is a
  384 x 272 palette image, and uses only colour indexes 0-15.
- Each 48 MHz golden shows exactly the pixels the 64 MHz golden shows,
  through the dot maps measured from the "pattern" goldens (the same
  check run_e2e.py makes when a device of each kind takes part).
- The pattern goldens show the pixels each path is meant to show, in
  order: all of them at 64 MHz, 4m, 4m+2 and 4m+3 at 48 MHz, and pixel
  0 on the same dot on both.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'e2e'))

import geometry  # noqa: E402
import png  # noqa: E402
from run_e2e import STEPS  # noqa: E402

GOLDEN = os.path.join(HERE, 'e2e', 'golden')
CAPTURES = [step[1] for step in STEPS if step[0] == 'capture']
MODES = ('48mhz', '64mhz')


def golden(mode, name):
    return png.read(os.path.join(GOLDEN, mode, name + '.png'))[0]


class Goldens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.maps = {m: geometry.pixel_map(golden(m, 'pattern')[120], m) for m in MODES}

    def test_every_capture_has_a_golden_for_both_paths(self):
        for mode in MODES:
            for name in CAPTURES:
                rows = golden(mode, name)
                self.assertEqual((len(rows), len(rows[0])), (272, 384), (mode, name))
                self.assertLessEqual(max(max(r) for r in rows), 15, (mode, name))

    def test_48mhz_goldens_match_the_64mhz_ones(self):
        for name in CAPTURES:
            bad = geometry.compare_48_to_64(golden('48mhz', name), golden('64mhz', name),
                                            self.maps['48mhz'], self.maps['64mhz'])
            self.assertEqual(bad[:5], [], name)

    def test_cross_check_can_fail(self):
        # Negative control: two different pictures must not pass.
        bad = geometry.compare_48_to_64(golden('48mhz', 'overview'), golden('64mhz', 'zoom-2x'),
                                        self.maps['48mhz'], self.maps['64mhz'])
        self.assertGreater(len(bad), 1000)

    def test_pattern_geometry(self):
        # Exact pitch (ultimate_upic_lib, since v1.2.0). 64 MHz: dot x shows
        # pixel x, all 384. 48 MHz: pixels 4m, 4m+2, 4m+3 (4m+1 left out); a
        # group is 24 cycles = 4.085 dots, so inside a 16-pixel span a pixel
        # can start one dot off its ideal dot (4m, 4m+2, 4m+3), while every
        # 16th pixel is exact. Both start on dot 0 and fill all 384 dots.
        self.assertEqual(self.maps['64mhz'], geometry.expected_map('64mhz'))
        m48 = self.maps['48mhz']
        first = {}
        for x, p in enumerate(m48):
            if p is not None and p not in first:
                first[p] = x
        self.assertEqual(sorted(first), geometry.shown_pixels('48mhz'))
        for p, x in first.items():
            self.assertLessEqual(abs(x - p), 1, 'pixel %d on dot %d' % (p, x))
            if p % 16 == 0:
                self.assertEqual(x, p, 'pixel %d' % p)
        self.assertIsNotNone(m48[0])
        self.assertIsNotNone(m48[-1])


if __name__ == '__main__':
    unittest.main()
