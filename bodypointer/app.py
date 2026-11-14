"""앱 컨트롤러: 비전 → 안정화 → 엔진 → UI를 잇고, 기록·추천·제스처·키보드를 관리한다."""
import atexit
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QPoint, QTimer, Signal
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QHBoxLayout, QMessageBox, QVBoxLayout

from . import profile, vision, winmouse
from .actions import ALLOWED_WHILE_PAUSED, get_action, macro_steps
from .engine import Engine
from .gestures import GESTURES, GestureDetector
from .insights import auto_quick_actions, recommend
from .parts import get_part
from .predict import Predictor
from .stabilize import PointStabilizer
from .ui import theme
from .ui.calibration import CalibrationWidget
from .ui.keyboard import KeyboardBar
from .ui.main_window import MainWindow
from .ui.overlay import Banner, CursorRing
from .ui.preview import CameraPreview
from .ui.toolbar import CLICK_CYCLE, ClickPopup, Toolbar
from .ui.widgets import Card, IconButton, refresh_icons
from .ui.wizard import SetupWizard
from .usage import SessionRecorder, UsageStore

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:
    QTextToSpeech = None

C_A = winmouse.MOD_CONTROL | winmouse.MOD_ALT
HOTKEYS = {
    "pause": (0, winmouse.VK_PAUSE), "pause2": (C_A, ord("P")), "next": (C_A, ord("N")),
    "clutch": (C_A, ord("C")), "control": (C_A, ord("M")), "settings": (C_A, ord("S")),
    "keyboard": (C_A, ord("K")),
}
THEME_KEYS = {"high_contrast", "large_text"}
SLIDER_KEYS = {"filter_strength", "sensitivity", "rel_gain", "accel", "dwell_time", "dwell_radius",
               "kb_dwell_time", "gesture_sensitivity"}
NO_FOCUS_WINDOWS = ("toolbar", "popup", "keyboard")
BREAK_MINUTES = 40


class _Bridge(QObject):
    hotkey = Signal(str)


class App(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.state = profile.app_state()
        self.profile_name = self.state.get("last_profile", "기본")
        self.settings = profile.load_profile(self.profile_name)
        self.cameras = sorted(set(vision.list_cameras()) | {self.settings.camera_index})
        self.camera_names = {}
        self.calibrator = None
        self.picking = False
        self.roi_mode = False
        self.wizard = None
        self.calib_dialog = None
        self._last_voice = {}
        self._hover = (None, 0.0, 0.0)
        self._status_t = 0.0
        self._last_break = time.monotonic()
        self._sessions_cache = None
        screen = qapp.primaryScreen()
        self.dpr = screen.devicePixelRatio()
        self.origin = screen.geometry().topLeft()

        self.tts = None
        if QTextToSpeech is not None:
            try:
                self.tts = QTextToSpeech(self)
                for loc in self.tts.availableLocales():
                    if loc.language() == QLocale.Language.Korean:
                        self.tts.setLocale(loc)
                        break
            except Exception:
                self.tts = None

        # 데이터
        self.store = UsageStore()
        self.recorder = SessionRecorder()
        self.predictor = Predictor()
        self._load_words()
        self.stabilizer = PointStabilizer()
        self.gesture_detector = GestureDetector(self.settings.gesture_sensitivity)

        # 화면 요소
        self.ring, self.banner = CursorRing(), Banner()
        self.toolbar, self.popup = Toolbar(), ClickPopup()
        self.keyboard = KeyboardBar(self.predictor)
        self.popup.chosen.connect(self._popup_chosen)
        tb = self.toolbar
        tb.pauseClicked.connect(self.toggle_pause)
        tb.clickModeClicked.connect(self._cycle_click_mode)
        tb.scrollUp.connect(lambda: self.engine.scroll_last_target(3))
        tb.scrollDown.connect(lambda: self.engine.scroll_last_target(-3))
        tb.keyboardClicked.connect(self.toggle_keyboard)
        tb.saveClicked.connect(self.save_session)
        tb.nextPart.connect(self.next_part)
        tb.clutch.connect(lambda: self.engine.toggle_clutch(time.monotonic(), timed=True))
        tb.settings.connect(self.show_window)
        tb.quickAction.connect(lambda a: self.run_action(a, "toolbar"))
        kb = self.keyboard
        kb.actionRequested.connect(lambda a: self.run_action(a, "keyboard"))
        kb.wordTyped.connect(self._word_typed)
        kb.charsTyped.connect(self.recorder.on_typed)
        kb.closed.connect(lambda: self.toolbar.set_keyboard_open(False))

        self.engine = Engine(self.settings, hooks=self)
        self._apply_theme()
        self.window = MainWindow(self)
        self.window.closeEvent = self._window_close
        self._apply_part_config()

        self.worker = vision.VisionWorker(self.settings.camera_index)
        self.worker.result.connect(self.on_result)
        self.worker.status.connect(self._on_status)
        self.worker.cameras.connect(self._on_cameras)
        self._update_sources()
        if self.settings.part_id == "roi" and self.settings.roi_box:
            self.worker.track_roi(tuple(self.settings.roi_box))

        self._save_timer = QTimer(self, singleShot=True, interval=700, timeout=self._save)
        self._live_timer = QTimer(self, interval=100, timeout=self._live_update)
        self._live_timer.start()
        self._minute_timer = QTimer(self, interval=30000, timeout=self._wellbeing_check)
        self._minute_timer.start()
        self._bridge = _Bridge()
        self._bridge.hotkey.connect(self._on_hotkey)
        self.hotkeys = winmouse.HotkeyListener(HOTKEYS, self._bridge.hotkey.emit)
        self.hotkeys.start()
        atexit.register(self._safety_release)

        self.worker.start()
        tb.set_click_mode("menu" if self.settings.dwell_popup else self.settings.click_type)
        tb.set_relative(self.settings.mode == "relative")
        self._refresh_quick_actions()
        tb.place()
        tb.show()
        self.window.show()
        self.alert("paused")   # NFR-09: 시작은 항상 일시정지
        if not self.state.get("wizard_done"):
            QTimer.singleShot(400, self.run_wizard)

    # ================= 좌표 =================
    def logical(self, pt) -> QPoint:
        return QPoint(int(pt[0] / self.dpr) + self.origin.x(), int(pt[1] / self.dpr) + self.origin.y())

    # ================= 엔진 hooks =================
    def _own_windows(self):
        return [w for w in QApplication.topLevelWidgets() if w.isVisible() and w not in (self.ring, self.banner)]

    def _window_at(self, pt):
        root = winmouse.root_window_at(*pt)
        wins = self._own_windows()
        by_id = {int(w.winId()): w for w in wins}
        if root in by_id:
            return by_id[root]
        if root in (int(self.ring.winId()), int(self.banner.winId())):
            lp = self.logical(pt)
            for w in (self.popup, self.keyboard, self.toolbar, *wins):
                if w.isVisible() and w.frameGeometry().contains(lp):
                    return w
        return None

    def ui_target(self, pt):
        w = self._window_at(pt)
        return (w.objectName() or w.__class__.__name__) if w else None

    def dwell_time_for(self, own):
        return self.settings.kb_dwell_time if own == "keyboard" else self.settings.dwell_time

    def ui_dwell(self, pt):
        w = self._window_at(pt)
        if w is not None and w.objectName() in NO_FOCUS_WINDOWS:
            child = w.childAt(w.mapFromGlobal(self.logical(pt)))
            while child is not None and not isinstance(child, QAbstractButton):
                child = child.parentWidget()
            if child is not None and child.isEnabled():
                child.click()
            return
        winmouse.left_click()      # 설정 창·마법사는 실제 클릭으로 조작

    def popup_visible(self):
        return self.popup.isVisible()

    def show_popup(self, pt):
        self.popup.popup_at(self.logical(pt), pt)

    def hide_popup(self):
        self.popup._timer.stop()
        self.popup.hide()

    def on_engine_event(self, name, *args):
        if name == "click":
            kind, pt = args
            self.recorder.on_click(kind, pt, (self.engine.w, self.engine.h))
            if self.keyboard.isVisible():
                self.keyboard.reset_context()     # 다른 곳을 눌렀으니 이어 쓰던 단어 맥락을 끊는다
        elif name == "popup_cancel":
            self.recorder.on_popup_cancel()
        elif name == "lost":
            self.recorder.on_lost()

    MESSAGES = {
        "found": ("다시 찾았어요", "ok", None),
        "override": ("마우스가 움직여서 잠시 양보할게요", "info", None),
        "drag_start": ("드래그 중 — 놓을 곳에 머무르세요", "info", "드래그를 시작합니다. 놓을 곳에 머무르세요."),
        "drag_end": ("드래그를 놓았어요", "ok", None),
        "scroll_start": ("스크롤 모드 — 위·아래로 움직이세요. 가운데 머무르면 끝나요", "info", "스크롤 모드입니다."),
        "scroll_end": ("스크롤 모드를 마쳤어요", "ok", None),
        "clutch_on": ("커서 고정 — 부위를 편한 위치로 옮기세요", "info", "커서를 고정했습니다."),
        "clutch_off": ("커서 고정 해제", "ok", None),
    }

    def alert(self, kind: str):
        b = self.banner
        part = get_part(self.settings.part_id).name
        if kind == "lost":
            b.set_sticky("lost", f"‘{part}’을(를) 놓쳤어요 — 카메라에 보이게 해 주세요")
            self.say("추적이 끊겼습니다", key="lost", every=5)
        elif kind == "found":
            b.set_sticky("lost", None)
            b.set_sticky("frame", None)
            b.flash(*self.MESSAGES["found"][:2], ms=900)
        elif kind == "paused":
            # 화면 위를 계속 가리지 않도록 잠깐만 보여 준다. 상태는 툴바 '재개' 버튼과 점선 원으로 계속 표시된다.
            b.flash("클릭이 멈춰 있어요 — 툴바 ‘재개’에 머무르거나 Pause 키", "info", 4000)
            self.toolbar.set_paused(True)
            self.say("클릭 일시정지")
        elif kind == "resumed":
            b.flash("클릭을 다시 시작했어요", "ok", 1200)
            self.toolbar.set_paused(False)
            self.say("클릭 재개")
        elif kind == "control_off":
            b.set_sticky("control", "카메라 커서가 꺼져 있어요 — Ctrl+Alt+M 으로 켜기", "info")
            self.say("카메라 커서 제어를 껐습니다")
        elif kind == "control_on":
            b.set_sticky("control", None)
            self.say("카메라 커서 제어를 켰습니다")
        elif kind in self.MESSAGES:
            text, level, voice = self.MESSAGES[kind]
            b.flash(text, level)
            if voice:
                self.say(voice)

    def say(self, text: str, key: str | None = None, every: float = 0):
        if not self.settings.voice_alert:
            return
        now = time.monotonic()
        if key and now - self._last_voice.get(key, -1e9) < every:
            return
        if key:
            self._last_voice[key] = now
        if self.tts is not None:
            self.tts.say(text)
        else:
            QApplication.beep()

    # ================= 프레임 처리 =================
    def _previews(self):
        ps = list(self.window.previews) if self.window.isVisible() else []
        if self.wizard is not None and self.wizard.isVisible():
            ps.extend(self.wizard.previews)
        if self.calib_dialog is not None and self.calib_dialog.isVisible():
            ps.append(self.calib_dialog.preview)
        return ps

    def on_result(self, res: vision.FrameResult):
        previews = self._previews()
        for p in previews:
            p.set_frame(res)
        self.worker.send_preview = bool(previews)
        t = res.t
        part = get_part(self.settings.part_id)

        sample = vision.extract_sample(res, part) if res.camera_ok else None
        pt = self.stabilizer(sample, t)
        cal = self.calibrator
        if cal is not None and cal.active:
            cal.feed(pt, t)
            if pt is None:
                self.engine.on_point(None, t)
        else:
            self.engine.on_point(pt, t)
        self.recorder.on_frame(part.id, pt, self.engine.pos, t, self.engine.lost or pt is None, self.engine.w)

        # 표정 제스처 (tap: 한 번 실행 / hold: 표정을 유지하는 동안)
        modes = {gid: ("hold" if get_action(aid) and get_action(aid).kind == "hold" else "tap")
                 for gid, aid in self.settings.gestures.items()}
        if self._gcal is not None:
            modes = {}                         # 표정 맞추는 중에는 실행하지 않는다
        if res.blend is not None or modes or self.gesture_detector.holding():
            for gid, ev in self.gesture_detector.update(res.blend, t, modes):
                self._gesture_event(gid, ev)
        if self._gcal is not None and res.blend is not None:
            self._gcal["scores"].append(self.gesture_detector.scores.get(self._gcal["gid"], 0.0))

        # 하체가 화면에 덜 보이면 배치 안내
        lc = self.stabilizer.low_conf_since
        need_frame = part.source == "pose" and lc is not None and t - lc > 1.5
        self.banner.set_sticky("frame", f"‘{part.name}’이(가) 잘 안 보여요 — 카메라를 낮추거나 조금 멀리 두세요"
                               if need_frame else None, "info")
        self.banner.set_sticky("light", "너무 어두워요 — 조명을 밝혀 주세요" if res.low_light else None)
        self._update_ring(calibrating=cal is not None and cal.active)
        if t - self._status_t > 0.25:
            self._status_t = t
            self._update_status(res)

    def _hover_scroll(self, t):
        """툴바 위/아래 버튼 위에 있는 동안 계속 스크롤 (가상 스크롤 버튼, FR-33)."""
        e = self.engine
        btn = None
        if not e.lost and self.toolbar.isVisible():
            child = self.toolbar.childAt(self.toolbar.mapFromGlobal(self.logical(e.pos)))
            btn = child if child in self.toolbar.scroll_buttons else None
        cur, since, last = self._hover
        if btn is not cur:
            self._hover = (btn, t, 0.0)
            return
        if btn is not None and t - since > 0.4 and t - last > 0.15:
            e.scroll_last_target(self.toolbar.scroll_buttons[btn])
            self._hover = (btn, since, t)

    def _update_ring(self, calibrating=False):
        e = self.engine
        if not e.control_enabled:
            self.ring.hide()
            return
        center = self.logical(e.scroll_anchor or e.pos)
        on_kb = self.keyboard.isVisible() and self.keyboard.frameGeometry().contains(center)
        st = {
            "center": (center.x(), center.y()),
            "progress": 0 if calibrating else e.dwell.progress,
            "paused": e.paused and not on_kb,
            "lost": e.lost,
            "drag": e.drag_active,
            "clutched": e.mapper.clutched,
            "color_key": "keyboard" if on_kb else "menu" if self.settings.dwell_popup else self.settings.click_type,
        }
        if e.scroll_anchor is not None:
            st["scroll"] = (max(self.settings.dwell_radius, 30) / self.dpr, (e.pos[1] - e.scroll_anchor[1]) / self.dpr)
        self.ring.update_state(st)

    def _update_status(self, res):
        e = self.engine
        part = get_part(self.settings.part_id).name
        if not res.camera_ok:
            title, sub, tone, mini = "카메라를 찾고 있어요", "카메라 연결을 확인해 주세요.", "care", "카메라 없음"
        elif e.lost:
            title, sub, tone, mini = f"‘{part}’을(를) 찾는 중", "고른 부위가 카메라에 보이게 해 주세요.", "care", "추적 끊김"
        elif e.paused:
            title, sub, tone, mini = "클릭이 멈춰 있어요", f"커서만 움직여요 · {part}", "accent", "일시정지"
        else:
            title, sub, tone, mini = "사용 중", f"{part}(으)로 조작하고 있어요 · {res.fps:.0f} fps", "ok", "사용 중"
        self.window.set_status(title, sub, e.paused, tone, f"● {mini}")

    def _live_update(self):
        if self.window.isVisible() and self.window.current_page() == "gesture":
            self.window.pages["gesture"].update_live(self.gesture_detector)

    def _on_status(self, msg: str):
        if "끊겼" in msg or "찾을 수 없" in msg or "오류" in msg or "못했" in msg:
            self.banner.set_sticky("camera", msg.split(" (")[0])
            self.say(msg.split(" (")[0], key="camera", every=15)
            print("[vision]", msg)
        elif msg == "카메라 연결됨":
            self.banner.set_sticky("camera", None)
        elif msg.startswith("인식 엔진"):
            self.banner.flash("인식 엔진을 준비하고 있어요…", "info", 4000)

    def _on_cameras(self, names: list):
        self.camera_names = dict(enumerate(names))
        self.cameras = list(range(len(names))) or [0]
        if self.window.current_page() == "settings":
            self.window.refresh()

    # ================= 동작 실행 (키보드·툴바·제스처 공용) =================
    def run_action(self, action_id: str, source: str):
        a = get_action(action_id)
        if a is None:
            return
        if self.engine.paused and source == "gesture" and action_id not in ALLOWED_WHILE_PAUSED:
            return
        for step in macro_steps(action_id):
            s = get_action(step)
            if s.kind == "keys":
                winmouse.press_combo(*s.keys)
                time.sleep(0.06)
            elif step in ("click", "double_click", "right_click"):
                self.engine.perform({"click": "left", "double_click": "double", "right_click": "right"}[step],
                                    self.engine.pos)
            elif step == "toggle_pause":
                self.toggle_pause()
            elif step == "toggle_keyboard":
                self.toggle_keyboard()
            elif step == "next_part":
                self.next_part()
        self.recorder.on_action(action_id, source)

    def _gesture_fired(self, gid):
        aid = self.settings.gestures.get(gid)
        if not aid:
            return
        a = get_action(aid)
        self.recorder.on_gesture(gid)
        if self.engine.paused and aid not in ALLOWED_WHILE_PAUSED:
            self.banner.flash(f"{GESTURES[gid].label} — 클릭이 멈춰 있어 실행하지 않았어요", "info", 1500)
            return
        self.banner.flash(f"{GESTURES[gid].label}  →  {a.label}", "ok", 1300)
        self.run_action(aid, "gesture")

    def _word_typed(self, word, prev):
        if self.settings.learn_words:
            self.recorder.on_word(word, prev)

    # ================= 사용자 동작 =================
    def toggle_pause(self):
        self.engine.toggle_pause()
        self._update_status_now()

    def _update_status_now(self):
        self._status_t = 0

    def toggle_keyboard(self):
        if self.keyboard.isVisible():
            self.keyboard._close()
        else:
            self.keyboard.place(self.toolbar.width() + 12)
            self.keyboard.show()
            self.keyboard.raise_()
            self.toolbar.set_keyboard_open(True)
            self.say("키보드")

    def _on_hotkey(self, name):
        if name in ("pause", "pause2"):
            self.toggle_pause()
        elif name == "next":
            self.next_part()
        elif name == "clutch":
            self.engine.toggle_clutch(time.monotonic())
        elif name == "control":
            self.engine.set_control(not self.engine.control_enabled)
        elif name == "settings":
            self.show_window()
        elif name == "keyboard":
            self.toggle_keyboard()

    def _popup_chosen(self, key):
        if key == "cancel":
            self.recorder.on_popup_cancel()
        elif self.popup.target is not None:
            self.engine.perform(key, self.popup.target)

    def _cycle_click_mode(self):
        keys = [k for k, _, _ in CLICK_CYCLE]
        cur = "menu" if self.settings.dwell_popup else self.settings.click_type
        nxt = keys[(keys.index(cur) + 1) % len(keys)]
        if nxt == "menu":
            self.update("dwell_popup", True)
        else:
            self.settings.click_type = nxt
            self.update("dwell_popup", False)
        text = next(t for k, _, t in CLICK_CYCLE if k == nxt)
        self.banner.flash(f"머무르면: {text}", "info", 1200)
        self.say(f"클릭 방식 {text}")

    def update(self, key, value):
        setattr(self.settings, key, value)
        self.engine.apply_settings(self.settings)
        if key == "gesture_sensitivity":
            self.gesture_detector.sensitivity = value
        if key in THEME_KEYS:
            self._apply_theme()
        if key == "mode":
            self.toolbar.set_relative(value == "relative")
            self.toolbar.place()
        if key in ("dwell_popup", "click_type"):
            self.toolbar.set_click_mode("menu" if self.settings.dwell_popup else self.settings.click_type)
        if key in ("quick_actions", "auto_quick"):
            self._refresh_quick_actions()
        if key not in SLIDER_KEYS:
            self.window.refresh()
        self._save_timer.start()

    def _apply_part_config(self):
        part = get_part(self.settings.part_id)
        self.stabilizer.configure(part.min_conf, part.smooth)

    def set_part(self, pid):
        part = get_part(pid)
        self.settings.part_id = part.id
        hint = "" if (part.id in self.settings.calibrations or part.id == "roi") else " — 가동 범위를 측정하면 더 편해요"
        self.banner.flash(f"이제 ‘{part.name}’(으)로 움직여요{hint}", "info", 2500)
        self.say(part.name)
        if self.picking:
            self.window.pick_btn.setChecked(False)
        self.engine.apply_settings(self.settings)
        self._apply_part_config()
        self._update_sources()
        self.window.refresh()
        if self.wizard is not None:
            self.wizard._sync_part_buttons()
        self._save_timer.start()

    def next_part(self):
        favs = [f for f in self.settings.favorites if f]
        if not favs:
            return
        cur = self.settings.part_id
        self.set_part(favs[(favs.index(cur) + 1) % len(favs)] if cur in favs else favs[0])

    def toggle_favorite(self):
        favs, pid = self.settings.favorites, self.settings.part_id
        favs.remove(pid) if pid in favs else favs.append(pid)
        self.window.refresh()
        self._save_timer.start()

    def set_picking(self, on):
        self.picking = on
        self.window.preview.picking = on
        if on and self.window.roi_btn.isChecked():
            self.window.roi_btn.setChecked(False)
        self._update_sources()
        self.window.refresh()

    def set_roi_mode(self, on):
        self.roi_mode = on
        self.window.preview.roi_mode = on
        if on and self.window.pick_btn.isChecked():
            self.window.pick_btn.setChecked(False)
        self.window.refresh()

    def set_roi(self, box):
        self.settings.roi_box = list(box)
        self.worker.track_roi(tuple(box))
        self.window.roi_btn.setChecked(False)
        self.set_part("roi")

    def on_page_changed(self, key):
        if hasattr(self, "worker"):
            self._update_sources()

    def _update_sources(self):
        part = get_part(self.settings.part_id)
        if self.picking:
            sources, model = {"face", "pose", "hand"}, "lite"
        else:
            sources, model = {part.source}, ("full" if part.full_model else "lite")
        if self.settings.gestures or (self.window.isVisible() and self.window.current_page() == "gesture"):
            sources.add("face")             # 표정 제스처
        self.worker.set_sources(sources, model)

    def change_camera(self, index):
        if index is None:
            return
        self.settings.camera_index = index
        self.worker.change_camera(index)
        self._save_timer.start()

    def camera_label(self, i):
        return self.camera_names.get(i, f"카메라 {i}")

    def apply_calibration(self, calib):
        self.engine.set_calibration(calib)
        self.window.refresh()
        self._save_timer.start()
        self.say("측정을 마쳤습니다")

    def open_calibration(self):
        if self.calib_dialog is None:
            dlg = QDialog(self.window)
            dlg.setWindowTitle("가동 범위 측정")
            fit_to_screen(dlg, 1180, 680)
            lay = QHBoxLayout(dlg)
            lay.setContentsMargins(24, 24, 24, 24)
            lay.setSpacing(18)
            card = Card(margins=24)
            w = CalibrationWidget()
            w.finished.connect(self.apply_calibration)
            w.stepChanged.connect(self.say)
            card.add(w, 1)
            close = IconButton("close", "닫기")
            close.clicked.connect(dlg.close)
            card.add(close)
            lay.addWidget(card, 2)
            dlg.preview = CameraPreview()
            lay.addWidget(dlg.preview, 3)
            dlg.calib = w
            dlg.finished.connect(lambda _: (w.cancel(), setattr(self, "calibrator", None)))
            self.calib_dialog = dlg
        self.calib_dialog.preview.part = get_part(self.settings.part_id)
        self.calibrator = self.calib_dialog.calib
        self.calib_dialog.show()
        self.calib_dialog.raise_()

    def run_wizard(self):
        if self.wizard is not None and self.wizard.isVisible():
            self.wizard.raise_()
            return
        self.wizard = SetupWizard(self)
        self.wizard.finished.connect(self._wizard_done)
        self.wizard.show()

    def _wizard_done(self, _):
        self.wizard = None
        self.window.refresh()

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _window_close(self, event):
        event.ignore()
        self.window.hide()
        self.banner.flash("설정 창을 숨겼어요 — 툴바 ‘설정’으로 다시 열 수 있어요", "info", 3000)

    # ================= 기록·추천 =================
    def _load_words(self):
        words, bigrams, recent = self.store.words(self.profile_name)
        self.predictor = getattr(self, "predictor", None) or Predictor()
        self.predictor.load(words, bigrams, recent)

    def all_sessions(self):
        if self._sessions_cache is None:
            self._sessions_cache = self.store.sessions(self.profile_name)
        return self._sessions_cache

    def top_words(self, k):
        words, _, _ = self.store.words(self.profile_name)
        c = Counter(words)
        c.update(self.recorder.words)
        return c.most_common(k)

    def quick_actions(self):
        out = list(self.settings.quick_actions)
        if self.settings.auto_quick:
            out += [a for a in auto_quick_actions(self.all_sessions()) if a not in out]
        return out[:3]

    def _refresh_quick_actions(self):
        self.toolbar.set_quick_actions(self.quick_actions())
        self.toolbar.place()

    def save_session(self):
        if not self.recorder.has_data():
            self.banner.flash("아직 저장할 기록이 없어요", "info", 2000)
            return
        before = {r.id for r in recommend(self.all_sessions(), self.settings)}
        self.store.save(self.profile_name, self.recorder, None, self.settings.learn_words)
        self.recorder.reset()
        self._sessions_cache = None
        self._refresh_quick_actions()
        new = [r for r in recommend(self.all_sessions(), self.settings) if r.id not in before]
        msg = "기록을 저장했어요" + (f" · 새 추천 {len(new)}개" if new else "")
        self.banner.flash(msg, "ok", 2500)
        self.say("기록을 저장했습니다")
        self.window.refresh()

    def export_csv(self):
        docs = Path.home() / "Documents"
        docs.mkdir(exist_ok=True)
        path = docs / f"BodyPointer_기록_{self.profile_name}_{time.strftime('%Y%m%d')}.csv"
        self.store.export_csv(self.profile_name, path)
        QMessageBox.information(self.window, "내보내기 완료", f"기록을 저장했어요.\n\n{path}")

    def bind_gesture(self, gid, action_id):
        if action_id:
            self.settings.gestures[gid] = action_id
            self.banner.flash(f"{GESTURES[gid].label} → {get_action(action_id).label}", "ok", 1500)
        else:
            self.settings.gestures.pop(gid, None)
        self._update_sources()
        self.window.refresh()
        self._save_timer.start()

    def apply_recommendation(self, rec):
        a = rec.apply
        kind = a.get("type")
        if kind == "set":
            self.update(a["key"], a["value"])
        elif kind == "bind_gesture":
            self.bind_gesture(a["gesture"], a["action"])
        elif kind == "add_quick":
            if a["action"] not in self.settings.quick_actions:
                self.update("quick_actions", self.settings.quick_actions + [a["action"]])
        elif kind == "part":
            self.set_part(a["part"])
        self.settings.dismissed_recs.append(rec.id)
        self.banner.flash(f"적용했어요 — {rec.title}", "ok", 2000)
        self.window.refresh()
        self._save_timer.start()

    def dismiss_recommendation(self, rec_id):
        self.settings.dismissed_recs.append(rec_id)
        self.window.refresh()
        self._save_timer.start()

    def _wellbeing_check(self):
        now = time.monotonic()
        if self.settings.break_reminder and now - self._last_break >= BREAK_MINUTES * 60:
            self._last_break = now
            self.banner.flash(f"{BREAK_MINUTES}분 동안 쓰셨어요. 잠시 쉬어 가세요", "info", 6000)
            self.say("잠시 쉬어 가세요")
        if self.settings.fatigue_alert and self.recorder.fatigue_check(now):
            self.banner.flash("움직임이 처음보다 많이 줄었어요. 쉬거나 다른 부위로 바꿔 보세요", "info", 7000)
            self.say("잠시 쉬거나 다른 부위로 바꿔 보세요")

    # ================= 프로필 =================
    def profile_names(self):
        names = profile.list_profiles()
        return names if self.profile_name in names else [*names, self.profile_name]

    def switch_profile(self, name):
        if name == self.profile_name:
            return
        self._save()
        self.profile_name = name
        self.settings = profile.load_profile(name)
        self.predictor.counts.clear()
        self.predictor.bigrams.clear()
        self.predictor.__init__()
        self._load_words()
        self._sessions_cache = None
        self._reload_settings()

    def new_profile(self, name):
        self._save()
        self.profile_name = name
        self._sessions_cache = None
        self._save()
        self.window.refresh()

    def _reload_settings(self):
        self.engine.apply_settings(self.settings)
        self.gesture_detector.sensitivity = self.settings.gesture_sensitivity
        self._apply_theme()
        self._apply_part_config()
        self._update_sources()
        self.worker.change_camera(self.settings.camera_index)
        if self.settings.part_id == "roi" and self.settings.roi_box:
            self.worker.track_roi(tuple(self.settings.roi_box))
        self.toolbar.set_click_mode("menu" if self.settings.dwell_popup else self.settings.click_type)
        self.toolbar.set_relative(self.settings.mode == "relative")
        self._refresh_quick_actions()
        self.window.refresh_all()
        self._save()

    def delete_all(self):
        profile.delete_all_data()
        self.state = {"consent": False, "last_profile": "기본", "wizard_done": False}
        self.profile_name = "기본"
        self.settings = profile.Settings()
        self.recorder.reset()
        self.predictor.__init__()
        self._sessions_cache = None
        self._reload_settings()
        profile.delete_all_data()   # _reload_settings가 저장한 기본값까지 지운다
        QMessageBox.information(self.window, "삭제 완료", "모든 기록을 삭제했어요.")

    def _save(self):
        profile.save_profile(self.profile_name, self.settings)
        self.state["last_profile"] = self.profile_name
        self.save_state()

    def save_state(self):
        profile.save_app_state(self.state)

    # ================= 기타 =================
    def _apply_theme(self):
        theme.configure(self.settings.high_contrast, self.settings.large_text)
        self.qapp.setFont(theme.ui_font())
        self.qapp.setStyleSheet(theme.stylesheet())
        for w in (self.toolbar, self.popup, self.keyboard):
            w.restyle()
            refresh_icons(w)
        if hasattr(self, "window"):
            refresh_icons(self.window)
            self.window.refresh_all()

    def _safety_release(self):
        if getattr(self, "engine", None) and self.engine.drag_active:
            winmouse.left_up()

    def quit(self, ask=True):
        if ask and self.recorder.has_data():
            r = QMessageBox.question(self.window, "종료", "이번에 사용한 기록을 저장할까요?\n저장하면 맞춤 추천과 자동완성에 쓰여요.",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Save:
                self.save_session()
        self._save()
        self.engine._end_modes()
        self.hotkeys.stop()
        self.worker.stop()
        self.qapp.quit()
