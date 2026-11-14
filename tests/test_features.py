"""추가 기능 테스트: 한글 조합, 자동완성, 좌표 안정화, 제스처, 기록 저장, 추천."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodypointer.gestures import GestureDetector, personal_threshold  # noqa: E402
from bodypointer.hangul import HangulComposer, choseong  # noqa: E402
from bodypointer.insights import auto_quick_actions, recommend  # noqa: E402
from bodypointer.parts import get_part  # noqa: E402
from bodypointer.predict import Predictor  # noqa: E402
from bodypointer.profile import Settings  # noqa: E402
from bodypointer.stabilize import PointStabilizer  # noqa: E402
from bodypointer.usage import SessionRecorder, UsageStore  # noqa: E402
from bodypointer.vision import FrameResult, extract_sample  # noqa: E402


def typed(keys):
    c = HangulComposer()
    out = "".join(c.input(k) for k in keys)
    return out + c.composing


class HangulTest(unittest.TestCase):
    def test_compose(self):
        cases = {"ㅇㅏㄴㄴㅕㅇㅎㅏㅅㅔㅇㅛ": "안녕하세요", "ㄷㅏㄹㄱ": "닭", "ㄷㅏㄹㄱㅏ": "달가", "ㄱㅗㅏ": "과",
                 "ㅇㅡㅣㅅㅏ": "의사", "ㄱㅏㅂㅅㅣ": "갑시", "ㅇㄴㅎ": "ㅇㄴㅎ", "ㄲㅗㅊ ㄷ": "꽃 ㄷ", "ㅆㅏ": "싸"}
        for keys, expected in cases.items():
            self.assertEqual(typed(keys), expected, keys)

    def test_backspace_jamo(self):
        c = HangulComposer()
        for k in "ㄷㅏㄹㄱ":
            c.input(k)
        c.backspace()
        self.assertEqual(c.composing, "달")
        c.backspace()
        c.backspace()
        self.assertEqual(c.composing, "ㄷ")
        c.backspace()
        self.assertFalse(c.backspace())

    def test_choseong(self):
        self.assertEqual(choseong("안녕하세요"), "ㅇㄴㅎㅅㅇ")


class PredictTest(unittest.TestCase):
    def test_choseong_and_prefix(self):
        p = Predictor()
        self.assertIn("안녕하세요", p.suggest("ㅇㄴㅎ"))
        self.assertIn("안녕하세요", p.suggest("안녀"))      # 조합 중인 '녀'로도 찾는다

    def test_learning_ranks_personal_words_first(self):
        p = Predictor()
        for _ in range(3):
            p.learn("안녕히주무세요")
        self.assertEqual(p.suggest("ㅇㄴ")[0], "안녕히주무세요")

    def test_next_word(self):
        p = Predictor()
        p.learn("물", None)
        p.learn("주세요", "물")
        self.assertEqual(p.suggest("", "물"), ["주세요"])


class StabilizeTest(unittest.TestCase):
    def test_hold_through_low_confidence(self):
        s = PointStabilizer(min_conf=0.3, smooth=0.5, hold=0.4)
        self.assertIsNotNone(s((0.5, 0.5, 0.9), 0.0))
        self.assertEqual(s((0.9, 0.9, 0.1), 0.1), (0.5, 0.5))   # 낮은 신뢰도 → 직전 위치 유지
        self.assertIsNone(s(None, 1.0))                          # hold 지나면 끊김

    def test_rejects_low_conf_jump(self):
        s = PointStabilizer(min_conf=0.3, smooth=0.0)
        s((0.5, 0.5, 0.9), 0.0)
        self.assertEqual(s((0.9, 0.1, 0.5), 0.03), (0.5, 0.5))   # 갑자기 튀는 애매한 값 무시
        s((0.9, 0.1, 0.5), 0.06)
        s((0.9, 0.1, 0.5), 0.09)
        self.assertEqual(s((0.9, 0.1, 0.5), 0.12), (0.9, 0.1))   # 계속 오면 받아들임

    def test_composite_leg_point(self):
        pose = [(0.0, 0.0, 0.0)] * 33
        pose[31], pose[29], pose[27] = (0.6, 0.9, 0.9), (0.5, 0.92, 0.9), (0.5, 0.85, 0.9)
        x, y, c = extract_sample(FrameResult(0, None, pose=pose), get_part("l_foot"))
        self.assertAlmostEqual(x, (0.6 * 2 + 0.5 + 0.5) / 4, places=3)
        self.assertAlmostEqual(c, 0.9, places=3)


class GestureTest(unittest.TestCase):
    TAP = {"mouth_open": "tap"}

    def test_tap_fires_once_per_expression(self):
        g = GestureDetector()
        ev = []
        for i in range(30):          # 1초 동안 입을 벌리고 있음
            ev += g.update({"jawOpen": 0.8}, i / 30, self.TAP)
        self.assertEqual(ev, [("mouth_open", "tap")])
        for i in range(10):
            g.update({"jawOpen": 0.0}, 1 + i / 30, self.TAP)
        for i in range(30):
            ev += g.update({"jawOpen": 0.8}, 2 + i / 30, self.TAP)
        self.assertEqual(len(ev), 2)

    def test_hold_start_and_end(self):
        g = GestureDetector()
        modes = {"smile": "hold"}
        ev = []
        for i in range(30):
            ev += g.update({"mouthSmileLeft": 0.9, "mouthSmileRight": 0.9}, i / 30, modes)
        self.assertEqual(ev, [("smile", "start")])
        self.assertEqual(g.holding(), {"smile"})
        ev += g.update({"mouthSmileLeft": 0.62, "mouthSmileRight": 0.62}, 1.05, modes)   # 살짝 약해져도 유지
        self.assertEqual(len(ev), 1)
        for i in range(10):
            ev += g.update({"mouthSmileLeft": 0.0}, 1.1 + i / 30, modes)     # 표정을 풂
        self.assertEqual(ev[-1], ("smile", "end"))

    def test_face_lost_releases_hold(self):
        g = GestureDetector()
        for i in range(20):
            g.update({"jawOpen": 0.9}, i / 30, {"mouth_open": "hold"})
        self.assertEqual(g.update(None, 1.0, {"mouth_open": "hold"}), [("mouth_open", "end")])

    def test_inactive_gesture_ignored(self):
        g = GestureDetector()
        self.assertEqual(sum((g.update({"browInnerUp": 1.0}, i / 30, {}) for i in range(30)), []), [])

    def test_personal_threshold(self):
        th = personal_threshold([0.05] * 30, [0.5] * 60)
        self.assertAlmostEqual(th, 0.05 + 0.45 * 0.55, places=3)
        self.assertIsNone(personal_threshold([0.3] * 30, [0.35] * 60))   # 구분이 안 되는 표정
        g = GestureDetector(personal={"brow_raise": 0.2})
        ev = []
        for i in range(30):
            ev += g.update({"browInnerUp": 0.3}, i / 30, {"brow_raise": "tap"})   # 기본 기준(0.55)이면 안 됐을 값
        self.assertEqual(ev, [("brow_raise", "tap")])


class LinkTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_url("youtube.com"), "https://youtube.com")
        self.assertEqual(normalize_url(" https://www.naver.com/search?q=1 "), "https://www.naver.com/search?q=1")
        self.assertEqual(normalize_url("http://192.168.0.10:8080/a"), "http://192.168.0.10:8080/a")
        for bad in ["", "hello", "file:///C:/Windows/system32/calc.exe", "javascript:alert(1)", "mailto:a@b.com",
                    r"C:\Windows\notepad.exe", "ftp://x.com", "naver .com"]:
            self.assertIsNone(normalize_url(bad), bad)

    def test_action_roundtrip(self):
        aid = make_link_action("유튜브", "https://www.youtube.com")
        self.assertEqual(link_parts(aid), ("유튜브", "https://www.youtube.com"))
        self.assertEqual(get_action(aid).label, "유튜브 열기")
        self.assertEqual(get_action(aid).kind, "link")
        self.assertEqual(link_parts(make_link_action("", "https://www.daum.net"))[0], "daum.net")   # 이름 없으면 도메인
        self.assertIsNone(get_action("link:나쁜\tfile:///C:/x.exe"))                              # 저장 파일이 조작돼도 거부

    def test_link_not_recorded_with_url(self):
        r = SessionRecorder()
        r.on_action("link", "gesture")
        self.assertNotIn("youtube", str(r.summary()))


class UsageAndInsightsTest(unittest.TestCase):
    def _session(self):
        r = SessionRecorder()
        for i in range(200):
            r.on_frame("nose", (0.4 + 0.2 * (i % 20) / 20, 0.5), (500 + i, 400), i / 30, False, 1920)
        for _ in range(12):
            r.on_click("left", (960, 540), (1920, 1080))
        for _ in range(8):
            r.on_action("select_all", "keyboard")
            r.on_action("copy", "keyboard")
            r.on_action("paste", "toolbar")
        r.on_word("안녕하세요", None)
        r.on_word("반가워요", "안녕하세요")
        return r

    def test_store_roundtrip_and_words(self):
        with tempfile.TemporaryDirectory() as d:
            store = UsageStore(Path(d) / "u.db")
            store.save("p", self._session(), None)
            sessions = store.sessions("p")
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["actions"]["copy"], 8)
            self.assertGreater(sessions[0]["rom_x"], 0.1)
            words, bigrams, _ = store.words("p")
            self.assertEqual(words["반가워요"], 1)
            self.assertEqual(bigrams[("안녕하세요", "반가워요")], 1)
            csv_path = store.export_csv("p", Path(d) / "out.csv")
            self.assertIn("가동 범위", csv_path.read_text("utf-8-sig"))

    def test_recommendations(self):
        s = self._session().summary()
        s["started"] = 0
        recs = recommend([s], Settings())
        kinds = {r.apply.get("type") for r in recs}
        self.assertIn("bind_gesture", kinds)            # 자주 쓴 복사·붙여넣기 → 제스처 제안
        self.assertIn("add_quick", kinds)               # 전체선택→복사 연속 사용 → 묶음 동작 제안
        self.assertEqual(auto_quick_actions([s])[:3], ["select_all", "copy", "paste"])

    def test_dismissed_hidden(self):
        s = self._session().summary()
        st = Settings()
        ids = [r.id for r in recommend([s], st)]
        st.dismissed_recs = ids
        self.assertEqual(recommend([s], st), [])


if __name__ == "__main__":
    unittest.main()
