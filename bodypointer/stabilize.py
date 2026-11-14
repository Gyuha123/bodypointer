"""인식 좌표 안정화 (하체 등 흔들리는 부위용).

1. 신뢰도가 낮은 프레임은 버리고, 짧은 시간(hold) 동안 직전 위치를 유지한다 → 깜빡이며 '추적 끊김'이 반복되지 않음.
2. 신뢰도가 낮으면서 갑자기 크게 튀는 값(오검출)은 무시한다. 같은 방향으로 계속 오면 받아들인다.
3. 신뢰도에 비례해 따라간다 → 확실한 프레임은 빠르게, 애매한 프레임은 천천히.
"""
import math


class PointStabilizer:
    def __init__(self, min_conf=0.5, smooth=0.0, hold=0.4, max_jump=0.12):
        self.min_conf, self.smooth, self.hold, self.max_jump = min_conf, smooth, hold, max_jump
        self.reset()

    def configure(self, min_conf, smooth):
        self.min_conf, self.smooth = min_conf, smooth
        self.reset()

    def reset(self):
        self.last = None
        self.last_t = 0.0
        self._rejects = 0
        self.low_conf_since = None     # 신뢰도가 계속 낮았던 시작 시각 (카메라 배치 안내용)

    def __call__(self, sample, t):
        """sample: (x, y, conf) 또는 None. 반환: (x, y) 또는 None."""
        if sample is None or sample[2] < self.min_conf:
            if sample is not None and self.low_conf_since is None:
                self.low_conf_since = t
            if self.last is not None and t - self.last_t <= self.hold:
                return self.last
            return None
        self.low_conf_since = None
        x, y, conf = sample
        if self.last is not None:
            jump = math.hypot(x - self.last[0], y - self.last[1])
            if jump > self.max_jump and conf < 0.85 and self._rejects < 3:
                self._rejects += 1
                return self.last if t - self.last_t <= self.hold else None
        self._rejects = 0
        if self.last is not None and self.smooth > 0:
            # 신뢰도 1 → 그대로, 신뢰도 낮을수록 smooth 비율만큼 덜 움직임
            a = 1.0 - self.smooth * (1.0 - min(max((conf - self.min_conf) / (1 - self.min_conf + 1e-6), 0), 1))
            x = self.last[0] + a * (x - self.last[0])
            y = self.last[1] + a * (y - self.last[1])
        self.last, self.last_t = (x, y), t
        return self.last
