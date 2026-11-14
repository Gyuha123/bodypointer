"""공용 UI 구성 요소. 모두 머무르기(Dwell)로 조작하기 쉬운 크기를 기본으로 한다."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme


def set_prop(w, name, value):
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)


def label(text="", role=None, wrap=False, align=None) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if align is not None:
        lb.setAlignment(align)
    return lb


class IconButton(QToolButton):
    """아이콘 + 글자 버튼. 테마가 바뀌면 refresh_icon()으로 아이콘 색을 다시 칠한다."""

    def __init__(self, icon_name: str, text: str = "", variant: str | None = None, *, under=False,
                 icon_size=22, overlay=False, checkable=False, parent=None):
        super().__init__(parent)
        self.icon_name, self.icon_px, self.overlay = icon_name, icon_size, overlay
        self._beside = bool(text) and not under
        self.setCheckable(checkable)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon if under and text
                                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                                else Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setText(text)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if variant:
            self.setProperty("variant", variant)
        self.toggled.connect(lambda _: self.refresh_icon())
        self.refresh_icon()

    def setText(self, text):
        # 아이콘 옆 글자는 살짝 띄워 읽기 편하게
        super().setText(("  " + text) if getattr(self, "_beside", False) and text else text)

    def set_icon_name(self, name):
        self.icon_name = name
        self.refresh_icon()

    def set_variant(self, variant):
        set_prop(self, "variant", variant)
        self.refresh_icon()

    def refresh_icon(self):
        if self.overlay:
            color = "#FFFFFF" if (self.isChecked() or self.property("variant") == "primary") else theme.OVERLAY["text"]
            if self.property("role") == "danger":
                color = theme.OVERLAY["danger"]
        else:
            p = theme.pal()
            v = self.property("variant")
            color = p["on_accent"] if (self.isChecked() or v == "primary") else \
                p["danger"] if v == "danger" else p["text"]
            if self.property("role") == "nav" and not self.isChecked():
                color = p["muted"]
            elif self.property("role") == "nav":
                color = p["accent"]
        self.setIcon(theme.icon(self.icon_name, color, self.icon_px))


def refresh_icons(root: QWidget):
    for b in root.findChildren(IconButton):
        b.refresh_icon()
    for b in root.findChildren(IconLabel):
        b.refresh_icon()


class Card(QFrame):
    def __init__(self, title: str | None = None, subtitle: str | None = None, tone: str | None = None,
                 spacing=12, margins=20, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        if tone:
            self.setProperty("tone", tone)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(spacing)
        if title:
            self.body.addWidget(label(title, "h2"))
        if subtitle:
            self.body.addWidget(label(subtitle, "muted", wrap=True))

    def add(self, w, stretch=0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w


class SliderRow(QWidget):
    """제목·설명 / [−] 슬라이더 [+] 값. 슬라이더를 끌기 어려우면 버튼으로 조절."""
    valueChanged = Signal(float)

    def __init__(self, title, desc, lo, hi, step, value, fmt="{:.1f}", parent=None):
        super().__init__(parent)
        self.lo, self.step, self.fmt = lo, step, fmt
        n = round((hi - lo) / step)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(2)
        head = QHBoxLayout()
        head.addWidget(label(title, "h2"))
        head.addStretch()
        self.value_label = label("", "h2")
        head.addWidget(self.value_label)
        v.addLayout(head)
        if desc:
            v.addWidget(label(desc, "muted", wrap=True))
        row = QHBoxLayout()
        self.minus = IconButton("minus", variant="ghost")
        self.plus = IconButton("add", variant="ghost")
        for b in (self.minus, self.plus):
            b.setFixedSize(52, 52)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, n)
        self.slider.setPageStep(max(1, n // 10))
        row.addWidget(self.minus)
        row.addWidget(self.slider, 1)
        row.addWidget(self.plus)
        v.addLayout(row)
        self.minus.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 1))
        self.plus.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 1))
        self.slider.valueChanged.connect(self._changed)
        self.set_value(value)

    def value(self) -> float:
        return self.lo + self.slider.value() * self.step

    def set_value(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(round((val - self.lo) / self.step))
        self.slider.blockSignals(False)
        self.value_label.setText(self.fmt.format(self.value()))

    def _changed(self, _):
        self.value_label.setText(self.fmt.format(self.value()))
        self.valueChanged.emit(self.value())


class ToggleRow(QWidget):
    """제목 + 설명 + 스위치. 줄 어디를 눌러도 켜고 끌 수 있다."""
    toggled = Signal(bool)

    def __init__(self, title, desc="", checked=False, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "h2"))
        if desc:
            text.addWidget(label(desc, "muted", wrap=True))
        h.addLayout(text, 1)
        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.toggled.connect(self.toggled.emit)
        h.addWidget(self.check)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.check.toggle()

    def set_checked(self, v):
        self.check.blockSignals(True)
        self.check.setChecked(v)
        self.check.blockSignals(False)


class Segmented(QWidget):
    """서로 배타적인 큰 선택 버튼 묶음."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str, str]], parent=None):
        """options: [(key, icon, label)]"""
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.buttons = {}
        for key, ic, text in options:
            b = IconButton(ic, text, checkable=True)
            b.setProperty("role", "chip")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.set_current(k, emit=True))
            h.addWidget(b)
            self.buttons[key] = b

    def set_current(self, key, emit=False):
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        if emit:
            self.changed.emit(key)


class Meter(QWidget):
    """가로 막대 게이지 (제스처 점수 등). 기준선 표시."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.threshold = 0.0, 0.5
        self.setFixedHeight(14)

    def set(self, value, threshold):
        self.value, self.threshold = max(0.0, min(value, 1.0)), threshold
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal()
        r = self.rect().adjusted(0, 2, 0, -2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["sunken"]))
        p.drawRoundedRect(r, 5, 5)
        hit = self.value >= self.threshold
        p.setBrush(QColor(c["ok"] if hit else c["accent"]))
        w = int(r.width() * self.value)
        if w > 0:
            p.drawRoundedRect(r.adjusted(0, 0, -(r.width() - w), 0), 5, 5)
        x = r.x() + int(r.width() * min(self.threshold, 1.0))
        p.setBrush(QColor(c["text"]))
        p.drawRect(x - 1, 0, 2, self.height())
        p.end()


class IconLabel(QLabel):
    """장식용 아이콘 (누를 수 없음)."""

    def __init__(self, name, size=22, color_key="muted", parent=None):
        super().__init__(parent)
        self.name, self.px, self.color_key = name, size, color_key
        self.setFixedSize(size + 6, size + 6)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_icon()

    def refresh_icon(self):
        self.setPixmap(theme.icon(self.name, theme.pal()[self.color_key], self.px).pixmap(self.px, self.px))


def fit_to_screen(w: QWidget, pref_w: int, pref_h: int, margin: float = 0.94):
    """작업 표시줄을 뺀 실제 사용 가능 영역 안에 들어오도록 크기를 정하고 가운데에 둔다."""
    from PySide6.QtWidgets import QApplication
    g = QApplication.primaryScreen().availableGeometry()
    width = min(pref_w, int(g.width() * margin))
    height = min(pref_h, int(g.height() * margin))
    w.resize(width, height)
    w.move(g.x() + (g.width() - width) // 2, g.y() + (g.height() - height) // 2)


def hline():
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.pal()['border']}; border: none;")
    return f
