"""입력 엔진: 추적 좌표 → 커서 이동 → Dwell → 클릭/메뉴/드래그/스크롤.

UI와는 hooks 객체로만 연결한다 (모듈 분리, §5):
  hooks.ui_target(pt) -> str | None   pt가 BodyPointer 자체 UI 위면 그 이름
  hooks.ui_dwell(pt)                  자체 UI 위에서 Dwell 발동
  hooks.popup_visible() -> bool
  hooks.show_popup(pt) / hooks.hide_popup()
  hooks.alert(kind: str)              lost / found / paused / resumed / override / drag_start ...
"""
import math

from . import winmouse
from .dwell import DwellDetector
from .mapping import Calibration, PointerMapper
from .profile import Settings

LOST_AFTER = 0.3          # 이 시간 이상 부위가 안 보이면 '추적 끊김'
OVERRIDE_HOLD = 2.0       # 실제 마우스가 움직이면 이 시간 동안 양보
CLUTCH_AUTO_RELEASE = 3.0


class Engine:
    def __init__(self, settings: Settings, hooks):
        self.hooks = hooks
        self.w, self.h = winmouse.screen_size()
        self.mapper = PointerMapper(self.w, self.h)
        self.dwell = DwellDetector()
        self.settings = settings

        self.control_enabled = True
        self.paused = True                    # NFR-09: 시작 시 클릭 일시정지
        self.lost = True
        self.last_seen = None
        self.pos = (self.w / 2, self.h / 2)   # 엔진이 계산한 포인터 위치
        self.drag_active = False
        self._drag_manual = False             # 제스처로 꾹 누르는 중
        self.scroll_anchor = None
        self._scroll_manual = False           # 제스처로 시작한 스크롤 (가운데 머물러도 안 끝남)
        self.scroll_pick = False              # 툴바 '스크롤': 스크롤할 곳을 고르는 중
        self._locked = False                  # 떨림 클릭 보조로 커서를 붙잡고 있는지
        self._speed = 0.0                     # 포인터 평균 속도 (px/s)
        self._prev_pos, self._prev_t = None, 0.0
        self._scroll_still_since = None
        self.last_target = None               # 마지막으로 클릭한 지점
        self._last_set = None
        self.override_until = 0.0
        self.clutch_until = 0.0
        self._edge_since = None
        self._last_wheel = 0.0
        self.apply_settings(settings)

    def _emit(self, name, *args):
        """사용 기록용 이벤트 (hooks.on_engine_event가 있으면 전달)."""
        fn = getattr(self.hooks, "on_engine_event", None)
        if fn:
            fn(name, *args)

    # ---------------- 설정 ----------------
    def apply_settings(self, s: Settings):
        self.settings = s
        m = self.mapper
        m.mode = s.mode
        m.sensitivity = s.sensitivity
        m.gain = s.rel_gain
        m.accel = s.accel
        m.filter.set_strength(s.filter_strength)
        m.calib = Calibration.from_list(s.calibrations.get(s.part_id))
        self.dwell.dwell_time = s.dwell_time
        self.dwell.radius = s.dwell_radius
        self.dwell.grace = 0.4 if getattr(s, "tremor_assist", False) else 0.0
        m.reset(cursor=self.pos)

    def set_calibration(self, calib: Calibration):
        self.mapper.calib = calib
        self.settings.calibrations[self.settings.part_id] = calib.to_list()
        self.mapper.reset(cursor=self.pos)

    # ---------------- 상태 전환 ----------------
    def set_paused(self, paused: bool):
        if paused == self.paused:
            return
        self.paused = paused
        if paused:
            self._end_modes()
        self.dwell.reset()
        self.hooks.alert("paused" if paused else "resumed")

    def toggle_pause(self):
        self.set_paused(not self.paused)

    def set_control(self, enabled: bool):
        self.control_enabled = enabled
        if not enabled:
            self._end_modes()
        self.dwell.reset()
        self.mapper.reset(cursor=winmouse.get_pos())
        self.hooks.alert("control_on" if enabled else "control_off")

    def toggle_clutch(self, t: float, timed: bool = False):
        m = self.mapper
        m.clutched = not m.clutched
        self.clutch_until = t + CLUTCH_AUTO_RELEASE if (m.clutched and timed) else 0.0
        if not m.clutched:
            m.reset()
        self.hooks.alert("clutch_on" if m.clutched else "clutch_off")

    def _end_modes(self):
        if self.drag_active:
            winmouse.left_up()
            self.drag_active = False
        self._drag_manual = False
        self.scroll_anchor = None
        self._scroll_manual = False
        self.scroll_pick = False
        if self.hooks.popup_visible():
            self.hooks.hide_popup()

    # ---------------- 제스처로 하는 스크롤·꾹 누르기 ----------------
    def start_scroll(self, anchor=None, manual=True):
        """휠 버튼을 누른 것처럼: 기준점에서 위·아래로 움직인 만큼 스크롤.
        manual=True 이면 가운데에 머물러도 끝나지 않고 stop_scroll()로만 끝난다 (제스처용)."""
        if self.scroll_anchor is not None:
            return
        self.scroll_anchor = anchor or self.pos
        self._scroll_manual = manual
        self._scroll_still_since = None
        self.hooks.alert("scroll_start")

    def stop_scroll(self):
        if self.scroll_anchor is None:
            return
        self.scroll_anchor = None
        self._scroll_manual = False
        self.dwell.anchor = self.pos          # 끝나자마자 머무르기 클릭이 이어지지 않도록
        self.dwell.armed = False
        self.hooks.alert("scroll_end")

    def press_hold(self):
        """지금 커서 위치에서 왼쪽 버튼을 꾹 누른 상태로 (드래그 시작)."""
        if self.drag_active:
            return
        self.last_target = self.pos
        winmouse.left_down()
        self.drag_active = True
        self._drag_manual = True
        self._emit("click", "drag", self.pos)
        self.hooks.alert("drag_start")

    def release_hold(self):
        if not self.drag_active:
            return
        winmouse.left_up()
        self.drag_active = False
        self._drag_manual = False
        self.dwell.anchor = self.pos
        self.dwell.armed = False
        self.hooks.alert("drag_end")

    # ---------------- 프레임 처리 ----------------
    def on_point(self, pt, t: float):
        """pt: 선택 부위의 정규화 좌표 (x, y) 또는 None(미검출)."""
        if pt is None:
            if not self.lost and (self.last_seen is None or t - self.last_seen > LOST_AFTER):
                self.lost = True
                self.dwell.reset()            # FR-04: 커서는 현재 위치에 고정
                self.hooks.alert("lost")
                self._emit("lost")
            return
        if not self._begin_frame(t):
            return
        self.pos = self.mapper.update(pt[0], pt[1], t)
        self._after_move(t)

    def on_delta(self, dx: float, dy: float, t: float):
        """머리 센서처럼 '움직인 양(px)'만 주는 입력. 카메라 입력과 같은 클릭·스크롤 처리를 쓴다."""
        if not self._begin_frame(t):
            return
        if not self.mapper.clutched:
            x = min(max(self.pos[0] + dx, 0), self.w - 1)
            y = min(max(self.pos[1] + dy, 0), self.h - 1)
            self.pos = (x, y)
        self.mapper.reset(cursor=self.pos)   # 카메라(상대 모드)로 돌아갈 때 이어지도록
        self._after_move(t)

    def _begin_frame(self, t: float) -> bool:
        """추적 복귀·커서 제어 여부·실제 마우스 양보를 처리. 계속 진행하면 True."""
        if self.lost:
            self.lost = False
            self.mapper.reset(cursor=self.pos)
            self.hooks.alert("found")
        self.last_seen = t
        if not self.control_enabled:
            return False
        # 보호자/사용자가 실제 마우스를 움직이면 잠시 양보
        cur = winmouse.get_pos()
        if self._last_set and self.scroll_anchor is None and math.dist(cur, self._last_set) > 40:
            if t >= self.override_until:
                self.hooks.alert("override")
            self.override_until = t + OVERRIDE_HOLD
        if t < self.override_until:
            self._last_set = cur
            self.pos = cur
            self.mapper.reset(cursor=cur)
            self.dwell.reset()
            return False
        if self.clutch_until and t >= self.clutch_until:
            self.toggle_clutch(t)
        return True

    def _after_move(self, t: float):
        self._track_speed(t)
        if self.scroll_anchor is not None:
            self._scroll_step(t)
            return

        winmouse.move(*self.pos)
        self._last_set = (int(round(self.pos[0])), int(round(self.pos[1])))

        if self._edge_scroll(t):
            self.dwell.reset()
            return

        if self.scroll_pick:
            # 스크롤할 곳 고르기: 다음에 머무른 곳(어느 창이든)에서 스크롤 모드를 시작한다.
            # 클릭이 아니라서 일시정지 중에도 동작한다.
            self.dwell.dwell_time = self.settings.dwell_time
            fired = self.dwell.update(self.pos, t)
            if fired:
                self.scroll_pick = False
                self.last_target = fired
                self.start_scroll(fired, manual=False)
            return

        own = self.hooks.ui_target(self.pos)
        if self.paused and not own:
            self.dwell.reset()
            return
        if self.mapper.clutched or self._drag_manual:
            # 제스처로 꾹 누르는 동안에는 머무르기가 버튼을 놓지 않게 한다
            self.dwell.reset()
            return
        per_target = getattr(self.hooks, "dwell_time_for", None)
        self.dwell.dwell_time = per_target(own) if per_target else self.settings.dwell_time
        fired = self.dwell.update(self.pos, t)
        self._update_lock()
        if self._locked:
            # 떨림 클릭 보조: 멈춰서 게이지를 채우는 중이면 실제 커서를 기준점에 붙잡아 떨림에 밀리지 않게 한다
            winmouse.move(*self.dwell.anchor)
            self._last_set = (int(round(self.dwell.anchor[0])), int(round(self.dwell.anchor[1])))
        if fired:
            self._locked = False
            self._on_dwell(fired, own)

    LOCK_AT = 0.5             # 게이지가 이만큼 찼고
    LOCK_SPEED = 60.0         # 포인터 속도(px/s, 평균)가 이보다 느릴 때만 붙잡는다 → 천천히 이동 중엔 붙잡지 않음
    UNLOCK_SPEED = 160.0      # 이보다 빨라지면 바로 놓는다

    def _track_speed(self, t):
        if self._prev_pos is not None and t > self._prev_t:
            v = math.dist(self.pos, self._prev_pos) / (t - self._prev_t)
            self._speed = 0.75 * self._speed + 0.25 * v
        self._prev_pos, self._prev_t = self.pos, t

    def _update_lock(self):
        d = self.dwell
        if not getattr(self.settings, "tremor_assist", False) or d.anchor is None or d.progress <= 0 \
                or self.scroll_anchor is not None:
            self._locked = False
            return
        if self._locked:
            if self._speed > self.UNLOCK_SPEED or math.dist(self.pos, d.anchor) > d.radius * 0.75:
                self._locked = False
        elif d.progress >= self.LOCK_AT and self._speed < self.LOCK_SPEED:
            self._locked = True

    @property
    def locked_pos(self):
        """떨림 클릭 보조로 커서를 붙잡고 있으면 그 위치, 아니면 None."""
        return self.dwell.anchor if self._locked else None

    @property
    def display_pos(self):
        """화면에 게이지를 그릴 위치."""
        return self.scroll_anchor or self.locked_pos or self.pos

    def _on_dwell(self, pt, own):
        if own:
            self.hooks.ui_dwell(pt)
            return
        if self.hooks.popup_visible():       # 메뉴 밖에서 Dwell = 취소
            self.hooks.hide_popup()
            self._emit("popup_cancel")
            return
        if self.drag_active:
            winmouse.left_up()
            self.drag_active = False
            self.hooks.alert("drag_end")
            return
        if self.settings.dwell_popup:
            self.hooks.show_popup(pt)
            return
        self.perform(self.settings.click_type, pt)

    def perform(self, action: str, pt):
        """클릭 종류를 pt 위치에서 실행 (FR-31)."""
        self.last_target = pt
        winmouse.move(*pt)
        self._last_set = (int(round(pt[0])), int(round(pt[1])))
        self._emit("click", action, pt)
        if action == "left":
            winmouse.left_click()
        elif action == "right":
            winmouse.right_click()
        elif action == "double":
            winmouse.double_click()
        elif action == "drag":
            winmouse.left_down()
            self.drag_active = True
            self.hooks.alert("drag_start")
        elif action == "scroll":
            self.start_scroll(pt, manual=False)

    # ---------------- 스크롤 (FR-33) ----------------
    def _scroll_step(self, t: float):
        """스크롤 모드: 커서는 기준점에 고정, 포인터를 위/아래로 움직인 만큼 스크롤."""
        ax, ay = self.scroll_anchor
        winmouse.move(ax, ay)
        self._last_set = (int(round(ax)), int(round(ay)))
        dy = self.pos[1] - ay
        dead = max(self.settings.dwell_radius, 30)
        if abs(dy) <= dead:
            self._scroll_still_since = self._scroll_still_since or t
            if not self._scroll_manual and t - self._scroll_still_since >= self.settings.dwell_time * 1.5:
                self.stop_scroll()            # 가운데에 머물면 스크롤 종료 (메뉴·툴바로 시작한 경우)
            return
        self._scroll_still_since = None
        if t - self._last_wheel >= 0.05:
            rate = min((abs(dy) - dead) / 60.0, 8.0)            # 초당 노치 수
            winmouse.wheel((-1 if dy > 0 else 1) * rate * (t - self._last_wheel if self._last_wheel else 0.05))
            self._last_wheel = t

    def _edge_scroll(self, t: float) -> bool:
        if not self.settings.edge_scroll or self.paused:
            self._edge_since = None
            return False
        band = max(6, self.h * 0.015)
        y = self.pos[1]
        direction = 1 if y <= band else -1 if y >= self.h - 1 - band else 0
        if not direction:
            self._edge_since = None
            return False
        self._edge_since = self._edge_since or t
        if t - self._edge_since >= 0.5 and t - self._last_wheel >= 0.12:
            winmouse.wheel(direction)
            self._last_wheel = t
        return True
