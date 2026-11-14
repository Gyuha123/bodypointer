"""두벌식 한글 조합기와 자모 유틸리티."""

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ",
        "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
VOWEL_COMBO = {("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ", ("ㅜ", "ㅓ"): "ㅝ",
               ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ", ("ㅡ", "ㅣ"): "ㅢ"}
JONG_COMBO = {("ㄱ", "ㅅ"): "ㄳ", ("ㄴ", "ㅈ"): "ㄵ", ("ㄴ", "ㅎ"): "ㄶ", ("ㄹ", "ㄱ"): "ㄺ", ("ㄹ", "ㅁ"): "ㄻ",
              ("ㄹ", "ㅂ"): "ㄼ", ("ㄹ", "ㅅ"): "ㄽ", ("ㄹ", "ㅌ"): "ㄾ", ("ㄹ", "ㅍ"): "ㄿ", ("ㄹ", "ㅎ"): "ㅀ",
              ("ㅂ", "ㅅ"): "ㅄ"}
JONG_SPLIT = {v: k for k, v in JONG_COMBO.items()}


def is_consonant(c: str) -> bool:
    return len(c) == 1 and c in CHO


def is_vowel(c: str) -> bool:
    return len(c) == 1 and c in JUNG


def is_hangul_syllable(c: str) -> bool:
    return len(c) == 1 and 0xAC00 <= ord(c) <= 0xD7A3


def compose(cho, jung, jong=None) -> str:
    if cho and jung:
        return chr(0xAC00 + (CHO.index(cho) * 21 + JUNG.index(jung)) * 28 + JONG.index(jong or ""))
    return cho or jung or ""


def decompose(text: str) -> str:
    """'안녕' → 'ㅇㅏㄴㄴㅕㅇ' (겹받침·겹모음은 한 글자로 유지)."""
    out = []
    for ch in text:
        if is_hangul_syllable(ch):
            c = ord(ch) - 0xAC00
            out.append(CHO[c // 588])
            out.append(JUNG[(c % 588) // 28])
            if c % 28:
                out.append(JONG[c % 28])
        else:
            out.append(ch)
    return "".join(out)


def choseong(text: str) -> str:
    """'안녕하세요' → 'ㅇㄴㅎㅅㅇ'."""
    return "".join(CHO[(ord(c) - 0xAC00) // 588] if is_hangul_syllable(c) else c for c in text)


class HangulComposer:
    """자모를 하나씩 받아 음절을 조합한다.

    input(j)는 '확정된 글자'를 돌려주고, composing 속성은 '조합 중인 글자'다.
    화면에는 확정 글자 + 조합 중 글자를 보여 주면 된다.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.cho = self.jung = self.jong = None
        self._history = []          # 현재 음절 안에서의 이전 상태들 (자모 단위 지우기용)

    @property
    def composing(self) -> str:
        return compose(self.cho, self.jung, self.jong)

    def _state(self):
        return (self.cho, self.jung, self.jong)

    def _set(self, cho=None, jung=None, jong=None):
        self.cho, self.jung, self.jong = cho, jung, jong

    def _commit_and_start(self, cho=None, jung=None) -> str:
        done = self.composing
        self._set(cho, jung, None)
        self._history = []
        if cho and jung:
            self._history = [(None, None, None), (cho, None, None)]
        elif cho or jung:
            self._history = [(None, None, None)]
        return done

    def commit(self) -> str:
        done = self.composing
        self.reset()
        return done

    def input(self, j: str) -> str:
        cho, jung, jong = self._state()
        if is_consonant(j):
            if not cho and not jung:
                self._history.append(self._state())
                self._set(cho=j)
                return ""
            if cho and not jung:
                return self._commit_and_start(cho=j)
            if not cho and jung:
                return self._commit_and_start(cho=j)
            if not jong:
                if j in JONG:
                    self._history.append(self._state())
                    self.jong = j
                    return ""
                return self._commit_and_start(cho=j)
            combo = JONG_COMBO.get((jong, j))
            if combo:
                self._history.append(self._state())
                self.jong = combo
                return ""
            return self._commit_and_start(cho=j)

        if is_vowel(j):
            if not cho and not jung:
                self._history.append(self._state())
                self._set(jung=j)
                return ""
            if cho and not jung:
                self._history.append(self._state())
                self.jung = j
                return ""
            if not jong:
                combo = VOWEL_COMBO.get((jung, j))
                if combo:
                    self._history.append(self._state())
                    self.jung = combo
                    return ""
                return self._commit_and_start(jung=j)
            # 받침이 다음 글자의 초성으로 넘어감 (각+ㅏ → 가가, 닭+ㅏ → 달가)
            if jong in JONG_SPLIT:
                keep, move = JONG_SPLIT[jong]
            else:
                keep, move = None, jong
            self.jong = keep
            return self._commit_and_start(cho=move, jung=j)

        # 자모가 아닌 글자(공백, 숫자, 영문, 기호)
        return self.commit() + j

    def backspace(self) -> bool:
        """조합 중인 글자에서 자모 하나를 지운다. 조합 중인 글자가 없으면 False."""
        if not self.composing:
            return False
        if self._history:
            self._set(*self._history.pop())
        else:
            self.reset()
        return True
