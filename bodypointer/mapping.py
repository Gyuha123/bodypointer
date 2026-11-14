"""추적 좌표 → 화면 좌표 변환 (FR-21 절대 모드, FR-22 상대 모드, FR-24 감도/가속, FR-40 캘리브레이션)."""
from dataclasses import dataclass
import math

import numpy as np

from .filters import PointFilter


@dataclass
class Calibration:
    """선택 부위의 가동 범위(정규화 좌표, 거울 모드 기준)."""
    min_x: float = 0.3
    max_x: float = 0.7
    min_y: float = 0.3
    max_y: float = 0.7

    MIN_SPAN = 0.015

    @classmethod
    def from_samples(cls, xs, ys, margin: float = 0.06) -> "Calibration":
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        lo_x, hi_x = np.percentile(xs, [3, 97])
        lo_y, hi_y = np.percentile(ys, [3, 97])
        cx, cy = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
        # 끝까지 닿기 힘든 사용자를 위해 측정 범위보다 약간 좁게 잡는다
        sx = max(hi_x - lo_x, cls.MIN_SPAN) * (1 - margin)
        sy = max(hi_y - lo_y, cls.MIN_SPAN) * (1 - margin)
        return cls(cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2)

    @property
    def span(self):
        return self.max_x - self.min_x, self.max_y - self.min_y

    def to_list(self):
        return [self.min_x, self.max_x, self.min_y, self.max_y]

    @classmethod
    def from_list(cls, v):
        return cls(*v) if v and len(v) == 4 else cls()


class PointerMapper:
    def __init__(self, screen_w: int, screen_h: int):
        self.w, self.h = screen_w, screen_h
        self.calib = Calibration()
        self.mode = "absolute"      # "absolute" | "relative"
        self.sensitivity = 1.0      # 절대 모드: >1이면 작은 움직임으로 화면 전체 이동
        self.gain = 1.0             # 상대 모드 이동 배율
        self.accel = 0.5            # 상대 모드 가속 곡선 지수(0=선형)
        self.filter = PointFilter()
        self.clutched = False
        self._prev = None           # 상대 모드 직전 필터 좌표
        self._prev_t = None
        self.cursor = (screen_w / 2, screen_h / 2)

    def reset(self, cursor=None):
        """추적이 끊겼다 돌아왔을 때 튀지 않도록 상태 초기화."""
        self.filter.reset()
        self._prev = self._prev_t = None
        if cursor is not None:
            self.cursor = cursor

    def _calibrated(self, x: float, y: float):
        """가동 범위를 화면 크기 단위(px)로 펼친 좌표."""
        sx, sy = self.calib.span
        return (x - self.calib.min_x) / sx * self.w, (y - self.calib.min_y) / sy * self.h

    def update(self, x: float, y: float, t: float):
        """정규화 좌표(0~1)를 받아 새 커서 위치(px)를 돌려준다."""
        qx, qy = self.filter(*self._calibrated(x, y), t)

        if self.mode == "absolute":
            cx, cy = self.w / 2, self.h / 2
            tx = cx + (qx - cx) * self.sensitivity
            ty = cy + (qy - cy) * self.sensitivity
            self.cursor = (min(max(tx, 0), self.w - 1), min(max(ty, 0), self.h - 1))
            return self.cursor

        # 상대 모드
        if self._prev is None or self.clutched:
            self._prev, self._prev_t = (qx, qy), t
            return self.cursor
        dx, dy = qx - self._prev[0], qy - self._prev[1]
        dt = max(t - self._prev_t, 1e-3)
        self._prev, self._prev_t = (qx, qy), t
        speed = math.hypot(dx, dy) / dt                       # px/s
        factor = self.gain * min(max((speed / 600.0) ** self.accel, 0.25), 4.0)
        nx = min(max(self.cursor[0] + dx * factor, 0), self.w - 1)
        ny = min(max(self.cursor[1] + dy * factor, 0), self.h - 1)
        self.cursor = (nx, ny)
        return self.cursor
