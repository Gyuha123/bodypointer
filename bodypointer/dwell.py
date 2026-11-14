"""Dwell 클릭 감지 (FR-30): 포인터가 반경 안에 일정 시간 머물면 발동."""
import math


class DwellDetector:
    def __init__(self, dwell_time: float = 1.0, radius: float = 35.0):
        self.dwell_time = dwell_time
        self.radius = radius
        self.anchor = None
        self.start = 0.0
        self.armed = True       # 발동 후 포인터가 반경 밖으로 나가야 다시 활성화
        self.progress = 0.0

    def reset(self):
        self.anchor = None
        self.progress = 0.0
        self.armed = True

    def update(self, pos, t: float):
        """발동 시 anchor 좌표를, 아니면 None을 돌려준다. self.progress(0~1)는 게이지 표시용."""
        if self.anchor is None or math.dist(pos, self.anchor) > self.radius:
            self.anchor, self.start = pos, t
            self.armed = True
            self.progress = 0.0
            return None
        if not self.armed:
            self.progress = 0.0
            return None
        self.progress = min((t - self.start) / self.dwell_time, 1.0)
        if self.progress >= 1.0:
            self.armed = False
            self.progress = 0.0
            return self.anchor
        return None
