"""디자인 시스템: 색 토큰, 글꼴, 아이콘, 스타일시트.

- 기본: 밝은 중성색 + 인디고 강조색 하나. 대비 4.5:1 이상 (NFR-10).
- 고대비: 검정 바탕 + 흰 글자 + 노랑 강조.
- 떠 있는 패널(툴바·키보드·메뉴): 어떤 앱 위에서도 잘 보이는 어두운 패널.
- 아이콘: Windows 기본 'Segoe Fluent Icons' 글리프 (Windows 10은 'Segoe MDL2 Assets').
- 터치 타깃 최소 48px (FR-61).
"""
from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap

LIGHT = {
    "bg": "#F4F5F7", "surface": "#FFFFFF", "sunken": "#EEF0F3", "border": "#E3E6EB", "border_strong": "#C9CED6",
    "text": "#111827", "muted": "#5F6876", "accent": "#4F46E5", "accent_hover": "#4338CA", "accent_soft": "#EEF0FF",
    "on_accent": "#FFFFFF", "ok": "#047857", "ok_soft": "#E7F7EF", "warn": "#B45309", "warn_soft": "#FEF5E6",
    "danger": "#C62828", "danger_soft": "#FDECEC", "focus": "#4F46E5",
}
HIGH_CONTRAST = {
    "bg": "#000000", "surface": "#000000", "sunken": "#141414", "border": "#FFFFFF", "border_strong": "#FFFFFF",
    "text": "#FFFFFF", "muted": "#FFFF66", "accent": "#FFFF00", "accent_hover": "#FFE600", "accent_soft": "#333300",
    "on_accent": "#000000", "ok": "#00FF7F", "ok_soft": "#003318", "warn": "#FFB000", "warn_soft": "#332300",
    "danger": "#FF6B6B", "danger_soft": "#330000", "focus": "#FFFF00",
}
# 다른 앱 위에 떠 있는 패널
OVERLAY = {
    "bg": "#16181D", "key": "#262A33", "key_hover": "#343947", "key_pressed": "#4F46E5", "text": "#F5F6F8",
    "muted": "#A1A8B5", "accent": "#7C83F7", "border": "#2F3440", "ok": "#34D399", "danger": "#F87171",
}

ICONS = {
    "home": 0xE80F, "play": 0xE768, "pause": 0xE769, "keyboard": 0xE765, "save": 0xE74E, "settings": 0xE713,
    "up": 0xE70E, "down": 0xE70D, "sync": 0xE895, "switch": 0xE8AB, "copy": 0xE8C8, "paste": 0xE77F,
    "cut": 0xE8C6, "undo": 0xE7A7, "redo": 0xE7A6, "select_all": 0xE8B3, "back": 0xE72B, "refresh": 0xE72C,
    "mouse": 0xE962, "person": 0xE77B, "check": 0xE8FB, "close": 0xE711, "delete": 0xE74D, "chart": 0xE9D2,
    "pulse": 0xE9D9, "health": 0xE95E, "touch": 0xE7C9, "bulb": 0xEA80, "emoji": 0xE76E, "star": 0xE734,
    "star_fill": 0xE735, "camera": 0xE722, "history": 0xE81C, "zoom_in": 0xE8A3, "zoom_out": 0xE71F,
    "arrow_up": 0xE74A, "arrow_down": 0xE74B, "chevron_left": 0xE76B, "chevron_right": 0xE76C, "ruler": 0xECC6,
    "download": 0xE896, "power": 0xE7E8, "apps": 0xF0E2, "info": 0xE946, "add": 0xE710, "search": 0xE721,
    "enter": 0xE751, "backspace": 0xE750, "tab": 0xE7FD, "shift": 0xE752, "globe": 0xE774, "hide": 0xED1A,
    "lock": 0xE72E, "minus": 0xE738, "target": 0xE7B7, "wizard": 0xE82D, "clutch": 0xE72E, "space": 0xE75D,
}

_state = {"hc": False, "large": True}


def configure(high_contrast: bool, large: bool):
    _state["hc"], _state["large"] = high_contrast, large


def pal() -> dict:
    return HIGH_CONTRAST if _state["hc"] else LIGHT


def base_size() -> int:
    return 17 if _state["large"] else 15


def icon_family() -> str:
    fams = set(QFontDatabase.families())
    return "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"


def ui_font(size: int | None = None, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Malgun Gothic"])
    f.setPixelSize(size or base_size())
    f.setWeight(weight)
    return f


@lru_cache(maxsize=512)
def _icon_cached(name: str, color: str, size: int, dpr: float) -> QIcon:
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    f = QFont(icon_family())
    f.setPixelSize(int(size * 0.92))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(ICONS.get(name, ICONS["info"])))
    p.end()
    return QIcon(px)


def icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    scr = QGuiApplication.primaryScreen()
    dpr = scr.devicePixelRatio() if scr else 1.0
    return _icon_cached(name, color or pal()["text"], size, dpr)


def glyph(name: str) -> str:
    return chr(ICONS.get(name, ICONS["info"]))


def stylesheet() -> str:
    c = pal()
    fs = base_size()
    bw = 2 if _state["hc"] else 1
    return f"""
    QWidget {{ color: {c['text']}; font-size: {fs}px; }}
    QMainWindow, QDialog, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['bg']}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="h1"] {{ font-size: {fs + 13}px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: {fs + 4}px; font-weight: 650; }}
    QLabel[role="muted"] {{ color: {c['muted']}; }}
    QLabel[role="small"] {{ color: {c['muted']}; font-size: {fs - 2}px; }}
    QLabel[role="big"] {{ font-size: {fs + 20}px; font-weight: 700; }}

    QFrame[role="card"] {{ background: {c['surface']}; border: {bw}px solid {c['border']}; border-radius: 16px; }}
    QFrame[role="sidebar"] {{ background: {c['surface']}; border: none; border-right: {bw}px solid {c['border']}; }}
    QFrame[role="tile"] {{ background: {c['sunken']}; border: none; border-radius: 12px; }}
    QFrame[tone="ok"] {{ background: {c['ok_soft']}; border-color: {c['ok_soft']}; }}
    QFrame[tone="warn"] {{ background: {c['warn_soft']}; border-color: {c['warn_soft']}; }}
    QFrame[tone="care"] {{ background: {c['danger_soft']}; border-color: {c['danger_soft']}; }}
    QFrame[tone="accent"] {{ background: {c['accent_soft']}; border-color: {c['accent_soft']}; }}

    QPushButton, QToolButton {{
        min-height: 48px; padding: 6px 16px; border-radius: 12px; font-weight: 600;
        background: {c['surface']}; border: {bw}px solid {c['border_strong']};
    }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
    QPushButton:focus, QToolButton:focus {{ outline: none; border: 2px solid {c['focus']}; }}
    QPushButton[variant="primary"], QToolButton[variant="primary"],
    QPushButton:checked, QToolButton:checked {{
        background: {c['accent']}; color: {c['on_accent']}; border-color: {c['accent']};
    }}
    QPushButton[variant="primary"]:hover, QToolButton[variant="primary"]:hover {{ background: {c['accent_hover']}; }}
    QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
    QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {c['sunken']}; }}
    QPushButton[variant="danger"], QToolButton[variant="danger"] {{ color: {c['danger']}; border-color: {c['danger']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; background: {c['sunken']}; }}

    QToolButton[role="nav"] {{
        text-align: left; border: none; border-radius: 12px; background: transparent; padding: 8px 14px;
        min-height: 52px; font-weight: 600; color: {c['muted']};
    }}
    QToolButton[role="nav"]:hover {{ background: {c['sunken']}; color: {c['text']}; }}
    QToolButton[role="nav"]:checked {{ background: {c['accent_soft']}; color: {c['accent']}; }}

    QToolButton[role="chip"] {{ border-radius: 14px; min-height: 56px; padding: 6px 14px; font-weight: 600;
        background: {c['surface']}; border: {max(bw, 1)}px solid {c['border_strong']}; }}
    QToolButton[role="chip"]:checked {{ background: {c['accent']}; color: {c['on_accent']}; border-color: {c['accent']}; }}

    QComboBox {{ min-height: 48px; padding: 4px 14px; border: {bw}px solid {c['border_strong']}; border-radius: 12px;
        background: {c['surface']}; }}
    QComboBox::drop-down {{ border: none; width: 32px; }}
    QComboBox QAbstractItemView {{ background: {c['surface']}; border: {bw}px solid {c['border_strong']};
        selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; outline: none; }}
    QComboBox QAbstractItemView::item {{ min-height: 48px; padding: 0 12px; }}
    QLineEdit {{ min-height: 48px; padding: 0 14px; border: {bw}px solid {c['border_strong']}; border-radius: 12px;
        background: {c['surface']}; }}

    QCheckBox {{ spacing: 12px; min-height: 48px; background: transparent; }}
    QCheckBox::indicator {{ width: 46px; height: 26px; border-radius: 13px; background: {c['border_strong']};
        border: {bw}px solid {c['border_strong']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}

    QSlider {{ min-height: 48px; background: transparent; }}
    QSlider::groove:horizontal {{ height: 6px; background: {c['sunken']}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ width: 26px; height: 26px; margin: -10px 0; border-radius: 13px;
        background: {c['surface']}; border: 3px solid {c['accent']}; }}

    QProgressBar {{ min-height: 10px; max-height: 10px; border: none; border-radius: 5px; background: {c['sunken']}; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}

    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ width: 14px; background: transparent; margin: 4px; }}
    QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 5px; min-height: 40px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QToolTip {{ background: {c['text']}; color: {c['bg']}; border: none; padding: 6px 10px; border-radius: 8px; }}
    """


def overlay_stylesheet() -> str:
    o = OVERLAY
    fs = base_size()
    return f"""
    QWidget#panel {{ background: {o['bg']}; border: 1px solid {o['border']}; border-radius: 20px; }}
    QWidget {{ color: {o['text']}; font-size: {fs}px; }}
    QLabel {{ background: transparent; }}
    QLabel[role="muted"] {{ color: {o['muted']}; font-size: {fs - 2}px; }}
    QToolButton, QPushButton {{
        background: {o['key']}; border: none; border-radius: 12px; padding: 4px 8px; font-weight: 600;
        color: {o['text']};
    }}
    QToolButton:hover, QPushButton:hover {{ background: {o['key_hover']}; }}
    QToolButton:checked, QToolButton[variant="primary"], QPushButton[variant="primary"] {{
        background: {o['key_pressed']}; color: white; }}
    QToolButton[variant="ghost"] {{ background: transparent; color: {o['muted']}; }}
    QToolButton[variant="ghost"]:hover {{ background: {o['key']}; color: {o['text']}; }}
    QToolButton[role="suggest"] {{ background: transparent; border: 1px solid {o['border']}; font-size: {fs + 2}px; }}
    QToolButton[role="suggest"]:hover {{ background: {o['key_hover']}; }}
    QToolButton[role="key"] {{ font-size: {fs + 6}px; }}
    QToolButton[role="danger"] {{ color: {o['danger']}; }}
    QFrame[role="divider"] {{ background: {o['border']}; max-width: 1px; min-width: 1px; }}
    """
