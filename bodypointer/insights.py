"""사용 기록 분석 → 맞춤 추천.

진단 표현은 쓰지 않는다 (FR-54). 모든 추천은 사용자가 '적용'을 눌러야 반영된다.
툴바 '빠른 실행'만 자동으로 채운다 (자주 쓴 기능 상위 3개).
"""
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field

from .actions import get_action
from .gestures import GESTURE_ORDER, GESTURES
from .parts import get_part


@dataclass
class Rec:
    id: str
    icon: str
    title: str
    body: str
    level: str = "tip"                 # tip | good | care | info
    apply: dict = field(default_factory=dict)
    apply_label: str = "적용"


def aggregate(sessions: list[dict]) -> dict:
    agg = {"actions": Counter(), "seqs": Counter(), "clicks": 0, "cancels": 0, "misclicks": 0,
           "duration": 0.0, "lost_secs": 0.0, "typed": 0, "saved": 0, "jitter": [], "part_lost": Counter(),
           "part_secs": Counter(), "cells": Counter(), "hours": Counter(), "gestures": Counter()}
    for s in sessions:
        agg["actions"].update(s.get("actions", {}))
        agg["seqs"].update(s.get("seqs", {}))
        agg["gestures"].update(s.get("gestures", {}))
        agg["clicks"] += s.get("click_total", 0)
        agg["cancels"] += s.get("popup_cancels", 0)
        agg["misclicks"] += s.get("misclicks", 0)
        agg["duration"] += s.get("duration", 0)
        agg["lost_secs"] += s.get("lost_secs", 0)
        agg["typed"] += s.get("typed_chars", 0)
        agg["saved"] += s.get("saved_chars", 0)
        if s.get("jitter"):
            agg["jitter"].append(s["jitter"])
        agg["part_secs"].update(s.get("part_secs", {}))
        if s.get("main_part"):
            agg["part_lost"][s["main_part"]] += s.get("lost_secs", 0)
        agg["cells"].update(s.get("click_cells", {}))
        agg["hours"][s.get("hour", 0)] += s.get("duration", 0)
    return agg


def auto_quick_actions(sessions: list[dict], k: int = 3) -> list[str]:
    """툴바에 자동으로 올릴 자주 쓴 기능."""
    agg = aggregate(sessions)
    skip = {"toggle_keyboard", "toggle_pause", "backspace", "enter", "left", "right", "up", "down"}
    return [a for a, n in agg["actions"].most_common() if n >= 3 and a not in skip and get_action(a)][:k]


def recommend(sessions: list[dict], settings) -> list[Rec]:
    recs: list[Rec] = []
    if not sessions:
        return [Rec("start", "bulb", "기록을 모아 보세요",
                    "사용을 마치고 툴바의 ‘저장’을 누르면, 쓰는 습관을 분석해 맞춤 기능을 추천해 드려요.", "info")]
    agg = aggregate(sessions)
    bound = set(settings.gestures.values())
    free_gestures = [g for g in GESTURE_ORDER if g not in settings.gestures]

    # 1. 자주 쓰는 기능 → 개인 제스처로 등록 제안
    skip = {"toggle_keyboard", "backspace", "left", "right", "up", "down"}
    for action_id, n in agg["actions"].most_common(6):
        if n < 5 or action_id in bound or action_id in skip or not free_gestures:
            continue
        a = get_action(action_id)
        g = free_gestures.pop(0)
        recs.append(Rec(f"gesture:{action_id}", "emoji", f"‘{a.label}’을(를) {n}번 쓰셨어요",
                        f"‘{GESTURES[g].label}’ 표정만으로 바로 {a.label}할 수 있게 등록할까요?",
                        "tip", {"type": "bind_gesture", "gesture": g, "action": action_id}, "제스처로 등록"))
        if len(recs) >= 2:
            break

    # 2. 늘 이어서 쓰는 두 동작 → 한 번에 실행하는 묶음 동작
    for seq, n in agg["seqs"].most_common(3):
        if n < 4:
            break
        macro = f"macro:{seq}"
        a = get_action(macro)
        if a and macro not in bound and macro not in settings.quick_actions:
            recs.append(Rec(f"macro:{seq}", "switch", f"‘{a.label}’을(를) 자주 이어서 쓰세요 ({n}번)",
                            "두 동작을 한 번에 실행하는 버튼을 툴바에 추가할까요?", "tip",
                            {"type": "add_quick", "action": macro}, "툴바에 추가"))
            break

    # 3. 클릭 실수가 많으면 머무는 시간 늘리기 / 정확하면 줄이기 (FR-43)
    clicks = agg["clicks"]
    if clicks >= 10:
        miss = (agg["cancels"] + agg["misclicks"]) / max(clicks, 1)
        if miss > 0.25 and settings.dwell_time < 2.5:
            v = round(settings.dwell_time + 0.2, 1)
            recs.append(Rec("dwell_up", "touch", "클릭이 자주 취소되거나 되돌려져요",
                            f"머무는 시간을 {settings.dwell_time:.1f}초 → {v:.1f}초로 늘리면 실수가 줄어요.",
                            "tip", {"type": "set", "key": "dwell_time", "value": v}))
        elif miss < 0.05 and clicks >= 30 and settings.dwell_time > 0.6:
            v = round(settings.dwell_time - 0.1, 1)
            recs.append(Rec("dwell_down", "touch", "클릭이 아주 정확해요",
                            f"머무는 시간을 {settings.dwell_time:.1f}초 → {v:.1f}초로 줄여 더 빠르게 써 보세요.",
                            "good", {"type": "set", "key": "dwell_time", "value": v}))

    # 4. 떨림이 크면 보정 강화 (FR-42)
    if agg["jitter"] and statistics.median(agg["jitter"]) > 2.5 and settings.filter_strength < 85:
        v = min(settings.filter_strength + 10, 95)
        recs.append(Rec("filter_up", "pulse", "커서가 조금 떨리고 있어요",
                        f"떨림 보정을 {settings.filter_strength:.0f} → {v:.0f}으로 높이면 더 안정돼요.",
                        "tip", {"type": "set", "key": "filter_strength", "value": v}))

    # 5. 추적이 자주 끊기는 부위 → 다른 부위나 카메라 배치
    if agg["duration"] > 300 and agg["lost_secs"] / agg["duration"] > 0.15:
        worst = agg["part_lost"].most_common(1)[0][0] if agg["part_lost"] else settings.part_id
        alt = "nose" if worst != "nose" else "chin"
        recs.append(Rec("tracking", "camera", f"‘{get_part(worst).name}’ 추적이 자주 끊겨요",
                        "카메라에 그 부위가 다 보이도록 위치를 바꾸거나, 더 잘 잡히는 부위로 바꿔 보세요.",
                        "care", {"type": "part", "part": alt}, f"‘{get_part(alt).name}’(으)로 바꾸기"))

    # 6. 가동 범위 변화 (재활 참고용, 진단 아님)
    main = [s for s in sessions if s.get("main_part") == settings.part_id and s.get("rom_x")]
    if len(main) >= 4:
        areas = [s["rom_x"] * s["rom_y"] for s in main]
        base, last = statistics.median(areas[:-1]), areas[-1]
        if base > 0 and last > base * 1.2:
            recs.append(Rec("rom_up", "health", "움직이는 범위가 넓어지고 있어요",
                            "지난 기록보다 이번에 더 크게 움직이셨어요. 꾸준함이 보여요!", "good"))
        elif base > 0 and last < base * 0.7:
            recs.append(Rec("rom_down", "health", "이번엔 움직임 범위가 평소보다 작았어요",
                            "피곤하셨을 수 있어요. 무리하지 마시고, 변화가 계속되면 전문가와 상의해 보세요.", "care"))

    # 7. 오래 쉬지 않고 쓰면 휴식 알림
    avg_min = agg["duration"] / len(sessions) / 60
    if avg_min > 45 and not settings.break_reminder:
        recs.append(Rec("break", "history", f"한 번에 평균 {avg_min:.0f}분씩 쓰세요",
                        "40분마다 쉬어 가자고 알려 드릴까요?", "tip",
                        {"type": "set", "key": "break_reminder", "value": True}, "알림 켜기"))

    # 8. 글을 많이 쓰면 키보드 머무는 시간 줄이기
    if agg["typed"] > 300 and settings.kb_dwell_time > 0.6:
        v = round(settings.kb_dwell_time - 0.1, 1)
        recs.append(Rec("kb_fast", "keyboard", f"키보드로 {agg['typed']}글자를 입력하셨어요",
                        f"키보드의 머무는 시간을 {settings.kb_dwell_time:.1f}초 → {v:.1f}초로 줄여 더 빨리 쳐 보세요.",
                        "tip", {"type": "set", "key": "kb_dwell_time", "value": v}))

    dismissed = set(settings.dismissed_recs)
    return [r for r in recs if r.id not in dismissed]


def headline_stats(sessions: list[dict], days: int = 7) -> dict:
    cutoff = time.time() - days * 86400
    recent = [s for s in sessions if s.get("started", 0) >= cutoff]
    agg = aggregate(recent)
    clicks = agg["clicks"]
    acc = 1 - (agg["cancels"] + agg["misclicks"]) / clicks if clicks else None
    return {"minutes": agg["duration"] / 60, "clicks": clicks, "accuracy": acc, "saved": agg["saved"],
            "typed": agg["typed"], "sessions": len(recent)}
