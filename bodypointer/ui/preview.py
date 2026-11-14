"""카메라 미리보기 + 인식 결과 오버레이 (FR-03), 화면 클릭으로 부위 선택 (FR-10), 영역 지정 (FR-12)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..parts import PARTS, Part
from ..vision import FrameResult, extract_point

POSE_EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
              (23, 25), (25, 27), (24, 26), (26, 28), (27, 31), (28, 32), (27, 29), (28, 30)]
HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
              (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class CameraPreview(QWidget):
    partPicked = Signal(str)
    roiSelected = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.res: FrameResult | None = None
        self.part: Part | None = None
        self.picking = False
        self.roi_mode = False
        self._drag_start = None
        self._drag_now = None
        self.message = ""

    def set_frame(self, res: FrameResult):
        self.res = res
        self.update()

    def _img_rect(self) -> QRectF:
        iw, ih = 640, 480
        if self.res and self.res.image is not None:
            iw, ih = self.res.image.width(), self.res.image.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_widget(self, x, y, r: QRectF) -> QPointF:
        return QPointF(r.x() + x * r.width(), r.y() + y * r.height())

    def _to_norm(self, pt: QPointF, r: QRectF):
        return (pt.x() - r.x()) / r.width(), (pt.y() - r.y()) / r.height()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor(22, 24, 29))
        r = self._img_rect()
        res = self.res
        if res is None or res.image is None:
            p.setPen(QColor(230, 230, 230))
            p.setFont(QFont("Malgun Gothic", 16))
            text = "카메라 연결 대기 중…" if res is None or res.camera_ok else "카메라 연결이 끊겼습니다"
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
            p.end()
            return
        p.drawImage(r, res.image)
        W = lambda x, y: self._to_widget(x, y, r)  # noqa: E731

        # 인식 상태 (얼굴은 성기게, 몸·손은 골격선)
        if res.face:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(120, 200, 255, 140))
            for x, y in res.face[::9]:
                p.drawEllipse(W(x, y), 1.6, 1.6)
        if res.pose:
            p.setPen(QPen(QColor(255, 200, 60, 200), 3))
            for a, b in POSE_EDGES:
                if res.pose[a][2] > 0.5 and res.pose[b][2] > 0.5:
                    p.drawLine(W(*res.pose[a][:2]), W(*res.pose[b][:2]))
        for _, pts in res.hands:
            p.setPen(QPen(QColor(120, 255, 160, 210), 2))
            for a, b in HAND_EDGES:
                p.drawLine(W(*pts[a]), W(*pts[b]))
        if res.roi:
            x, y, w, h = res.roi
            p.setPen(QPen(QColor(0, 230, 255), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(W(x, y), W(x + w, y + h)))

        # 화면에서 고르기 모드: 고를 수 있는 모든 부위를 점으로
        if self.picking:
            p.setFont(QFont("Malgun Gothic", 10))
            for part in PARTS:
                pt = extract_point(res, part)
                if pt:
                    q = W(*pt)
                    p.setPen(QPen(QColor(0, 0, 0), 2))
                    p.setBrush(QColor(255, 235, 0))
                    p.drawEllipse(q, 7, 7)

        # 선택 부위 강조
        if self.part:
            pt = extract_point(res, self.part)
            if pt:
                q = W(*pt)
                p.setPen(QPen(QColor(255, 255, 255), 4))
                p.setBrush(QColor(30, 200, 90))
                p.drawEllipse(q, 13, 13)
            else:
                msg = f"‘{self.part.name}’이(가) 보이지 않아요"
                if self.part.group == "다리":
                    msg += " — 카메라를 낮추거나 조금 멀리 두세요"
                self._badge(p, r, msg, QColor(190, 40, 40, 230))

        if self._drag_start and self._drag_now:
            p.setPen(QPen(QColor(0, 230, 255), 3, Qt.PenStyle.DashLine))
            p.setBrush(QColor(0, 230, 255, 40))
            p.drawRect(QRectF(self._drag_start, self._drag_now).normalized())

        info = f"{res.fps:.0f} fps"
        if res.low_light:
            info += "  ·  ⚠ 너무 어두워요 — 조명을 밝혀주세요"
        p.setFont(QFont("Malgun Gothic", 12, QFont.Weight.Bold))
        p.setPen(QColor(255, 220, 80) if res.low_light else QColor(255, 255, 255))
        p.drawText(r.adjusted(10, 8, -10, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, info)
        if self.message:
            self._badge(p, r, self.message, QColor(20, 60, 140), bottom=True)
        p.end()

    def _badge(self, p, r, text, color, bottom=False):
        p.setFont(QFont("Malgun Gothic", 12, QFont.Weight.Bold))
        br = QRectF(r.x() + 12, r.bottom() - 52 if bottom else r.y() + 40, r.width() - 24, 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(br, 20, 20)
        p.setPen(QColor(255, 255, 255))
        p.drawText(br, Qt.AlignmentFlag.AlignCenter, text)

    # ---------- 마우스 (Dwell 클릭/드래그도 OS 마우스 이벤트로 들어온다) ----------
    def mousePressEvent(self, e):
        if self.roi_mode:
            self._drag_start = self._drag_now = e.position()

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            self._drag_now = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        r = self._img_rect()
        if self.roi_mode and self._drag_start is not None:
            a, b = self._to_norm(self._drag_start, r), self._to_norm(e.position(), r)
            self._drag_start = self._drag_now = None
            x0, x1 = sorted((min(max(a[0], 0), 1), min(max(b[0], 0), 1)))
            y0, y1 = sorted((min(max(a[1], 0), 1), min(max(b[1], 0), 1)))
            if x1 - x0 > 0.02 and y1 - y0 > 0.02:
                self.roiSelected.emit((x0, y0, x1 - x0, y1 - y0))
            self.update()
            return
        if self.picking and self.res:
            best, best_d = None, 45.0
            for part in PARTS:
                pt = extract_point(self.res, part)
                if pt:
                    d = math.dist((e.position().x(), e.position().y()), (self._to_widget(*pt, r).x(), self._to_widget(*pt, r).y()))
                    if d < best_d:
                        best, best_d = part, d
            if best:
                self.partPicked.emit(best.id)
