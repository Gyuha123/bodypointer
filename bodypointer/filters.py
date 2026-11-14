"""떨림 보정 필터 (FR-23): One Euro Filter + 정지 데드밴드."""
import math


class _LowPass:
    def __init__(self):
        self.y = None

    def __call__(self, x: float, alpha: float) -> float:
        self.y = x if self.y is None else alpha * x + (1 - alpha) * self.y
        return self.y


class OneEuroFilter:
    """Casiez et al. 2012. 느리게 움직일 땐 강하게, 빠르게 움직일 땐 약하게 거른다."""

    def __init__(self, min_cutoff=1.0, beta=0.01, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x, self._dx = _LowPass(), _LowPass()
        self._t = None
        self._prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self._x, self._dx = _LowPass(), _LowPass()
        self._t = self._prev = None

    def __call__(self, x: float, t: float) -> float:
        if self._t is None:
            self._t, self._prev = t, x
            self._dx(0.0, 1.0)
            return self._x(x, 1.0)
        dt = max(t - self._t, 1e-3)
        self._t = t
        dx = (x - self._prev) / dt
        self._prev = x
        edx = self._dx(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x(x, self._alpha(cutoff, dt))


class PointFilter:
    """2D 좌표 필터. strength 0(약)~100(강)~200(아주 강함: 떨림이 심한 경우).

    1단계 One Euro Filter로 고주파 떨림을 줄이고,
    2단계 '고무줄' 데드밴드: 출력은 입력이 일정 반경을 벗어날 때만, 벗어난 만큼만 따라간다.
    (예전처럼 반경을 넘는 순간 뚝 점프하지 않아 부드럽다)
    """

    MAX_STRENGTH = 200.0

    def __init__(self, strength: float = 60):
        self.fx, self.fy = OneEuroFilter(), OneEuroFilter()
        self.out = None
        self.set_strength(strength)

    def set_strength(self, strength: float):
        s = max(0.0, min(self.MAX_STRENGTH, strength)) / 100.0
        s1, extra = min(s, 1.0), max(0.0, s - 1.0)   # extra: 100을 넘는 '심한 떨림' 구간 (0~1)
        min_cutoff = 4.0 * (0.03 / 4.0) ** s1        # 4Hz(약) ~ 0.03Hz(강), 로그 보간
        min_cutoff *= 1.0 - 0.6 * extra              # 200이면 0.012Hz까지
        beta = 0.02 * (1.0 - 0.7 * s1) * (1.0 - 0.5 * extra)
        for f in (self.fx, self.fy):
            f.min_cutoff, f.beta = min_cutoff, beta
        self.deadband = 1.0 + 3.0 * s1 + 11.0 * extra   # 고무줄 반경(px): 4px(100) ~ 15px(200)

    def reset(self):
        self.fx.reset()
        self.fy.reset()
        self.out = None

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        fx, fy = self.fx(x, t), self.fy(y, t)
        if self.out is None:
            self.out = (fx, fy)
            return self.out
        dx, dy = fx - self.out[0], fy - self.out[1]
        d = math.hypot(dx, dy)
        if d > self.deadband:
            k = (d - self.deadband) / d               # 반경 밖으로 나간 만큼만 따라간다
            self.out = (self.out[0] + dx * k, self.out[1] + dy * k)
        return self.out
