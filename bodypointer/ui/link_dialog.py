"""제스처에 연결할 링크 만들기. 자주 쓰는 사이트는 버튼으로 고를 수 있어 머무르기만으로도 쓸 수 있다."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QLineEdit, QSizePolicy, QVBoxLayout

from ..actions import LINK_PRESETS, make_link_action, normalize_url
from .widgets import IconButton, fit_to_screen, label


class LinkDialog(QDialog):
    def __init__(self, gesture_label: str, open_keyboard, parent=None):
        super().__init__(parent)
        self.setWindowTitle("링크 연결")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        fit_to_screen(self, 760, 620)
        self.action_id = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        lay.addWidget(label(f"‘{gesture_label}’을(를) 하면 열 사이트", "h1"))
        lay.addWidget(label("아래에서 고르거나 주소를 직접 입력하세요. 기본 브라우저로 열려요.", "muted", wrap=True))

        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (name, url) in enumerate(LINK_PRESETS):
            b = IconButton("globe", name)
            b.setProperty("role", "chip")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, n=name, u=url: self._fill(n, u))
            grid.addWidget(b, i // 3, i % 3)
        lay.addLayout(grid)

        lay.addWidget(label("이름", "h2"))
        self.name = QLineEdit(placeholderText="예: 유튜브, 우리 병원 예약")
        lay.addWidget(self.name)
        lay.addWidget(label("주소", "h2"))
        self.url = QLineEdit(placeholderText="예: youtube.com")
        self.url.textChanged.connect(lambda _: self.error.setText(""))
        lay.addWidget(self.url)
        self.error = label("", "muted")
        self.error.setStyleSheet("color: #C62828;")
        lay.addWidget(self.error)
        lay.addStretch()

        kb = IconButton("keyboard", "키보드로 입력", "ghost")
        kb.clicked.connect(lambda: (self.url.setFocus() if not self.url.text() else None, open_keyboard()))
        cancel = IconButton("close", "취소")
        cancel.clicked.connect(self.reject)
        ok = IconButton("check", "연결하기", "primary")
        ok.clicked.connect(self._accept)
        row = QGridLayout()
        row.addWidget(kb, 0, 0)
        row.setColumnStretch(1, 1)
        row.addWidget(cancel, 0, 2)
        row.addWidget(ok, 0, 3)
        lay.addLayout(row)
        self.url.setFocus()

    def _fill(self, name, url):
        self.name.setText(name)
        self.url.setText(url)

    def _accept(self):
        url = normalize_url(self.url.text())
        if not url:
            self.error.setText("열 수 있는 웹 주소가 아니에요. 예: youtube.com 또는 https://www.naver.com")
            return
        self.action_id = make_link_action(self.name.text(), url)
        self.accept()
