"""가벼운 차트 (QPainter). 디자인 시스템 색을 그대로 쓴다."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


class _Chart(QWidget):
    empty_text = "아직 기록이 없어요"

    def __init__(self, height=220, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.data = []

    def set_data(self, data):
        self.data = list(data)
        self.update()

    def _empty(self, p):
        c = theme.pal()
        p.setPen(QColor(c["muted"]))
        p.setFont(theme.ui_font(theme.base_size() - 1))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)


class BarChart(_Chart):
    """세로 막대. data: [(라벨, 값)]"""

    def __init__(self, fmt="{:.0f}", height=220, parent=None):
        super().__init__(height, parent)
        self.fmt = fmt

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal()
        if not self.data or max(v for _, v in self.data) <= 0:
            self._empty(p)
            return
        top, bottom, left = 24, 30, 6
        area = QRectF(left, top, self.width() - 2 * left, self.height() - top - bottom)
        mx = max(v for _, v in self.data)
        n = len(self.data)
        slot = area.width() / n
        bw = min(slot * 0.62, 40)
        p.setFont(theme.ui_font(theme.base_size() - 4))
        for i, (lab, v) in enumerate(self.data):
            h = area.height() * (v / mx) if mx else 0
            x = area.x() + slot * i + (slot - bw) / 2
            r = QRectF(x, area.bottom() - h, bw, max(h, 2))
            last = i == n - 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["accent"] if last else c["accent_soft"] if not theme._state["hc"] else c["muted"]))
            if not last and not theme._state["hc"]:
                p.setBrush(QColor(c["accent"]).lighter(165))
            p.drawRoundedRect(r, 6, 6)
            if lab:
                p.setPen(QColor(c["muted"]))
                cx = area.x() + slot * i + slot / 2
                p.drawText(QRectF(cx - 40, area.bottom() + 4, 80, bottom - 4),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, lab)
            if v > 0 and (last or n <= 10):
                p.setPen(QColor(c["text"]))
                p.drawText(QRectF(area.x() + slot * i, r.top() - 20, slot, 18), Qt.AlignmentFlag.AlignCenter,
                           self.fmt.format(v))
        p.end()


class HBarChart(_Chart):
    """가로 막대 순위. data: [(라벨, 값)]"""

    def __init__(self, fmt="{:.0f}회", parent=None):
        super().__init__(60, parent)
        self.fmt = fmt

    def set_data(self, data):
        super().set_data(data)
        self.setMinimumHeight(max(60, 40 * len(self.data)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal()
        if not self.data:
            self._empty(p)
            return
        mx = max(v for _, v in self.data) or 1
        label_w, val_w, row = 130, 70, 40
        p.setFont(theme.ui_font(theme.base_size() - 1))
        for i, (lab, v) in enumerate(self.data):
            y = i * row
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(0, y, label_w - 10, row), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, lab)
            track = QRectF(label_w, y + row / 2 - 7, self.width() - label_w - val_w, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["sunken"]))
            p.drawRoundedRect(track, 7, 7)
            p.setBrush(QColor(c["accent"]))
            p.drawRoundedRect(QRectF(track.x(), track.y(), max(track.width() * v / mx, 14), 14), 7, 7)
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(self.width() - val_w, y, val_w, row),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.fmt.format(v))
        p.end()


class LineChart(_Chart):
    """꺾은선 + 옅은 영역. data: [(라벨, 값)]"""

    def __init__(self, fmt="{:.0f}", height=220, parent=None):
        super().__init__(height, parent)
        self.fmt = fmt

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal()
        if len(self.data) < 2:
            self.empty_text = "기록이 2번 이상 쌓이면 추이가 보여요" if self.data else "아직 기록이 없어요"
            self._empty(p)
            return
        top, bottom, side = 26, 30, 18
        area = QRectF(side, top, self.width() - 2 * side, self.height() - top - bottom)
        vals = [v for _, v in self.data]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.15
        lo, hi = lo - pad, hi + pad
        n = len(self.data)
        pts = [QPointF(area.x() + area.width() * i / (n - 1), area.bottom() - area.height() * (v - lo) / (hi - lo))
               for i, (_, v) in enumerate(self.data)]
        path = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):
            mid = (a.x() + b.x()) / 2
            path.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), area.bottom())
        fill.lineTo(pts[0].x(), area.bottom())
        fill.closeSubpath()
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        col = QColor(c["accent"])
        col.setAlpha(60)
        grad.setColorAt(0, col)
        col.setAlpha(0)
        grad.setColorAt(1, col)
        p.fillPath(fill, grad)
        p.setPen(QPen(QColor(c["accent"]), 3))
        p.drawPath(path)
        p.setFont(theme.ui_font(theme.base_size() - 4))
        step = max(1, n // 7)
        for i, (pt, (lab, v)) in enumerate(zip(pts, self.data)):
            if i % step == 0 or i == n - 1:
                p.setPen(QColor(c["muted"]))
                p.drawText(QRectF(pt.x() - 40, area.bottom() + 6, 80, 20), Qt.AlignmentFlag.AlignCenter, lab)
        p.setPen(QPen(QColor(c["surface"]), 3))
        p.setBrush(QColor(c["accent"]))
        p.drawEllipse(pts[-1], 6, 6)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(pts[-1].x() - 60, pts[-1].y() - 26, 120, 20), Qt.AlignmentFlag.AlignCenter,
                   self.fmt.format(vals[-1]))
        p.end()


class HeatMap(_Chart):
    """화면 모양의 클릭 위치 분포. data: {"x,y": count} (16×9 격자)"""

    def __init__(self, gw=16, gh=9, parent=None):
        super().__init__(200, parent)
        self.gw, self.gh = gw, gh
        self.data = {}

    def set_data(self, data):
        self.data = dict(data)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal()
        w = min(self.width(), self.height() * self.gw / self.gh)
        h = w * self.gh / self.gw
        ox, oy = (self.width() - w) / 2, (self.height() - h) / 2
        p.setPen(QPen(QColor(c["border_strong"]), 2))
        p.setBrush(QColor(c["sunken"]))
        p.drawRoundedRect(QRectF(ox, oy, w, h), 10, 10)
        if not self.data:
            self._empty(p)
            return
        mx = max(self.data.values())
        cw, ch = w / self.gw, h / self.gh
        p.setPen(Qt.PenStyle.NoPen)
        for key, v in self.data.items():
            x, y = (int(s) for s in key.split(","))
            col = QColor(c["accent"])
            col.setAlpha(int(40 + 215 * (v / mx)))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(ox + x * cw + 2, oy + y * ch + 2, cw - 4, ch - 4), 4, 4)
        p.end()
