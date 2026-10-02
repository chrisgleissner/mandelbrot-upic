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
        m48, m64 = self.maps['48mhz'], self.maps['64mhz']
        # pixel_map() already checked the order of the shown pixels; here
        # the 48 MHz picture starts one dot left of the 64 MHz one (its
        # delay centres the position error, see upic_viewer.c) and both
        # fill the visible width.
        self.assertEqual(m48.index(0), m64.index(0) - 1)
        self.assertIsNotNone(m48[-1])
        self.assertIsNotNone(m64[-1])
        self.assertNotIn(1, m48)      # pixel 4m+1 is the one left out
        self.assertIn(1, m64)


if __name__ == '__main__':
    unittest.main()
