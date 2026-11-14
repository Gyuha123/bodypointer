"""개인 제스처: 얼굴 표정(블렌드셰이프)으로 동작을 실행한다.

두 가지 방식:
  tap  - 표정을 잠깐 유지하면 한 번 실행 (클릭, 복사 등)
  hold - 표정을 짓는 동안 계속 (꾹 누르기 = 드래그, 휠 누른 듯한 스크롤). 표정을 풀면 끝.

일부러 한 표정만 인식하도록:
  - 기준을 넘은 상태를 잠깐 유지해야 시작
  - 기준의 70% 아래로 내려가야 '풀었다'고 본다 (경계에서 깜빡이지 않게)
  - tap은 발동 후 표정을 풀었다가 다시 지어야 또 발동, 발동 사이 최소 간격
"""
import statistics
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
    # 말하거나 집중할 때 저절로 나오기 쉬워 기준을 높이고 더 오래 유지해야 인식
    Gesture("pucker", "입술 내밀기", "뽀뽀하듯 입술을 0.6초 동안 내미세요", 0.7, 0.6),
    Gesture("long_blink", "눈 2초 감기", "두 눈을 2초 동안 감았다 뜨세요", 0.6, 2.0),
]}
GESTURE_ORDER = ["brow_raise", "mouth_open", "smile", "long_blink", "pucker"]

# 처음 쓰는 사람을 위한 추천 연결 (한 번에 적용)
PRESET = {"brow_raise": "click", "mouth_open": "drag_hold", "smile": "scroll_hold", "long_blink": "toggle_keyboard"}

HOLD_START = 0.25        # hold 방식: 이만큼 유지하면 시작
RELEASE_GRACE = 0.15     # hold 방식: 이만큼 풀려 있어야 끝 (순간 흔들림 무시)


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
    def __init__(self, sensitivity: float = 1.0, cooldown: float = 1.2, personal: dict | None = None):
        self.sensitivity = sensitivity        # >1 이면 기준을 낮춰 더 쉽게 인식
        self.cooldown = cooldown
        self.personal = dict(personal or {})  # '내 표정으로 맞추기'로 정한 기준
        self._since = {}
        self._below_since = {}
        self._armed = {g: True for g in GESTURES}
        self._holding = set()
        self._last_fire = -1e9
        self.scores = {}
        self.last_event = {}                  # gid → 마지막으로 인식된 시각 (화면 표시용)

    def threshold(self, gid):
        base = self.personal.get(gid, GESTURES[gid].threshold)
        return min(max(base / max(self.sensitivity, 0.3), 0.05), 0.95)

    def holding(self) -> set:
        return set(self._holding)

    def release_all(self) -> list:
        ended = [(g, "end") for g in self._holding]
        self._holding.clear()
        return ended

    def update(self, blend: dict | None, t: float, modes: dict[str, str]) -> list[tuple[str, str]]:
        """modes: {제스처 id: 'tap' | 'hold'} (동작이 연결된 것만).
        반환: [(제스처 id, 'tap' | 'start' | 'end'), ...]"""
        events = []
        if not blend:
            self._since.clear()
            return events + self.release_all()       # 얼굴을 놓치면 누르고 있던 것은 놓는다
        for gid in GESTURES:
            s = gesture_score(gid, blend)
            self.scores[gid] = s
            mode = modes.get(gid)
            if mode is None:
                if gid in self._holding:
                    self._holding.discard(gid)
                    events.append((gid, "end"))
                continue
            th = self.threshold(gid)
            above, released = s >= th, s < th * 0.7

            if mode == "hold":
                if gid in self._holding:
                    if released:
                        start = self._below_since.setdefault(gid, t)
                        if t - start >= RELEASE_GRACE:
                            self._holding.discard(gid)
                            self._below_since.pop(gid, None)
                            events.append((gid, "end"))
                    else:
                        self._below_since.pop(gid, None)
                    continue
                if above:
                    start = self._since.setdefault(gid, t)
                    if t - start >= HOLD_START:
                        self._holding.add(gid)
                        self._since.pop(gid, None)
                        self.last_event[gid] = t
                        events.append((gid, "start"))
                else:
                    self._since.pop(gid, None)
                continue

            # tap
            if released:
                self._armed[gid] = True
                self._since.pop(gid, None)
                continue
            if not above or not self._armed[gid]:
                self._since.pop(gid, None)
                continue
            start = self._since.setdefault(gid, t)
            if t - start >= GESTURES[gid].hold and t - self._last_fire >= self.cooldown:
                events.append((gid, "tap"))
                self._armed[gid] = False
                self._since.pop(gid, None)
                self._last_fire = t
                self.last_event[gid] = t
        return events


def personal_threshold(rest_scores: list[float], peak_scores: list[float]) -> float | None:
    """'내 표정으로 맞추기': 편히 있을 때와 표정을 지었을 때 점수로 그 사람에게 맞는 기준을 정한다.
    두 상태의 차이가 너무 작으면 None (그 표정은 이 사람에게 잘 구분되지 않음)."""
    if len(rest_scores) < 5 or len(peak_scores) < 5:
        return None
    rest = statistics.median(rest_scores)
    peak = sorted(peak_scores)[int(len(peak_scores) * 0.8)]      # 상위 20% 지점 (순간 튐 제외)
    if peak - rest < 0.12:
        return None
    return round(rest + (peak - rest) * 0.55, 3)
