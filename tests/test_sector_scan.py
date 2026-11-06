import math
import unittest

from tools.sector_scan import sector_setup


def oscillation(amplitude=0.7):
    prices = [100 + amplitude * math.sin(i * math.tau / 48) for i in range(289)]
    return [dict(close=p, high=p + 0.02, low=p - 0.02) for p in prices]


class SectorScanTests(unittest.TestCase):
    def test_repeated_bounded_crossings_qualify(self):
        self.assertEqual(sector_setup(oscillation())["sector_status"], "candidate")

    def test_wide_oscillations_and_single_wick_rejected(self):
        self.assertIn("swing too wide", sector_setup(oscillation(3))["rejection_reasons"])
        bars = oscillation()
        bars[-1]["high"] += 5
        self.assertIn("large excursion outside box", sector_setup(bars)["rejection_reasons"])

    def test_one_active_half_and_uneconomic_box_rejected(self):
        bars = oscillation()
        bars[:144] = [dict(close=100, low=100, high=100)] * 144
        self.assertIn("too few sustained crossings", sector_setup(bars)["rejection_reasons"])
        self.assertIn("too narrow after costs", sector_setup(oscillation(0.1))["rejection_reasons"])
