"""개인 제스처: 얼굴 표정(블렌드셰이프)으로 동작을 실행한다.

일부러 한 표정만 인식하도록:
- 점수가 기준을 넘은 상태를 hold 초 동안 유지해야 발동
- 발동 후에는 점수가 기준의 70% 아래로 내려갔다 와야 다시 발동 (계속 입을 벌리고 있어도 한 번만)
- 발동 사이 최소 간격(cooldown)
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Gesture:
    id: str
    label: str
    hint: str
    threshold: float
    hold: float = 0.35


GESTURES: dict[str, Gesture] = {g.id: g for g in [
    Gesture("brow_raise", "눈썹 올리기", "눈썹을 위로 쭉 올렸다 내리세요", 0.55),
    Gesture("mouth_open", "입 벌리기", "입을 크게 벌렸다 다무세요", 0.45),
    Gesture("smile", "활짝 웃기", "입꼬리를 올려 활짝 웃으세요", 0.6),
    Gesture("pucker", "입술 내밀기", "뽀뽀하듯 입술을 내미세요", 0.6),
    Gesture("long_blink", "눈 지그시 감기", "두 눈을 1초 동안 감았다 뜨세요", 0.6, 0.9),
]}
GESTURE_ORDER = ["brow_raise", "mouth_open", "smile", "long_blink", "pucker"]


def gesture_score(gid: str, blend: dict) -> float:
    b = blend or {}
    if gid == "brow_raise":
        return b.get("browInnerUp", 0)
    if gid == "mouth_open":
        return b.get("jawOpen", 0)
    if gid == "smile":
        return (b.get("mouthSmileLeft", 0) + b.get("mouthSmileRight", 0)) / 2
    if gid == "pucker":
        return b.get("mouthPucker", 0)
    if gid == "long_blink":
        return min(b.get("eyeBlinkLeft", 0), b.get("eyeBlinkRight", 0))
    return 0.0


class GestureDetector:
    def __init__(self, sensitivity: float = 1.0, cooldown: float = 1.2):
        self.sensitivity = sensitivity        # >1 이면 기준을 낮춰 더 쉽게 인식
        self.cooldown = cooldown
        self._since = {}
        self._armed = {g: True for g in GESTURES}
        self._last_fire = -1e9
        self.scores = {}

    def threshold(self, gid):
        return GESTURES[gid].threshold / max(self.sensitivity, 0.3)

    def update(self, blend: dict | None, t: float, active: set[str]) -> list[str]:
        """active: 동작이 연결된 제스처만 검사. 발동한 제스처 id 목록을 돌려준다."""
        fired = []
        if not blend:
            self._since.clear()
            return fired
        for gid in GESTURES:
            s = gesture_score(gid, blend)
            self.scores[gid] = s
            if gid not in active:
                continue
            th = self.threshold(gid)
            if s < th * 0.7:
                self._armed[gid] = True
                self._since.pop(gid, None)
                continue
            if s < th or not self._armed[gid]:
                self._since.pop(gid, None)
                continue
            start = self._since.setdefault(gid, t)
            if t - start >= GESTURES[gid].hold and t - self._last_fire >= self.cooldown:
                fired.append(gid)
                self._armed[gid] = False
                self._since.pop(gid, None)
                self._last_fire = t
        return fired
