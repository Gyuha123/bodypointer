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

    def test_strong_tremor_filtered(self):
        """심한 떨림(±15px)도 떨림 보정 200이면 정지 시 커서가 거의 움직이지 않는다."""
        random.seed(2)
        f = PointFilter(strength=200)
        outs = []
        for i in range(300):
            x, y = f(500 + random.gauss(0, 7), 400 + random.gauss(0, 7), i / 30)
            if i > 90:
                outs.append((x, y))
        xs, ys = [o[0] for o in outs], [o[1] for o in outs]
        self.assertLessEqual(max(xs) - min(xs), 8.0)
        self.assertLessEqual(max(ys) - min(ys), 8.0)

    def test_slow_motion_has_no_stutter(self):
        """천천히 움직일 때(초당 60px) 멈췄다-튀었다 하지 않는다: 매 프레임 조금씩 계속 움직인다."""
        for strength in (60, 160, 200):
            f = PointFilter(strength=strength)
            xs = [f(i * 2.0, 0, i / 30)[0] for i in range(150)]      # 30fps, 프레임당 2px
            steps = [b - a for a, b in zip(xs[60:], xs[61:])]
            self.assertGreater(min(steps), 0.3, strength)               # 멈춘 프레임 없음
            self.assertLess(max(steps), 6.0, strength)                  # 확 튀는 프레임 없음

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


class TremorDwellTest(unittest.TestCase):
    def test_grace_keeps_progress_through_brief_excursions(self):
        d = DwellDetector(1.0, 30, grace=0.4)
        fired = []
        for i in range(60):                        # 2초, 0.3초마다 반경 밖으로 잠깐(0.1초) 튐
            t = i / 30
            pos = (160, 100) if i % 9 in (0, 1, 2) else (100, 100)
            fired.append(d.update(pos, t))
        self.assertTrue(any(f is not None for f in fired))

    def test_without_grace_excursions_reset(self):
        d = DwellDetector(1.0, 30, grace=0.0)
        fired = [d.update((160, 100) if i % 9 in (0, 1, 2) else (100, 100), i / 30) for i in range(60)]
        self.assertTrue(all(f is None for f in fired))

    def test_long_move_away_still_resets(self):
        d = DwellDetector(1.0, 30, grace=0.4)
        for i in range(15):
            d.update((100, 100), i / 30)
        for i in range(20):                        # 0.66초 동안 다른 곳 → 다시 시작
            d.update((400, 400), 0.5 + i / 30)
        self.assertLess(d.progress, 0.6)
        self.assertGreater(math.dist(d.anchor, (100, 100)), 100)


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

    def test_overlapping_clicks_are_not_double_click(self):
        """머무르기 클릭과 표정 클릭이 거의 동시에 겹쳐도 한 번만 누른다 (더블클릭 오인 방지)."""
        self.e.perform("left", (500, 500))
        self.e.perform("left", (502, 501))            # 0.6초 안에 또 → 버림
        self.assertEqual(self.mouse.count("left_click"), 1)
        self.e._last_left_click -= 1.0                 # 1초 지난 것으로
        self.e.perform("left", (500, 500))
        self.assertEqual(self.mouse.count("left_click"), 2)
        self.e.perform("double", (500, 500))           # 일부러 고른 더블클릭은 그대로
        self.assertEqual(self.mouse.count("double_click"), 1)

    def test_tremor_click_lands_on_target(self):
        """떨림 클릭 보조: 떨리는 손으로도 머무르기 클릭이 되고, 목표 근처를 누른다."""
        random.seed(5)
        self.e.settings.tremor_assist = True
        self.e.apply_settings(self.e.settings)
        self.e.set_paused(False)
        self.run_still(0.3)
        clicks = []
        for _ in range(int(3 * 30)):                # 3초 동안 목표(가운데) 주변에서 크게 떨림
            self.t += 1 / 30
            jitter = (0.5 + random.gauss(0, 0.012), 0.5 + random.gauss(0, 0.012))
            self.e.on_point(jitter, self.t)
            if self.mouse.count("left_click") > len(clicks):
                clicks.append(self.mouse.pos)
        self.assertGreaterEqual(len(clicks), 1)
        self.assertLess(math.dist(clicks[0], (960, 540)), 40)

    def test_slow_move_not_frozen_by_click_assist(self):
        """떨림 클릭 보조가 켜져 있어도, 천천히 이동하는 중에는 커서를 붙잡지 않는다 (끊김 방지)."""
        self.e.settings.tremor_assist = True
        self.e.settings.dwell_radius = 80
        self.e.apply_settings(self.e.settings)
        self.e.set_paused(False)
        self.run_still(0.3)
        xs = []
        for i in range(120):                       # 4초 동안 오른쪽으로 천천히 (초당 약 60px)
            self.t += 1 / 30
            self.e.on_point((0.5 + i * 0.0004, 0.5), self.t)
            xs.append(self.mouse.pos[0])
        frozen = max(len(list(g)) for _, g in __import__("itertools").groupby(xs))
        self.assertLessEqual(frozen, 3)            # 같은 자리에 멈춰 있는 프레임이 길게 이어지지 않음
        self.assertLess(max(abs(b - a) for a, b in zip(xs, xs[1:])), 6)   # 확 튀지 않음
        self.assertEqual(self.mouse.count("left_click"), 0)              # 천천히 이동 중엔 클릭하지 않음
        self.run_still(1.0, (0.5 + 119 * 0.0004, 0.5))                   # 멈추면 곧바로 클릭
        self.assertEqual(self.mouse.count("left_click"), 1)

    def test_scroll_pick_targets_where_user_dwells(self):
        """툴바 '스크롤' → 다음에 머무른 곳에서 스크롤 시작 (일시정지 중·BodyPointer 창 위에서도)."""
        self.e.last_target = (100, 100)            # 예전 클릭 위치 (다른 창) — 여기로 가면 안 됨
        self.hooks.own = True                      # 설정 창 위
        self.e.scroll_pick = True
        self.run_still(1.5, (0.6, 0.6))
        self.assertFalse(self.e.scroll_pick)
        self.assertIsNotNone(self.e.scroll_anchor)
        self.assertNotEqual(self.e.scroll_anchor, (100, 100))
        self.assertNotIn("ui_dwell", self.hooks.alerts)    # 버튼을 누르지 않고 스크롤만 시작
        anchor = self.e.scroll_anchor
        self.run_still(0.5, (0.6, 0.85))           # 아래로 움직이면 그 자리에서 아래로 스크롤
        self.assertEqual(self.mouse.pos, (int(round(anchor[0])), int(round(anchor[1]))))
        self.assertTrue(any(c[0] == "wheel" and c[1][0] < 0 for c in self.mouse.calls))

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
