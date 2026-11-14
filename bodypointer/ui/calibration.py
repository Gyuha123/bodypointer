"""가동 범위 측정 (FR-40): 고른 부위를 상하좌우 끝까지 움직이게 해 범위를 잰다.

측정 중에는 커서가 움직이지 않는다 (앱이 좌표를 엔진 대신 이 위젯으로 보냄).
저장하는 것은 범위 좌표 4개뿐이다 (§6).
"""
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from ..mapping import Calibration
from . import theme
from .widgets import IconButton, label

STEPS = [
    ("add", "편한 자세로 가운데에 있어 주세요", 2.0),
    ("arrow_up", "위로 끝까지 움직여 주세요", 2.5),
    ("arrow_down", "아래로 끝까지 움직여 주세요", 2.5),
    ("chevron_left", "왼쪽으로 끝까지 움직여 주세요", 2.5),
    ("chevron_right", "오른쪽으로 끝까지 움직여 주세요", 2.5),
    ("sync", "크게 원을 그리듯 돌려 주세요", 3.5),
]
MIN_SAMPLES = 40


class CalibrationWidget(QWidget):
    finished = Signal(object)
    failed = Signal(str)
    stepChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        self.glyph = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        f = QFont(theme.icon_family())
        f.setPixelSize(84)
        self.glyph.setFont(f)
        self.glyph.setStyleSheet(f"color: {theme.pal()['accent']};")
        self.glyph.setText(theme.glyph("ruler"))
        self.text = label("‘측정 시작’을 누르고 안내에 따라 고른 부위를 움직여 주세요. 끝까지 못 가도 괜찮아요.",
                          "h2", wrap=True, align=Qt.AlignmentFlag.AlignCenter)
        self.step_bar = QProgressBar(textVisible=False)
        self.total = label("", "muted", align=Qt.AlignmentFlag.AlignCenter)
        self.warn = label("", "h2", align=Qt.AlignmentFlag.AlignCenter)
        self.warn.setStyleSheet(f"color: {theme.pal()['danger']};")
        self.start_btn = IconButton("play", "측정 시작", "primary")
        self.start_btn.setMinimumHeight(60)
        self.start_btn.clicked.connect(self.start)
        lay.addStretch()
        for w in (self.glyph, self.text, self.step_bar, self.total, self.warn):
            lay.addWidget(w)
        lay.addWidget(self.start_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()

        self.active = False
        self._timer = QTimer(self, interval=50, timeout=self._tick)
        self.xs, self.ys = [], []
        self._step, self._step_t0, self._last_seen = 0, 0.0, 0.0

    def start(self):
        self.xs, self.ys = [], []
        self._step = -1
        self.active = True
        self.start_btn.setEnabled(False)
        self.warn.setText("")
        self._next_step()
        self._timer.start()

    def cancel(self):
        self.active = False
        self._timer.stop()
        self.start_btn.setEnabled(True)

    def feed(self, pt, t):
        if self.active and pt is not None:
            self.xs.append(pt[0])
            self.ys.append(pt[1])
            self._last_seen = t

    def _next_step(self):
        self._step += 1
        if self._step >= len(STEPS):
            self._finish()
            return
        icon, msg, _ = STEPS[self._step]
        self.glyph.setText(theme.glyph(icon))
        self.text.setText(msg)
        self.total.setText(f"{self._step + 1} / {len(STEPS)}")
        self._step_t0 = time.monotonic()
        self.stepChanged.emit(msg)

    def _tick(self):
        now = time.monotonic()
        dur = STEPS[self._step][2]
        el = now - self._step_t0
        self.step_bar.setValue(int(min(el / dur, 1) * 100))
        self.warn.setText("부위가 카메라에 보이지 않아요" if now - self._last_seen > 0.5 else "")
        if el >= dur:
            self._next_step()

    def _finish(self):
        self.cancel()
        self.total.setText("")
        if len(self.xs) < MIN_SAMPLES:
            self.glyph.setText(theme.glyph("info"))
            self.text.setText("측정이 충분하지 않았어요. 부위가 카메라에 잘 보이게 한 뒤 다시 측정해 주세요.")
            self.start_btn.setText("다시 측정")
            self.failed.emit("samples")
            return
        calib = Calibration.from_samples(self.xs, self.ys)
        sx, sy = calib.span
        self.glyph.setText(theme.glyph("check"))
        self.text.setText(f"측정 완료! 이제 이 범위만 움직여도 화면 끝까지 닿아요.\n(가로 {sx * 100:.0f}% · 세로 {sy * 100:.0f}%)")
        self.start_btn.setText("다시 측정")
        self.finished.emit(calib)
