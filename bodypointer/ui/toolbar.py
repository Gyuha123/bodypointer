"""떠 있는 툴바 + 클릭 종류 메뉴 (FR-31, FR-33, FR-34).

포커스를 가져가지 않는 창이라 작업 중인 앱의 입력을 방해하지 않는다.
Dwell이 이 창 위에서 발동하면 OS 클릭 대신 버튼의 click()을 직접 호출한다.
"""
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QGridLayout, QVBoxLayout, QWidget)

from ..actions import get_action
from . import theme
from .widgets import IconButton, label

FLOAT_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
               | Qt.WindowType.WindowDoesNotAcceptFocus)

CLICK_CYCLE = [("menu", "mouse", "메뉴"), ("left", "touch", "좌클릭"), ("double", "touch", "더블"),
               ("right", "mouse", "우클릭"), ("drag", "select_all", "드래그")]


class FloatingPanel(QWidget):
    """둥근 어두운 패널을 가진 떠 있는 창의 공통 바탕."""

    def __init__(self, name):
        super().__init__()
        self.setObjectName(name)
        self.setWindowFlags(FLOAT_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(blurRadius=24, offset=QPoint(0, 4).toPointF(), color=QColor(0, 0, 0, 90))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)
        self.setStyleSheet(theme.overlay_stylesheet())

    def restyle(self):
        self.setStyleSheet(theme.overlay_stylesheet())


def _tool(icon, text, w=92, h=66, checkable=False):
    b = IconButton(icon, text, under=True, icon_size=24, overlay=True, checkable=checkable)
    b.setFixedSize(w, h)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class Toolbar(FloatingPanel):
    pauseClicked = Signal()
    clickModeClicked = Signal()
    scrollUp = Signal()
    scrollDown = Signal()
    keyboardClicked = Signal()
    saveClicked = Signal()
    nextPart = Signal()
    clutch = Signal()
    settings = Signal()
    quickAction = Signal(str)

    def __init__(self):
        super().__init__("toolbar")
        self.setWindowTitle("BodyPointer 툴바")
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)
        self.pause_btn = _tool("play", "재개")
        self.mode_btn = _tool("mouse", "메뉴")
        self.up_btn = _tool("up", "위로")
        self.down_btn = _tool("down", "아래로")
        self.kb_btn = _tool("keyboard", "키보드", checkable=True)
        self.part_btn = _tool("sync", "부위")
        self.clutch_btn = _tool("clutch", "고정")
        self.save_btn = _tool("save", "저장")
        self.settings_btn = _tool("settings", "설정")
        for b, sig in [(self.pause_btn, self.pauseClicked), (self.mode_btn, self.clickModeClicked),
                       (self.up_btn, self.scrollUp), (self.down_btn, self.scrollDown),
                       (self.kb_btn, self.keyboardClicked), (self.part_btn, self.nextPart),
                       (self.clutch_btn, self.clutch), (self.save_btn, self.saveClicked),
                       (self.settings_btn, self.settings)]:
            b.clicked.connect(sig.emit)
        for b in (self.pause_btn, self.mode_btn, self.up_btn, self.down_btn, self.kb_btn):
            lay.addWidget(b)
        # 빠른 실행: 자주 쓴 기능이 자동으로 올라온다
        self.quick_title = label("빠른 실행", "muted", align=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.quick_title)
        self.quick_box = QVBoxLayout()
        self.quick_box.setSpacing(6)
        lay.addLayout(self.quick_box)
        self.quick_buttons = []
        for b in (self.part_btn, self.clutch_btn, self.save_btn, self.settings_btn):
            lay.addWidget(b)
        self.scroll_buttons = {self.up_btn: 1, self.down_btn: -1}
        self.set_quick_actions([])

    def place(self):
        self.adjustSize()
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.right() - self.width() + 2, g.top() + max(0, (g.height() - self.height()) // 2))

    def set_paused(self, paused: bool):
        self.pause_btn.setText("재개" if paused else "정지")
        self.pause_btn.set_icon_name("play" if paused else "pause")
        self.pause_btn.set_variant("primary" if paused else "")

    def set_click_mode(self, key: str):
        for k, ic, text in CLICK_CYCLE:
            if k == key:
                self.mode_btn.setText(text)
                self.mode_btn.set_icon_name(ic)

    def set_relative(self, relative: bool):
        self.clutch_btn.setVisible(relative)
        self.adjustSize()

    def set_keyboard_open(self, is_open: bool):
        self.kb_btn.setChecked(is_open)

    def set_quick_actions(self, action_ids: list[str]):
        for b in self.quick_buttons:
            b.deleteLater()
        self.quick_buttons = []
        for aid in action_ids[:3]:
            a = get_action(aid)
            if not a:
                continue
            text = a.label if len(a.label) <= 5 else a.label[:5] + "…"
            b = _tool(a.icon, text, h=60)
            b.setToolTip(a.label)
            b.clicked.connect(lambda _=False, x=aid: self.quickAction.emit(x))
            self.quick_box.addWidget(b)
            self.quick_buttons.append(b)
        self.quick_title.setVisible(bool(self.quick_buttons))
        self.adjustSize()


class ClickPopup(FloatingPanel):
    """Dwell 지점 옆에 뜨는 클릭 종류 선택 메뉴 (FR-31). 6초 동안 선택이 없으면 닫힌다."""
    chosen = Signal(str)

    ACTIONS = [("left", "touch", "클릭"), ("double", "touch", "더블클릭"), ("right", "mouse", "우클릭"),
               ("drag", "select_all", "드래그"), ("scroll", "switch", "스크롤"), ("cancel", "close", "취소")]

    def __init__(self):
        super().__init__("popup")
        grid = QGridLayout(self.panel)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(8)
        for i, (key, ic, text) in enumerate(self.ACTIONS):
            b = _tool(ic, text, w=132, h=84)
            if key == "left":
                b.setProperty("variant", "primary")
                b.refresh_icon()
            if key == "cancel":
                b.setProperty("role", "danger")
                b.refresh_icon()
            b.clicked.connect(lambda _=False, k=key: self._choose(k))
            grid.addWidget(b, i // 3, i % 3)
        self._timer = QTimer(self, singleShot=True, timeout=lambda: self._choose("cancel"))
        self.target = None

    def popup_at(self, logical_pt: QPoint, physical_target):
        """대상 지점을 가리지 않도록 옆에 띄운다."""
        self.target = physical_target
        self.adjustSize()
        g = QApplication.primaryScreen().availableGeometry()
        gap = 60
        x = logical_pt.x() + gap if logical_pt.x() + gap + self.width() < g.right() else logical_pt.x() - gap - self.width()
        y = max(g.top() + 4, min(logical_pt.y() - self.height() // 2, g.bottom() - self.height() - 4))
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self._timer.start(6000)

    def _choose(self, key):
        self._timer.stop()
        self.hide()
        self.chosen.emit(key)
