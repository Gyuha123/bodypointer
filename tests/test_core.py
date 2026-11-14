"""핵심 로직 테스트 (카메라·OS 마우스 없이 실행). 실행: python -m unittest discover tests"""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodypointer import winmouse  # noqa: E402
from bodypointer.dwell import DwellDetector  # noqa: E402
from bodypointer.engine import Engine  # noqa: E402
from bodypointer.filters import PointFilter  # noqa: E402
from bodypointer.mapping import Calibration, PointerMapper  # noqa: E402
from bodypointer.profile import Settings  # noqa: E402

W, H = 1920, 1080


class FakeMouse:
    """winmouse의 OS 호출을 가로채 기록한다."""

    def __init__(self):
        self.calls, self.pos = [], (W // 2, H // 2)
        self._orig = {}
        for name in ("move", "left_click", "right_click", "double_click", "left_down", "left_up", "wheel",
                     "get_pos", "screen_size", "wheel_at"):
            self._orig[name] = getattr(winmouse, name)
        winmouse.move = self.move
        winmouse.get_pos = lambda: self.pos
        winmouse.screen_size = lambda: (W, H)
        for name in ("left_click", "right_click", "double_click", "left_down", "left_up", "wheel", "wheel_at"):
            setattr(winmouse, name, lambda *a, n=name: self.calls.append((n, a)))

    def move(self, x, y):
        self.pos = (int(round(x)), int(round(y)))

    def restore(self):
        for k, v in self._orig.items():
            setattr(winmouse, k, v)

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)


class FakeHooks:
    def __init__(self):
        self.alerts, self.popup, self.own = [], None, False

    def ui_target(self, pt):
        return "toolbar" if self.own else None

    def ui_dwell(self, pt):
        self.alerts.append("ui_dwell")

    def popup_visible(self):
        return self.popup is not None

    def show_popup(self, pt):
        self.popup = pt

    def hide_popup(self):
        self.popup = None

    def alert(self, kind):
        self.alerts.append(kind)


class FilterTest(unittest.TestCase):
    def test_stationary_jitter_within_3px(self):
        random.seed(1)
        f = PointFilter(strength=60)
        outs = []
        for i in range(300):  # 10초, 30fps, ±6px 잡음
            x, y = f(500 + random.gauss(0, 3), 400 + random.gauss(0, 3), i / 30)
            if i > 60:
                outs.append((x, y))
        xs, ys = [o[0] for o in outs], [o[1] for o in outs]
        self.assertLessEqual(max(xs) - min(xs), 6.0)   # ±3px
        self.assertLessEqual(max(ys) - min(ys), 6.0)

    def test_follows_fast_motion(self):
        f = PointFilter(strength=60)
        for i in range(60):
            x, _ = f(i * 30.0, 0, i / 30)   # 900 px/s
        self.assertGreater(x, 59 * 30 - 120)       # 크게 뒤처지지 않음


class MappingTest(unittest.TestCase):
    def test_calibration_and_absolute_mapping(self):
        xs = [0.45 + 0.1 * math.cos(a / 10) for a in range(300)]
        ys = [0.50 + 0.06 * math.sin(a / 10) for a in range(300)]
        c = Calibration.from_samples(xs, ys)
        self.assertAlmostEqual((c.min_x + c.max_x) / 2, 0.45, delta=0.01)
        m = PointerMapper(W, H)
        m.calib = c
        m.filter.set_strength(0)
        for i in range(30):
            pos = m.update(0.45, 0.50, i / 30)
        self.assertAlmostEqual(pos[0], W / 2, delta=20)
        self.assertAlmostEqual(pos[1], H / 2, delta=20)
        m.reset()
        for i in range(30):
            pos = m.update(c.max_x, c.max_y, i / 30)
        self.assertGreater(pos[0], W - 30)
        self.assertGreater(pos[1], H - 30)

    def test_relative_clutch(self):
        m = PointerMapper(W, H)
        m.mode = "relative"
        m.filter.set_strength(0)
        m.update(0.5, 0.5, 0)
        m.clutched = True
        p = m.update(0.6, 0.5, 0.1)
        self.assertEqual(p, (W / 2, H / 2))       # 클러치 중엔 움직이지 않음
        m.clutched = False
        p = m.update(0.65, 0.5, 0.2)
        self.assertGreater(p[0], W / 2)


class DwellTest(unittest.TestCase):
    def test_fires_once_then_rearms(self):
        d = DwellDetector(1.0, 30)
        fired = [d.update((100, 100), i / 30) for i in range(70)]
        self.assertEqual(sum(f is not None for f in fired), 1)
        d.update((300, 300), 3.0)                  # 반경 밖으로 이동 → 재무장
        fired = [d.update((300, 300), 3.0 + i / 30) for i in range(40)]
        self.assertEqual(sum(f is not None for f in fired), 1)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse()
        self.hooks = FakeHooks()
        s = Settings(dwell_popup=False, dwell_time=1.0, filter_strength=0)
        self.e = Engine(s, self.hooks)
        self.t = 0.0

    def tearDown(self):
        self.mouse.restore()

    def run_still(self, sec, pt=(0.5, 0.5)):
        for _ in range(int(sec * 30)):
            self.t += 1 / 30
            self.e.on_point(pt, self.t)

    def test_starts_paused_no_click(self):
        self.assertTrue(self.e.paused)             # NFR-09
        self.run_still(3)
        self.assertEqual(self.mouse.count("left_click"), 0)

    def test_paused_dwell_on_own_ui_still_works(self):
        self.hooks.own = True
        self.run_still(1.5)
        self.assertIn("ui_dwell", self.hooks.alerts)
        self.assertEqual(self.mouse.count("left_click"), 0)

    def test_dwell_left_click_when_resumed(self):
        self.e.set_paused(False)
        self.run_still(1.5)
        self.assertEqual(self.mouse.count("left_click"), 1)

    def test_popup_flow(self):
        self.e.settings.dwell_popup = True
        self.e.set_paused(False)
        self.run_still(1.5)
        self.assertIsNotNone(self.hooks.popup)
        self.e.perform("right", self.hooks.popup)
        self.assertEqual(self.mouse.count("right_click"), 1)

    def test_drag(self):
        self.e.set_paused(False)
        self.e.perform("drag", (500, 500))
        self.assertTrue(self.e.drag_active)
        self.run_still(1.5, (0.6, 0.6))
        self.assertEqual(self.mouse.count("left_down"), 1)
        self.assertEqual(self.mouse.count("left_up"), 1)
        self.assertFalse(self.e.drag_active)

    def test_tracking_lost_freezes_cursor(self):
        self.run_still(0.5)
        before = self.mouse.pos
        for _ in range(20):
            self.t += 1 / 30
            self.e.on_point(None, self.t)
        self.assertIn("lost", self.hooks.alerts)
        self.assertEqual(self.mouse.pos, before)
        self.run_still(0.2)
        self.assertIn("found", self.hooks.alerts)

    def test_physical_mouse_override(self):
        self.e.set_paused(False)
        self.run_still(0.3)
        self.mouse.pos = (100, 100)                # 보호자가 실제 마우스를 움직임
        self.run_still(1.0)
        self.assertIn("override", self.hooks.alerts)
        self.assertEqual(self.mouse.pos, (100, 100))
        self.assertEqual(self.mouse.count("left_click"), 0)

    def test_gesture_hold_drag_not_released_by_dwell(self):
        self.e.set_paused(False)
        self.run_still(0.3)
        self.e.press_hold()                        # 입 벌리기 시작 → 꾹 누름
        self.run_still(2.5, (0.6, 0.6))            # 오래 머물러도 머무르기가 버튼을 놓지 않음
        self.assertTrue(self.e.drag_active)
        self.assertEqual(self.mouse.count("left_up"), 0)
        self.e.release_hold()                      # 표정 풂 → 놓기
        self.assertEqual(self.mouse.count("left_up"), 1)
        self.run_still(1.5, (0.6, 0.6))            # 놓은 자리에서 바로 클릭되지 않음
        self.assertEqual(self.mouse.count("left_click"), 0)

    def test_gesture_scroll_stays_until_released(self):
        self.e.set_paused(False)
        self.run_still(0.3)
        self.e.start_scroll()                      # 웃기 시작 → 휠 누른 듯 스크롤 모드
        self.run_still(4.0)                        # 가운데 오래 있어도 끝나지 않음 (수동 모드)
        self.assertIsNotNone(self.e.scroll_anchor)
        self.run_still(0.5, (0.5, 0.2))            # 위로 → 위로 스크롤
        wheels = [c[1][0] for c in self.mouse.calls if c[0] == "wheel"]
        self.assertTrue(wheels and all(w > 0 for w in wheels))
        self.e.stop_scroll()
        self.assertIsNone(self.e.scroll_anchor)

    def test_scroll_mode(self):
        self.e.set_paused(False)
        self.e.perform("scroll", (960, 540))
        self.run_still(1.0, (0.5, 0.75))           # 아래로 이동 → 아래로 스크롤
        wheels = [c[1][0] for c in self.mouse.calls if c[0] == "wheel"]
        self.assertTrue(wheels and all(w < 0 for w in wheels))
        self.assertEqual(self.mouse.pos, (960, 540))   # 커서는 기준점에 고정
        self.run_still(2.5, (0.5, 0.5))            # 가운데 머무르면 종료
        self.assertIsNone(self.e.scroll_anchor)


if __name__ == "__main__":
    unittest.main()
