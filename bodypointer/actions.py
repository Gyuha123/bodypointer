"""실행할 수 있는 동작 목록. 키보드 바, 툴바 빠른 실행, 개인 제스처가 모두 이 목록을 공유한다.

kind:
  keys  - 단축키 조합 전송
  app   - BodyPointer 자체 기능 (앱이 처리)
  hold  - 표정을 유지하는 동안 계속되는 동작 (제스처 전용: 시작/끝 두 번 호출)
  macro - 여러 동작을 차례로 실행 ("macro:select_all+copy")
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    icon: str
    kind: str = "keys"
    keys: tuple = ()
    group: str = "편집"


def _k(id_, label, icon, *keys, group="편집"):
    return Action(id_, label, icon, "keys", keys, group)


ACTIONS: dict[str, Action] = {a.id: a for a in [
    # 마우스 (제스처로 쓰기 좋은 것)
    Action("click", "바로 클릭", "touch", "app", group="마우스"),
    Action("double_click", "더블클릭", "touch", "app", group="마우스"),
    Action("right_click", "우클릭", "mouse", "app", group="마우스"),
    Action("drag_hold", "꾹 누르기 (표정 유지하는 동안)", "select_all", "hold", group="마우스"),
    Action("drag_toggle", "꾹 누르기 켜기/끄기", "select_all", "app", group="마우스"),
    Action("scroll_hold", "스크롤 (표정 유지하는 동안)", "switch", "hold", group="마우스"),
    Action("scroll_toggle", "스크롤 켜기/끄기", "switch", "app", group="마우스"),
    # 편집
    _k("copy", "복사", "copy", "ctrl", "c"),
    _k("paste", "붙여넣기", "paste", "ctrl", "v"),
    _k("cut", "잘라내기", "cut", "ctrl", "x"),
    _k("undo", "실행 취소", "undo", "ctrl", "z"),
    _k("redo", "다시 실행", "redo", "ctrl", "y"),
    _k("select_all", "전체 선택", "select_all", "ctrl", "a"),
    _k("enter", "엔터", "enter", "enter"),
    _k("backspace", "지우기", "backspace", "backspace"),
    _k("delete", "Delete", "delete", "delete"),
    _k("tab", "Tab", "tab", "tab"),
    _k("esc", "Esc", "close", "esc"),
    _k("left", "←", "chevron_left", "left"),
    _k("right", "→", "chevron_right", "right"),
    _k("up", "↑", "up", "up"),
    _k("down", "↓", "down", "down"),
    _k("save_file", "파일 저장", "save", "ctrl", "s"),
    _k("find", "찾기", "search", "ctrl", "f"),
    # 탐색
    _k("browser_back", "뒤로 가기", "back", "alt", "left", group="탐색"),
    _k("refresh", "새로고침", "refresh", "f5", group="탐색"),
    _k("new_tab", "새 탭", "add", "ctrl", "t", group="탐색"),
    _k("close_tab", "탭 닫기", "close", "ctrl", "w", group="탐색"),
    _k("switch_window", "창 전환", "switch", "alt", "tab", group="탐색"),
    _k("zoom_in", "확대", "zoom_in", "ctrl", "plus", group="탐색"),
    _k("zoom_out", "축소", "zoom_out", "ctrl", "minus", group="탐색"),
    _k("page_down", "페이지 아래", "down", "pagedown", group="탐색"),
    _k("page_up", "페이지 위", "up", "pageup", group="탐색"),
    # 앱
    Action("toggle_pause", "클릭 멈춤/재개", "pause", "app", group="앱"),
    Action("toggle_keyboard", "키보드 열기/닫기", "keyboard", "app", group="앱"),
    Action("next_part", "부위 바꾸기", "sync", "app", group="앱"),
]}

GROUP_ORDER = ["마우스", "편집", "탐색", "앱"]

# ---------------- 링크 열기 ----------------
# 동작 id 형식: "link:이름\t주소"  (예: "link:유튜브\thttps://www.youtube.com")
LINK_PREFIX = "link:"
LINK_PRESETS = [("네이버", "https://www.naver.com"), ("유튜브", "https://www.youtube.com"),
                ("구글", "https://www.google.com"), ("다음", "https://www.daum.net"),
                ("네이버 지도", "https://map.naver.com"), ("날씨", "https://weather.naver.com")]


def normalize_url(text: str) -> str | None:
    """사용자가 입력한 주소를 열 수 있는 https/http 주소로. 다른 형식(file:, 프로그램 경로 등)은 거부."""
    import re
    from urllib.parse import urlsplit
    t = (text or "").strip()
    if not t or any(c.isspace() for c in t):
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) and not re.match(r"^[^:/]+:\d+", t):
            return None                     # mailto:, javascript:, C:\ 같은 형식
        t = "https://" + t
    parts = urlsplit(t)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        return None
    return t


def make_link_action(name: str, url: str) -> str:
    name = (name or "").replace("\t", " ").strip()
    return f"{LINK_PREFIX}{name}\t{url}"


def link_parts(action_id: str) -> tuple[str, str] | None:
    if not action_id.startswith(LINK_PREFIX):
        return None
    name, _, url = action_id[len(LINK_PREFIX):].partition("\t")
    url = normalize_url(url)
    if not url:
        return None
    if not name:
        from urllib.parse import urlsplit
        name = urlsplit(url).hostname.removeprefix("www.")
    return name, url
# 키보드 바 편집 패널에 놓을 동작
EDIT_KEYS = ["copy", "paste", "cut", "undo", "select_all", "delete", "left", "right", "up", "down", "tab", "esc"]
# 일시정지 중에도 실행되는 동작
ALLOWED_WHILE_PAUSED = {"toggle_pause", "toggle_keyboard"}
# 툴바·키보드에서는 쓸 수 없는 제스처 전용 동작
GESTURE_ONLY = {a.id for a in ACTIONS.values() if a.kind == "hold"}


def get_action(action_id: str) -> Action | None:
    if action_id == "link":                 # 사용 기록용 묶음 이름 (주소는 기록하지 않음)
        return Action("link", "링크 열기", "globe", "link", group="링크")
    if action_id.startswith(LINK_PREFIX):
        lp = link_parts(action_id)
        return Action(action_id, f"{lp[0]} 열기", "globe", "link", group="링크") if lp else None
    if action_id.startswith("macro:"):
        parts = [ACTIONS[p] for p in action_id[6:].split("+") if p in ACTIONS]
        if not parts:
            return None
        return Action(action_id, " + ".join(p.label for p in parts), parts[-1].icon, "macro")
    return ACTIONS.get(action_id)


def macro_steps(action_id: str) -> list[str]:
    return [p for p in action_id[6:].split("+") if p in ACTIONS] if action_id.startswith("macro:") else [action_id]
