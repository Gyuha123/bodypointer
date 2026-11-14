"""화면 오버레이: 커서를 따라다니는 머무르기 게이지 + 상단 안내 배너 (FR-03, FR-04, FR-30).

두 창 모두 클릭 투과라 아래 앱 조작을 막지 않는다.
작은 창을 커서 위치로 옮겨 그리는 방식이라 전체 화면을 매 프레임 다시 그리지 않는다.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import theme

OVERLAY_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)

COLORS = {
    "left": QColor("#6366F1"), "menu": QColor("#6366F1"), "double": QColor("#A855F7"),
    "right": QColor("#F59E0B"), "drag": QColor("#10B981"), "scroll": QColor("#10B981"), "keyboard": QColor("#22D3EE"),
}


def _transparent(w: QWidget):
    w.setWindowFlags(OVERLAY_FLAGS)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def force_topmost(w: QWidget):
    """다른 '항상 위' 창(작업 표시줄, 툴바, 키보드, 다른 프로그램)보다 앞으로 다시 올린다. 포커스는 가져가지 않음."""
    try:
        import ctypes
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = -1, 0x1, 0x2, 0x10, 0x200
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(int(w.winId())), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
    except Exception:
        w.raise_()


class CursorRing(QWidget):
    SMALL, LARGE = 100, 420
    R = 18                       # 링 반지름 (px)

    def __init__(self):
        super().__init__()
        _transparent(self)
        self.resize(self.SMALL, self.SMALL)
        self.state = {}
        self._top_timer = QTimer(self, interval=300, timeout=lambda: self.isVisible() and force_topmost(self))
        self._top_timer.start()

    def update_state(self, st: dict):
        """st: center(논리 px), progress, paused, lost, drag, scroll(dead, dy), color_key, clutched"""
        self.state = st
        size = self.LARGE if st.get("scroll") else self.SMALL
        if self.width() != size:
            self.resize(size, size)
        cx, cy = st["center"]
        self.move(int(cx - size / 2), int(cy - size / 2))
        if not self.isVisible():
            self.show()
            force_topmost(self)
        self.update()

    def paintEvent(self, _):
        st = self.state
        if not st:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        green = COLORS["scroll"]

        if st.get("scroll"):
            dead, dy = st["scroll"]
            p.setPen(QPen(green, 3))
            fill = QColor(green)
            fill.setAlpha(40)
            p.setBrush(fill)
            p.drawEllipse(c, dead, dead)
            for sign in (-1, 1):
                tip = QPointF(c.x(), c.y() + sign * (dead + 70))
                p.drawLine(QPointF(c.x(), c.y() + sign * (dead + 12)), tip)
                p.drawLine(tip, QPointF(tip.x() - 16, tip.y() - sign * 16))
                p.drawLine(tip, QPointF(tip.x() + 16, tip.y() - sign * 16))
            ptr = QPointF(c.x(), c.y() + max(-self.height() / 2 + 14, min(self.height() / 2 - 14, dy)))
            p.setPen(QPen(QColor(255, 255, 255), 3))
            p.setBrush(green)
            p.drawEllipse(ptr, 11, 11)
            p.end()
            return

        r = self.R
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if st.get("lost"):
            red = QColor("#EF4444")
            p.setPen(QPen(red, 4))
            p.drawEllipse(rect)
            p.drawLine(QPointF(c.x() - 8, c.y() - 8), QPointF(c.x() + 8, c.y() + 8))
            p.drawLine(QPointF(c.x() + 8, c.y() - 8), QPointF(c.x() - 8, c.y() + 8))
        elif st.get("paused"):
            p.setPen(QPen(QColor(110, 116, 128, 210), 2.5, Qt.PenStyle.DashLine))
            p.drawEllipse(rect)
        else:
            # 어떤 배경에서도 보이도록 흰 링 + 얇은 어두운 테두리
            p.setPen(QPen(QColor(0, 0, 0, 80), 7))
            p.drawEllipse(rect)
            p.setPen(QPen(QColor(255, 255, 255, 230), 4))
            p.drawEllipse(rect)

        prog = st.get("progress", 0)
        if prog > 0 and not st.get("lost"):
            col = COLORS.get(st.get("color_key", "left"), COLORS["left"])
            p.setPen(QPen(col, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, -int(360 * 16 * prog))

        text = ("드래그 중" if st.get("drag") else "스크롤할 곳" if st.get("pick")
                else "커서 고정" if st.get("clutched") else "")
        if text:
            f = theme.ui_font(13, QFont.Weight.Bold)
            p.setFont(f)
            w = QFontMetrics(f).horizontalAdvance(text) + 20
            br = QRectF(c.x() - w / 2, c.y() + r + 6, w, 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(22, 24, 29, 220))
            p.drawRoundedRect(br, 13, 13)
            p.setPen(QColor(255, 255, 255))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class Banner(QWidget):
    """화면 위쪽 가운데 알림 알약. 지속 알림(sticky)과 잠깐 알림(flash)."""

    DOT = {"warn": QColor("#F87171"), "ok": QColor("#34D399"), "info": QColor("#818CF8")}

    def __init__(self):
        super().__init__()
        _transparent(self)
        self.text, self.level = "", "info"
        self._timer = QTimer(self, singleShot=True, timeout=self._expire)
        self.sticky: dict[str, tuple[str, str]] = {}

    def flash(self, text: str, level: str = "info", ms: int = 2500):
        self.text, self.level = text, level
        self._timer.start(ms)
        self._show()

    def set_sticky(self, key: str, text: str | None, level: str = "warn"):
        if text:
            if self.sticky.get(key) == (text, level):
                return
            self.sticky[key] = (text, level)
        elif key in self.sticky:
            self.sticky.pop(key)
        else:
            return
        if not self._timer.isActive():
            self._expire()

    def _expire(self):
        if self.sticky:
            self.text, self.level = list(self.sticky.values())[-1]
            self._show()
        else:
            self.text = ""
            self.hide()

    def _font(self):
        return theme.ui_font(19, QFont.Weight.DemiBold)

    def _show(self):
        scr = QApplication.primaryScreen().geometry()
        w = min(scr.width() - 40, QFontMetrics(self._font()).horizontalAdvance(self.text) + 96)
        self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + 20, w, 64)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _):
        if not self.text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(22, 24, 29, 235))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.DOT.get(self.level, self.DOT["info"]))
        p.drawEllipse(QPointF(r.x() + 30, r.center().y()), 7, 7)
        p.setFont(self._font())
        p.setPen(QColor(245, 246, 248))
        p.drawText(r.adjusted(50, 0, -22, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.end()
