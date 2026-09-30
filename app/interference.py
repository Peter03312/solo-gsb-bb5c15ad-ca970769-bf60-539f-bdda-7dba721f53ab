"""纯几何/区间裁决逻辑，不依赖数据库，便于理解与测试。

约定：
- 时段为 UTC 左闭右开区间 [start, end)，相交条件 start1 < end2 and start2 < end1；
- 覆盖圆在平面坐标中相交：圆心距 < r1 + r2（外切不算）；
- 频段为闭区间 [low, high]：相交条件 low1 <= high2 and low2 <= high1；
- 频段不相交时，最短间隔（gap）小于“要求的保护带宽”视为冲突，
  比较用的保护带宽取两申请各自值的较大者；比较为严格小于，
  即 gap == guard_band 时不构成冲突。
"""

from dataclasses import dataclass
import math
from typing import Protocol


class HasRegion(Protocol):
    center_x: float
    center_y: float
    radius: float
    freq_low_mhz: float
    freq_high_mhz: float
    guard_band_mhz: float
    start_utc: object
    end_utc: object


def time_overlaps(a_start, a_end, b_start, b_end) -> bool:
    """左闭右开时段是否相交（端点相接不算）。"""
    return a_start < b_end and b_start < a_end


def circles_overlap(x1, y1, r1, x2, y2, r2) -> bool:
    """两覆盖圆是否有重叠区域（外切不算）。"""
    return math.hypot(x1 - x2, y1 - y2) < (r1 + r2)


def bands_overlap(lo1, hi1, lo2, hi2) -> bool:
    """两个闭频段是否相交（端点相接算相交）。"""
    return lo1 <= hi2 and lo2 <= hi1


def band_gap(lo1, hi1, lo2, hi2) -> float | None:
    """频段不相交时的最短间隔；相交返回 None。"""
    if bands_overlap(lo1, hi1, lo2, hi2):
        return None
    return max(lo1 - hi2, lo2 - hi1)


def required_guard_band(a: HasRegion, b: HasRegion) -> float:
    """两申请之间生效的保护带宽，取各自要求的较大值（均非负）。"""
    return max(a.guard_band_mhz, b.guard_band_mhz)


@dataclass(frozen=True)
class Trigger:
    """一次冲突的触发条件明细。"""

    time_overlap: bool
    area_overlap: bool
    frequency_overlap: bool
    band_gap_mhz: float | None        # 频段不相交时的间隔，否则 None
    required_guard_band_mhz: float    # 本次比较采用的保护带宽
    reason: str                       # "band_overlap" 或 "guard_band"


def evaluate(a: HasRegion, b: HasRegion) -> Trigger | None:
    """若 a、b 构成干扰则返回触发条件，否则返回 None。

    前提：时段相交且覆盖区域相交；频段相交或最短间隔 < 保护带宽。
    """
    if not time_overlaps(a.start_utc, a.end_utc, b.start_utc, b.end_utc):
        return None
    if not circles_overlap(
        a.center_x, a.center_y, a.radius, b.center_x, b.center_y, b.radius
    ):
        return None

    required = required_guard_band(a, b)
    gap = band_gap(a.freq_low_mhz, a.freq_high_mhz, b.freq_low_mhz, b.freq_high_mhz)
    if gap is None:
        return Trigger(
            time_overlap=True,
            area_overlap=True,
            frequency_overlap=True,
            band_gap_mhz=None,
            required_guard_band_mhz=required,
            reason="band_overlap",
        )
    if gap < required:
        return Trigger(
            time_overlap=True,
            area_overlap=True,
            frequency_overlap=False,
            band_gap_mhz=gap,
            required_guard_band_mhz=required,
            reason="guard_band",
        )
    return None
