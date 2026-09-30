"""纯裁决逻辑单元测试（仅依赖标准库）：

    python -m unittest discover -s tests -v
"""

import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from app.interference import (
    band_gap,
    bands_overlap,
    circles_overlap,
    evaluate,
    required_guard_band,
    time_overlaps,
)

T1 = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
T3 = datetime(2026, 10, 5, 11, tzinfo=timezone.utc)
T4 = datetime(2026, 10, 5, 13, tzinfo=timezone.utc)
T5 = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)


@dataclass
class App:
    center_x: float
    center_y: float
    radius: float
    freq_low_mhz: float
    freq_high_mhz: float
    guard_band_mhz: float
    start_utc: datetime
    end_utc: datetime


def mk(x, y, r, lo, hi, s, e, guard=0.25):
    return App(x, y, r, lo, hi, guard, s, e)


A = mk(0, 0, 1000, 450.0, 452.0, T1, T2)


class TestIntervals(unittest.TestCase):
    def test_half_open_time(self):
        self.assertTrue(time_overlaps(T1, T2, T3, T4))     # [10,12) ∩ [11,13)
        self.assertFalse(time_overlaps(T1, T2, T2, T4))    # 端点相接不算
        self.assertFalse(time_overlaps(T1, T2, T4, T5))    # 完全分离

    def test_closed_bands(self):
        self.assertTrue(bands_overlap(450, 452, 451, 453))
        self.assertTrue(bands_overlap(450, 452, 452, 453))  # 端点相接算相交
        self.assertFalse(bands_overlap(450, 452, 453, 454))

    def test_band_gap(self):
        self.assertIsNone(band_gap(450, 452, 452, 453))
        self.assertAlmostEqual(band_gap(450, 452, 453, 454), 1.0)

    def test_circles_strict(self):
        self.assertTrue(circles_overlap(0, 0, 1000, 500, 0, 600))
        self.assertFalse(circles_overlap(0, 0, 1000, 1600, 0, 600))  # 外切
        self.assertFalse(circles_overlap(0, 0, 1000, 5000, 0, 100))  # 远离


class TestEvaluate(unittest.TestCase):
    def test_full_overlap_band_overlap(self):
        t = evaluate(A, mk(500, 0, 600, 451.0, 453.0, T3, T4))
        self.assertIsNotNone(t)
        self.assertEqual(t.reason, "band_overlap")
        self.assertTrue(t.frequency_overlap)
        self.assertIsNone(t.band_gap_mhz)

    def test_guard_band_gap_smaller(self):
        t = evaluate(A, mk(0, 0, 1000, 452.20, 453.0, T1, T2))
        self.assertIsNotNone(t)
        self.assertEqual(t.reason, "guard_band")
        self.assertFalse(t.frequency_overlap)
        self.assertAlmostEqual(t.band_gap_mhz, 0.20, places=6)
        self.assertEqual(t.required_guard_band_mhz, 0.25)

    def test_gap_equal_to_guard_is_safe(self):
        # 严格小于才冲突：gap == 0.25 不构成干扰
        self.assertIsNone(evaluate(A, mk(0, 0, 1000, 452.25, 453.0, T1, T2)))

    def test_tangent_bands_overlap_closed(self):
        t = evaluate(A, mk(0, 0, 1000, 452.0, 453.0, T1, T2))
        self.assertIsNotNone(t)
        self.assertEqual(t.reason, "band_overlap")

    def test_area_required_for_frequency_conflict(self):
        # 频段相交但空间远离
        self.assertIsNone(evaluate(A, mk(5000, 0, 100, 451.5, 452.5, T3, T4)))

    def test_time_required_for_frequency_conflict(self):
        # 频段相交但时段不相交（端点相接）
        self.assertIsNone(evaluate(A, mk(0, 0, 1000, 451.0, 453.0, T2, T4)))

    def test_tangent_circles_no_conflict(self):
        # 圆心距 == 半径和（外切）
        self.assertIsNone(evaluate(A, mk(1600, 0, 600, 451.0, 453.0, T3, T4)))

    def test_required_guard_band_is_max(self):
        big = mk(0, 0, 1, 1, 2, T1, T2, guard=0.9)
        small = mk(0, 0, 1, 3, 4, T1, T2, guard=0.2)
        self.assertEqual(required_guard_band(big, small), 0.9)


if __name__ == "__main__":
    unittest.main()
