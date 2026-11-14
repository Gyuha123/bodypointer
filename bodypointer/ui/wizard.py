"""처음 실행 마법사 (FR-63): 안내·동의 → 카메라 → 부위 → 가동 범위 측정 → 연습.

마법사도 머무르기만으로 진행할 수 있다 (자체 화면 위에서는 일시정지 중에도 머무르기 클릭이 동작).
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QProgressBar, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..parts import get_part
from .calibration import CalibrationWidget
from .preview import CameraPreview
from .widgets import Card, IconButton, IconLabel, ToggleRow, label

QUICK_PARTS = ["nose", "chin", "r_index", "l_index", "r_palm", "l_palm",
               "r_shoulder", "l_shoulder", "r_elbow", "l_elbow", "r_foot", "l_foot"]

POINTS = [
    ("lock", "영상은 이 컴퓨터 안에서만", "카메라 영상은 메모리에서만 처리하고 저장하거나 인터넷으로 보내지 않아요."),
    ("save", "저장하는 것", "설정값, 움직이는 범위, 사용 통계, 키보드로 쓴 단어의 빈도만 저장해요. 설정에서 한 번에 지울 수 있어요."),
    ("health", "참고용 보조 도구", "의료기기가 아니며 진단을 하지 않아요."),
    ("touch", "머무르면 클릭", "버튼 위에 포인터를 잠시 두면 둥근 게이지가 차고, 다 차면 눌려요."),
]


class SetupWizard(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("BodyPointer 시작하기")
        self.resize(1180, 860)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.setSpacing(16)
        self.step_label = label("", "small")
        self.title = label("", "h1")
        self.progress = QProgressBar(textVisible=False, maximum=5)
        lay.addWidget(self.step_label)
        lay.addWidget(self.title)
        lay.addWidget(self.progress)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.previews: list[CameraPreview] = []
        self.pages = [self._consent_page(), self._camera_page(), self._part_page(), self._calib_page(),
                      self._practice_page()]
        self.titles = ["BodyPointer에 오신 걸 환영해요", "카메라를 확인할게요", "마우스로 쓸 부위를 고르세요",
                       "움직일 수 있는 범위를 잴게요", "연습해 볼까요?"]
        for p in self.pages:
            self.stack.addWidget(p)

        nav = QHBoxLayout()
        self.back_btn = IconButton("chevron_left", "이전")
        self.next_btn = IconButton("chevron_right", "다음", "primary")
        self.next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        for b in (self.back_btn, self.next_btn):
            b.setMinimumSize(160, 60)
        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)
        self._go(0 if not app.state.get("consent") else 1)

    # ---------------- 페이지 ----------------
    def _consent_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(12)
        l.addWidget(label("웹캠으로 몸의 한 부위를 마우스처럼 쓰게 해 드려요. 시작하기 전에 꼭 알아 두실 것들이에요.",
                          "muted", wrap=True))
        grid = QGridLayout()
        grid.setSpacing(14)
        for i, (ic, title, body) in enumerate(POINTS):
            c = Card(margins=20)
            h = QHBoxLayout()
            h.addWidget(IconLabel(ic, 28, "accent"), alignment=Qt.AlignmentFlag.AlignTop)
            t = QVBoxLayout()
            t.addWidget(label(title, "h2"))
            t.addWidget(label(body, "muted", wrap=True))
            h.addLayout(t, 1)
            c.add(h)
            grid.addWidget(c, i // 2, i % 2)
        l.addLayout(grid)
        l.addStretch()
        self.consent = ToggleRow("위 내용을 확인했고 동의해요", "")
        self.consent.toggled.connect(lambda _: self._update_nav())
        l.addWidget(self.consent)
        return w

    def _preview(self):
        p = CameraPreview()
        p.part = get_part(self.app.settings.part_id)
        self.previews.append(p)
        return p

    def _camera_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        self.cam_combo = QComboBox()
        for i in self.app.cameras:
            self.cam_combo.addItem(self.app.camera_label(i), i)
        self.cam_combo.setCurrentIndex(max(0, self.cam_combo.findData(self.app.settings.camera_index)))
        self.cam_combo.activated.connect(lambda _: self.app.change_camera(self.cam_combo.currentData()))
        l.addWidget(self.cam_combo)
        l.addWidget(self._preview(), 1)
        l.addWidget(label("얼굴과 쓰려는 몸 부위가 화면 안에 들어오게 카메라 위치를 맞춰 주세요. "
                          "‘너무 어두워요’가 보이면 조명을 밝혀 주세요.", "muted", wrap=True))
        return w

    def _part_page(self):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setSpacing(18)
        left = QVBoxLayout()
        grid = QGridLayout()
        grid.setSpacing(8)
        self.part_buttons = {}
        for i, pid in enumerate(QUICK_PARTS):
            b = IconButton("person", get_part(pid).name, checkable=True, icon_size=16)
            b.setProperty("role", "chip")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, p=pid: self._pick(p))
            grid.addWidget(b, i // 2, i % 2)
            self.part_buttons[pid] = b
        left.addLayout(grid)
        left.addWidget(label("눈·무릎·발목·직접 지정한 영역 등 더 많은 부위는 설정 창의 ‘부위’에서 고를 수 있어요.",
                             "muted", wrap=True))
        left.addStretch()
        l.addLayout(left, 2)
        right = QVBoxLayout()
        right.addWidget(self._preview(), 1)
        right.addWidget(label("초록 점이 고른 부위예요. 처음이라면 ‘코끝’을 추천해요.", "muted", wrap=True))
        l.addLayout(right, 3)
        self._sync_part_buttons()
        return w

    def _calib_page(self):
        w = QWidget()
        l = QHBoxLayout(w)
        self.calib = CalibrationWidget()
        self.calib.finished.connect(self._calibrated)
        self.calib.stepChanged.connect(self.app.say)
        l.addWidget(self.calib, 2)
        l.addWidget(self._preview(), 3)
        self.calib_done = False
        return w

    def _practice_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(label("포인터를 과녁 위에 머물러 보세요. 게이지가 다 차면 눌려요.", "muted", wrap=True))
        area = QWidget()
        g = QGridLayout(area)
        self.targets = []
        for (r, c) in [(0, 0), (0, 2), (2, 1), (1, 1)]:
            b = IconButton("add", "", icon_size=40)
            b.setFixedSize(170, 120)
            b.clicked.connect(lambda _=False, btn=b: self._hit(btn))
            g.addWidget(b, r, c, alignment=Qt.AlignmentFlag.AlignCenter)
            self.targets.append(b)
        l.addWidget(area, 1)
        self.practice_label = label("", "h2", align=Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self.practice_label)
        return w

    # ---------------- 동작 ----------------
    def _pick(self, pid):
        self.app.set_part(pid)
        self._sync_part_buttons()

    def _sync_part_buttons(self):
        cur = self.app.settings.part_id
        for pid, b in self.part_buttons.items():
            b.setChecked(pid == cur)
        for p in self.previews:
            p.part = get_part(cur)

    def _calibrated(self, calib):
        self.app.apply_calibration(calib)
        self.calib_done = True
        self._update_nav()

    def _hit(self, btn):
        btn.set_icon_name("check")
        btn.set_variant("primary")
        btn.setEnabled(False)
        left = sum(b.isEnabled() for b in self.targets)
        self.practice_label.setText("모두 성공! ‘완료’를 눌러 시작하세요." if not left else f"좋아요! {left}개 남았어요.")
        if not left:
            self.app.say("잘하셨어요. 연습을 마쳤습니다.")

    def _go(self, idx):
        idx = max(0, min(idx, len(self.pages) - 1))
        if self.stack.currentIndex() == 3:
            self.calib.cancel()
        self.stack.setCurrentIndex(idx)
        self.step_label.setText(f"{idx + 1} / {len(self.pages)} 단계")
        self.title.setText(self.titles[idx])
        self.progress.setValue(idx + 1)
        self.app.calibrator = self.calib if idx == 3 else None
        self._update_nav()

    def _next(self):
        i = self.stack.currentIndex()
        if i == 0:
            self.app.state["consent"] = True
            self.app.save_state()
        if i == len(self.pages) - 1:
            self.accept()
            return
        self._go(i + 1)

    def _update_nav(self):
        i = self.stack.currentIndex()
        self.back_btn.setVisible(i > (0 if not self.app.state.get("consent") else 1))
        last = i == len(self.pages) - 1
        self.next_btn.setText("완료" if last else "다음")
        self.next_btn.set_icon_name("check" if last else "chevron_right")
        if i == 0:
            self.next_btn.setEnabled(self.consent.check.isChecked())
        elif i == 3:
            self.next_btn.setEnabled(True)
            self.next_btn.setText("다음" if self.calib_done else "건너뛰기")
        else:
            self.next_btn.setEnabled(True)

    def done(self, r):
        self.calib.cancel()
        self.app.calibrator = None
        self.previews.clear()
        if r == QDialog.DialogCode.Accepted:
            self.app.state["wizard_done"] = True
            self.app.save_state()
        super().done(r)
