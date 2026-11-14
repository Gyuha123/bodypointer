"""Dwell 클릭 감지 (FR-30): 포인터가 반경 안에 일정 시간 머물면 발동.

떨림 클릭 보조 (grace > 0):
  - 게이지가 차는 중 반경 밖으로 잠깐(grace초 이내) 튀어도 처음부터 다시 시작하지 않는다.
  - 기준점은 머무는 동안의 평균 위치로 천천히 옮겨 간다 → 떨림의 '가운데'를 클릭한다.
"""
import math


class DwellDetector:
    def __init__(self, dwell_time: float = 1.0, radius: float = 35.0, grace: float = 0.0):
        self.dwell_time = dwell_time
        self.radius = radius
        self.grace = grace
        self.anchor = None
        self.start = 0.0
        self.armed = True       # 발동 후 포인터가 반경 밖으로 나가야 다시 활성화
        self.progress = 0.0
        self._outside_since = None

    def reset(self):
        self.anchor = None
        self.progress = 0.0
        self.armed = True
        self._outside_since = None

    def _restart(self, pos, t):
        self.anchor, self.start = pos, t
        self.armed = True
        self.progress = 0.0
        self._outside_since = None

    def update(self, pos, t: float):
        """발동 시 클릭할 좌표를, 아니면 None을 돌려준다. self.progress(0~1)는 게이지 표시용."""
        if self.anchor is None:
            self._restart(pos, t)
            return None
        if math.dist(pos, self.anchor) > self.radius:
            # 떨림 보조: 게이지가 이미 차고 있으면 잠깐 튄 것은 봐준다
            if self.grace > 0 and self.armed and self.progress > 0:
                self._outside_since = self._outside_since or t
                if t - self._outside_since <= self.grace:
                    return self._advance(t)
            self._restart(pos, t)
            return None
        self._outside_since = None
        if self.grace > 0 and self.armed:
            ax, ay = self.anchor                      # 떨림의 가운데로 천천히 이동
            self.anchor = (ax + (pos[0] - ax) * 0.08, ay + (pos[1] - ay) * 0.08)
        if not self.armed:
            self.progress = 0.0
            return None
        return self._advance(t)

    def _advance(self, t):
        self.progress = min((t - self.start) / self.dwell_time, 1.0)
        if self.progress >= 1.0:
            self.armed = False
            self.progress = 0.0
            self._outside_since = None
            return self.anchor
        return None
