"""실행할 수 있는 동작 목록. 키보드 바, 툴바 빠른 실행, 개인 제스처가 모두 이 목록을 공유한다.

kind:
  keys  - 단축키 조합 전송
  app   - BodyPointer 자체 기능 (앱이 처리)
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


ACTIONS: dict[str, Action] = {a.id: a for a in [
    Action("copy", "복사", "copy", keys=("ctrl", "c")),
    Action("paste", "붙여넣기", "paste", keys=("ctrl", "v")),
    Action("cut", "잘라내기", "cut", keys=("ctrl", "x")),
    Action("undo", "실행 취소", "undo", keys=("ctrl", "z")),
    Action("redo", "다시 실행", "redo", keys=("ctrl", "y")),
    Action("select_all", "전체 선택", "select_all", keys=("ctrl", "a")),
    Action("enter", "엔터", "enter", keys=("enter",)),
    Action("backspace", "지우기", "backspace", keys=("backspace",)),
    Action("delete", "Delete", "delete", keys=("delete",)),
    Action("tab", "Tab", "tab", keys=("tab",)),
    Action("esc", "Esc", "close", keys=("esc",)),
    Action("left", "←", "chevron_left", keys=("left",)),
    Action("right", "→", "chevron_right", keys=("right",)),
    Action("up", "↑", "up", keys=("up",)),
    Action("down", "↓", "down", keys=("down",)),
    Action("browser_back", "뒤로 가기", "back", keys=("alt", "left")),
    Action("refresh", "새로고침", "refresh", keys=("f5",)),
    Action("new_tab", "새 탭", "add", keys=("ctrl", "t")),
    Action("close_tab", "탭 닫기", "close", keys=("ctrl", "w")),
    Action("switch_window", "창 전환", "switch", keys=("alt", "tab")),
    Action("zoom_in", "확대", "zoom_in", keys=("ctrl", "plus")),
    Action("zoom_out", "축소", "zoom_out", keys=("ctrl", "minus")),
    Action("save_file", "파일 저장", "save", keys=("ctrl", "s")),
    Action("find", "찾기", "search", keys=("ctrl", "f")),
    Action("page_down", "페이지 아래", "down", keys=("pagedown",)),
    Action("page_up", "페이지 위", "up", keys=("pageup",)),
    # 앱 기능
    Action("click", "바로 클릭", "touch", "app"),
    Action("double_click", "더블클릭", "touch", "app"),
    Action("right_click", "우클릭", "mouse", "app"),
    Action("toggle_pause", "일시정지/재개", "pause", "app"),
    Action("toggle_keyboard", "키보드 열기/닫기", "keyboard", "app"),
    Action("next_part", "부위 바꾸기", "sync", "app"),
]}

# 키보드 바 편집 패널에 놓을 동작
EDIT_KEYS = ["copy", "paste", "cut", "undo", "select_all", "delete", "left", "right", "up", "down", "tab", "esc"]
# 일시정지 중에도 실행되는 동작
ALLOWED_WHILE_PAUSED = {"toggle_pause", "toggle_keyboard"}


def get_action(action_id: str) -> Action | None:
    if action_id.startswith("macro:"):
        parts = [ACTIONS[p] for p in action_id[6:].split("+") if p in ACTIONS]
        if not parts:
            return None
        return Action(action_id, " + ".join(p.label for p in parts), parts[-1].icon, "macro")
    return ACTIONS.get(action_id)


def macro_steps(action_id: str) -> list[str]:
    return [p for p in action_id[6:].split("+") if p in ACTIONS] if action_id.startswith("macro:") else [action_id]
