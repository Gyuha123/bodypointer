"""키보드 바: 머무르기로 치는 화상 키보드 (FR-35) + 자동완성.

- 두벌식 한글 / 영문 / 숫자·기호, 편집 키(복사·붙여넣기 등)
- 글자는 유니코드로 직접 보내므로 상대 앱의 한/영 상태와 상관없이 정확히 입력된다.
- 조합 중인 글자는 '지우고 다시 쓰기'로 갱신한다 (가 → 각 → 갑시: ⌫ 후 재입력).
- 맨 위 줄: 자동완성 (초성 'ㅇㄴㅎ' → 안녕하세요, 앞부분 일치, 다음 단어 예측).
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from .. import winmouse
from ..actions import EDIT_KEYS, get_action
from ..hangul import HangulComposer, is_consonant, is_vowel
from .toolbar import FloatingPanel
from .widgets import IconButton, label

KO_ROWS = ["ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ", "ㅁㄴㅇㄹㅎㅗㅓㅏㅣ", "ㅋㅌㅊㅍㅠㅜㅡ"]
KO_SHIFT = {"ㅂ": "ㅃ", "ㅈ": "ㅉ", "ㄷ": "ㄸ", "ㄱ": "ㄲ", "ㅅ": "ㅆ", "ㅐ": "ㅒ", "ㅔ": "ㅖ"}
EN_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
NUM_ROWS = ["1234567890", "-/:;()₩&@", "\"'!?%+=*"]
SEPARATORS = set(" .,?!\n\t")
EDIT_LABELS = {"undo": "되돌리기", "select_all": "전체선택", "delete": "삭제", "left": "", "right": "", "up": "",
               "down": ""}
ARROWS = {"left", "right", "up", "down"}


class KeyboardBar(FloatingPanel):
    closed = Signal()
    actionRequested = Signal(str)          # 편집 키 → 앱이 실행·기록
    wordTyped = Signal(str, object)        # (단어, 앞 단어)
    charsTyped = Signal(int, int)          # (입력한 글자 수, 자동완성으로 아낀 글자 수)

    def __init__(self, predictor):
        super().__init__("keyboard")
        self.predictor = predictor
        self.mode, self.shift = "ko", False
        self.composer = HangulComposer()
        self.shown = ""            # 상대 앱에 보이는 조합 중 글자
        self.word = ""             # 현재 단어에서 확정된 부분
        self.prev_word = None

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        # 자동완성 줄
        top = QHBoxLayout()
        top.setSpacing(8)
        self.preview = label("", "muted")
        self.preview.setMinimumWidth(150)
        top.addWidget(self.preview)
        self.sugg_buttons = []
        for _ in range(5):
            b = QToolButton()
            b.setProperty("role", "suggest")
            b.setMinimumHeight(60)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, btn=b: self._pick_suggestion(btn.text()))
            top.addWidget(b, 1)
            self.sugg_buttons.append(b)
        hide = IconButton("hide", "닫기", overlay=True)
        hide.setProperty("variant", "ghost")
        hide.setFixedHeight(60)
        hide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hide.clicked.connect(self._close)
        top.addWidget(hide)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.keys_host = QWidget()
        self.keys_grid = QGridLayout(self.keys_host)
        self.keys_grid.setContentsMargins(0, 0, 0, 0)
        self.keys_grid.setSpacing(7)
        body.addWidget(self.keys_host, 1)
        div = QFrame()
        div.setProperty("role", "divider")
        body.addWidget(div)
        edit = QGridLayout()
        edit.setSpacing(7)
        for i, aid in enumerate(EDIT_KEYS):
            a = get_action(aid)
            b = IconButton(a.icon, EDIT_LABELS.get(aid, a.label), under=aid not in ARROWS, overlay=True,
                           icon_size=22 if aid in ARROWS else 20)
            b.setToolTip(a.label)
            b.setFixedSize(104, 72)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, x=aid: self._edit(x))
            edit.addWidget(b, i // 3, i % 3)
        body.addLayout(edit)
        root.addLayout(body)
        self._build_keys()
        self._refresh_suggestions()

    # ---------------- 배치 ----------------
    def place(self, right_margin: int):
        g = QApplication.primaryScreen().availableGeometry()
        w = min(g.width() - right_margin - 8, 1500)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move(g.left() + max(0, (g.width() - right_margin - w) // 2), g.bottom() - self.height() + 1)

    def _key(self, text, w=1.0, role="key", cb=None, icon=None):
        if icon:
            b = IconButton(icon, text, overlay=True, icon_size=20)
        else:
            b = QToolButton()
            b.setText(text)
            b.setProperty("role", role)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumSize(24, 72)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        b.clicked.connect(cb or (lambda _=False, t=text: self._char(t)))
        return b

    COLS = 24     # 균등한 24칸 격자. 글자 키 = 2칸

    def _build_keys(self):
        while self.keys_grid.count():
            w = self.keys_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        rows = {"ko": KO_ROWS, "en": EN_ROWS, "num": NUM_ROWS}[self.mode]
        g = self.keys_grid
        for c in range(self.COLS):
            g.setColumnStretch(c, 1)

        def letter(ch):
            if self.shift and self.mode == "ko":
                return KO_SHIFT.get(ch, ch)
            if self.shift and self.mode == "en":
                return ch.upper()
            return ch

        # 1줄: 글자 10개 + 지우기
        col = 0
        for ch in rows[0]:
            g.addWidget(self._key(letter(ch)), 0, col, 1, 2)
            col += 2
        g.addWidget(self._key("지우기", cb=self._backspace, icon="backspace"), 0, col, 1, self.COLS - col)
        # 2줄: 반 칸 들여 글자 9개 + 엔터
        col = 1
        for ch in rows[1]:
            g.addWidget(self._key(letter(ch)), 1, col, 1, 2)
            col += 2
        g.addWidget(self._key("엔터", cb=self._enter, icon="enter"), 1, col, 1, self.COLS - col)
        # 3줄: Shift + 글자 + 문장부호
        sh = self._key("Shift", cb=self._toggle_shift, icon="shift")
        if self.shift:
            sh.setProperty("variant", "primary")
            sh.refresh_icon()
        g.addWidget(sh, 2, 0, 1, 4)
        col = 4
        for ch in rows[2]:
            g.addWidget(self._key(letter(ch)), 2, col, 1, 2)
            col += 2
        for ch in ".,?!"[: (self.COLS - col) // 2]:
            g.addWidget(self._key(ch), 2, col, 1, 2)
            col += 2
        # 4줄: 한/영, 숫자, 띄어쓰기
        lang = "영문" if self.mode == "ko" else "한글"
        g.addWidget(self._key(lang, cb=self._toggle_lang, icon="globe"), 3, 0, 1, 4)
        g.addWidget(self._key("123" if self.mode != "num" else "가나다", cb=self._toggle_num), 3, 4, 1, 4)
        g.addWidget(self._key("띄어쓰기", cb=self._space, icon="space"), 3, 8, 1, self.COLS - 8)

    # ---------------- 입력 ----------------
    def _write(self, committed: str, composing: str):
        winmouse.backspace(len(self.shown))
        winmouse.type_text(committed + composing)
        self.shown = composing
        for ch in committed:
            if ch in SEPARATORS:
                self._finish_word()
            else:
                self.word += ch
        if committed:
            self.charsTyped.emit(len(committed), 0)
        self._refresh_suggestions()

    def _char(self, ch):
        if self.mode == "ko" and (is_consonant(ch) or is_vowel(ch)):
            self._write(self.composer.input(ch), self.composer.composing)
        else:
            # 영문·숫자·기호: 조합 중이던 글자는 이미 화면에 있으니 확정만 하고 새 글자만 보낸다
            self._flush_composing()
            winmouse.type_text(ch)
            if ch in SEPARATORS:
                self._finish_word()
            else:
                self.word += ch
            self.charsTyped.emit(1, 0)
        self._after_key()

    def _after_key(self):
        if self.shift:
            self.shift = False
            self._build_keys()
        self._refresh_suggestions()

    def _flush_composing(self):
        """조합 중 글자를 확정 (화면 글자는 그대로 둔다)."""
        done = self.composer.commit()
        if done:
            self.word += done
            self.charsTyped.emit(len(done), 0)
        self.shown = ""

    def _space(self):
        self._flush_composing()
        winmouse.type_text(" ")
        self._finish_word()
        self._refresh_suggestions()

    def _enter(self):
        self._flush_composing()
        winmouse.press_combo("enter")
        self._finish_word()
        self._refresh_suggestions()

    def _backspace(self):
        if self.composer.backspace():
            winmouse.backspace(len(self.shown))
            self.shown = self.composer.composing
            winmouse.type_text(self.shown)
        else:
            winmouse.backspace(1)
            self.word = self.word[:-1]
        self._refresh_suggestions()

    def _finish_word(self):
        w = self.word.strip()
        if w:
            self.predictor.learn(w, self.prev_word)
            self.wordTyped.emit(w, self.prev_word)
            self.prev_word = w
        self.word = ""

    def _pick_suggestion(self, text):
        if not text:
            return
        query = self.word + self.shown
        winmouse.backspace(len(query))
        winmouse.type_text(text + " ")
        saved = max(0, len(text) - len(query))
        self.composer.reset()
        self.shown = ""
        self.word = text
        self.charsTyped.emit(len(text), saved)
        self._finish_word()
        self._refresh_suggestions()

    def _edit(self, action_id):
        self._flush_composing()
        self._finish_word()
        self.prev_word = None
        self.actionRequested.emit(action_id)
        self._refresh_suggestions()

    def _toggle_shift(self):
        self.shift = not self.shift
        self._build_keys()

    def _toggle_lang(self):
        self._flush_composing()
        self.mode = "en" if self.mode == "ko" else "ko"
        self.shift = False
        self._build_keys()

    def _toggle_num(self):
        self._flush_composing()
        self.mode = "num" if self.mode != "num" else "ko"
        self.shift = False
        self._build_keys()

    def _close(self):
        self._flush_composing()
        self._finish_word()
        self.hide()
        self.closed.emit()

    def reset_context(self):
        """다른 곳을 클릭하면 이어 쓰던 단어 맥락을 끊는다."""
        self._flush_composing()
        self.word = ""
        self.prev_word = None
        self._refresh_suggestions()

    # ---------------- 자동완성 ----------------
    def _refresh_suggestions(self):
        query = self.word + self.shown
        if query:
            sugg = self.predictor.suggest(query, self.prev_word)
            self.preview.setText(f"입력 중  {query}")
        elif self.prev_word:
            sugg = self.predictor.suggest("", self.prev_word)
            self.preview.setText("다음 단어")
        else:
            sugg = [w for w, c in self.predictor.counts.most_common(5) if c > 0] or \
                ["안녕하세요", "감사합니다", "네", "아니요", "도와주세요"]
            self.preview.setText("자주 쓰는 말")
        for b, s in zip(self.sugg_buttons, sugg + [""] * 5):
            b.setText(s)
            b.setEnabled(bool(s))
