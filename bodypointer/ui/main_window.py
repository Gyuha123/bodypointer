"""메인 창: 왼쪽 사이드바 + 페이지. 모든 조작은 큰 버튼 위주라 머무르기만으로 쓸 수 있다 (FR-60, FR-61)."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QMainWindow, QMessageBox, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..actions import ACTIONS, get_action
from ..gestures import GESTURE_ORDER, GESTURES
from ..insights import aggregate, headline_stats, recommend
from ..parts import PARTS, get_part
from . import theme
from .charts import BarChart, HBarChart, HeatMap, LineChart
from .preview import CameraPreview
from .widgets import (Card, IconButton, IconLabel, Meter, Segmented, SliderRow, ToggleRow, fit_to_screen, label,
                      set_prop)

NAV = [("home", "home", "홈"), ("parts", "person", "부위"), ("move", "mouse", "움직임"), ("click", "touch", "클릭"),
       ("gesture", "emoji", "제스처"), ("stats", "chart", "기록"), ("insights", "bulb", "추천"),
       ("settings", "settings", "설정")]


def page(title: str, subtitle: str = ""):
    """스크롤 가능한 페이지 틀. (바깥 위젯, 내용 레이아웃)"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(36, 30, 36, 36)
    lay.setSpacing(18)
    lay.addWidget(label(title, "h1"))
    if subtitle:
        lay.addWidget(label(subtitle, "muted", wrap=True))
    scroll.setWidget(inner)
    return scroll, lay


def row(*widgets, spacing=12, stretch_last=False):
    h = QHBoxLayout()
    h.setSpacing(spacing)
    for i, w in enumerate(widgets):
        if w is None:
            h.addStretch()
        elif isinstance(w, QWidget):
            h.addWidget(w, 1 if (stretch_last and i == len(widgets) - 1) else 0)
        else:
            h.addLayout(w)
    return h


class StatTile(QFrame):
    def __init__(self, icon_name, title):
        super().__init__()
        self.setProperty("role", "tile")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(IconLabel(icon_name, 18))
        head.addWidget(label(title, "small"))
        head.addStretch()
        v.addLayout(head)
        self.value = label("–", "big")
        v.addWidget(self.value)

    def set(self, text):
        self.value.setText(text)


# =========================== 페이지 ===========================
class HomePage(QWidget):
    def __init__(self, app, nav):
        super().__init__()
        self.app = app
        outer, lay = page("홈", "카메라로 몸의 한 부위를 마우스처럼 써요. 원하는 곳에 잠시 머무르면 클릭돼요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.setSpacing(18)
        # 상태 카드
        self.status_card = Card(margins=26, spacing=14)
        self.state_title = label("시작 중…", "big")
        self.state_sub = label("", "muted", wrap=True)
        self.status_card.add(self.state_title)
        self.status_card.add(self.state_sub)
        self.pause_btn = IconButton("play", "클릭 시작하기", "primary", icon_size=24)
        self.pause_btn.setMinimumHeight(64)
        self.pause_btn.clicked.connect(app.toggle_pause)
        self.status_card.add(self.pause_btn)
        kb = IconButton("keyboard", "키보드 열기")
        kb.clicked.connect(app.toggle_keyboard)
        save = IconButton("save", "기록 저장")
        save.clicked.connect(app.save_session)
        self.status_card.add(row(kb, save))
        self.status_card.body.addStretch()
        top.addWidget(self.status_card, 2)
        # 카메라
        cam = Card("카메라", margins=18)
        self.preview = CameraPreview()
        self.preview.setMinimumHeight(300)
        cam.add(self.preview, 1)
        top.addWidget(cam, 3)
        lay.addLayout(top)

        # 이번 주 요약
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_time = StatTile("history", "최근 7일 사용 시간")
        self.t_clicks = StatTile("touch", "클릭")
        self.t_acc = StatTile("check", "클릭 정확도")
        self.t_saved = StatTile("keyboard", "자동완성으로 아낀 글자")
        for t in (self.t_time, self.t_clicks, self.t_acc, self.t_saved):
            tiles.addWidget(t)
        lay.addLayout(tiles)

        # 추천 + 빠른 설정
        bottom = QHBoxLayout()
        bottom.setSpacing(18)
        self.rec_card = Card("맞춤 추천", "사용 기록을 바탕으로 골랐어요.")
        self.rec_box = QVBoxLayout()
        self.rec_card.add(self.rec_box)
        more = IconButton("chevron_right", "추천 모두 보기", "ghost")
        more.clicked.connect(lambda: nav("insights"))
        self.rec_card.add(more)
        bottom.addWidget(self.rec_card, 3)
        setup = Card("처음이라면", "순서대로 따라 하면 3분이면 끝나요.")
        b1 = IconButton("wizard", "설정 마법사")
        b1.clicked.connect(app.run_wizard)
        b2 = IconButton("ruler", "가동 범위 측정")
        b2.clicked.connect(app.open_calibration)
        b3 = IconButton("person", "부위 바꾸기")
        b3.clicked.connect(lambda: nav("parts"))
        for b in (b1, b2, b3):
            setup.add(b)
        setup.body.addStretch()
        bottom.addWidget(setup, 2)
        lay.addLayout(bottom)
        lay.addStretch()

    def refresh(self):
        s = headline_stats(self.app.all_sessions())
        self.t_time.set(f"{s['minutes']:.0f}분")
        self.t_clicks.set(f"{s['clicks']:,}")
        self.t_acc.set("–" if s["accuracy"] is None else f"{s['accuracy'] * 100:.0f}%")
        self.t_saved.set(f"{s['saved']:,}")
        while self.rec_box.count():
            w = self.rec_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in recommend(self.app.all_sessions(), self.app.settings)[:2]:
            self.rec_box.addWidget(RecCard(r, self.app, compact=True))

    def set_status(self, title, sub, paused, tone):
        self.state_title.setText(title)
        self.state_sub.setText(sub)
        self.pause_btn.setText("클릭 시작하기" if paused else "클릭 잠시 멈추기")
        self.pause_btn.set_icon_name("play" if paused else "pause")
        set_prop(self.status_card, "tone", tone)


class PartsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("부위", "마우스로 쓸 몸의 부위를 고르세요. 편하게, 오래 움직일 수 있는 곳이 좋아요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        h.setSpacing(18)

        left = Card(margins=18)
        self.current = label("", "h2")
        left.add(self.current)
        self.preview = CameraPreview()
        self.preview.setFixedHeight(420)
        self.preview.partPicked.connect(app.set_part)
        self.preview.roiSelected.connect(app.set_roi)
        left.add(self.preview, 1)
        self.hint = label("", "muted", wrap=True)
        left.add(self.hint)
        self.pick_btn = IconButton("touch", "화면에서 고르기", checkable=True)
        self.pick_btn.toggled.connect(app.set_picking)
        self.roi_btn = IconButton("select_all", "영역 직접 지정", checkable=True)
        self.roi_btn.toggled.connect(app.set_roi_mode)
        self.fav_btn = IconButton("star", "즐겨찾기")
        self.fav_btn.clicked.connect(app.toggle_favorite)
        left.add(row(self.pick_btn, self.roi_btn, self.fav_btn))
        calib = IconButton("ruler", "이 부위의 가동 범위 측정", "primary")
        calib.clicked.connect(app.open_calibration)
        left.add(calib)
        h.addWidget(left, 3, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.chips = {}
        groups = {}
        for p in PARTS:
            groups.setdefault(p.group, []).append(p)
        for g, parts in groups.items():
            sub = {"다리": "다리가 카메라에 보이도록 카메라를 낮추거나 조금 뒤로 두세요.",
                   "직접 지정": "목록에 없는 부위는 ‘영역 직접 지정’으로 네모를 그려 쓰세요."}.get(g)
            card = Card(g, sub, margins=16, spacing=10)
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, p in enumerate(parts):
                b = IconButton("star_fill" if p.id in app.settings.favorites else "person", p.name, checkable=True,
                               icon_size=16)
                b.setProperty("role", "chip")
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.clicked.connect(lambda _=False, pid=p.id: self._choose(pid))
                grid.addWidget(b, i // 2, i % 2)
                self.chips[p.id] = b
            card.add(grid)
            right.addWidget(card)
        right.addStretch()
        h.addLayout(right, 2)
        lay.addLayout(h)

    def _choose(self, pid):
        if pid == "roi" and not self.app.settings.roi_box:
            self.roi_btn.setChecked(True)
        self.app.set_part(pid)

    def refresh(self):
        s = self.app.settings
        part = get_part(s.part_id)
        self.current.setText(f"지금 쓰는 부위 · {part.name}")
        for pid, b in self.chips.items():
            b.setChecked(pid == s.part_id)
            b.set_icon_name("star_fill" if pid in s.favorites else "person")
        fav = s.part_id in s.favorites
        self.fav_btn.setText("즐겨찾기 해제" if fav else "즐겨찾기")
        self.fav_btn.set_icon_name("star_fill" if fav else "star")
        self.preview.part = part
        if self.roi_btn.isChecked():
            self.hint.setText("카메라 화면에서 쓰고 싶은 부분을 네모로 끌어 감싸세요. (머무르기 사용자는 메뉴의 ‘드래그’)")
        elif self.pick_btn.isChecked():
            self.hint.setText("노란 점 중 쓰고 싶은 부위의 점을 누르세요.")
        else:
            measured = "측정됨" if s.part_id in s.calibrations else "아직 측정 전이에요 — 측정하면 훨씬 편해져요"
            self.hint.setText(f"가동 범위: {measured}")


class MovePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("움직임", "커서가 어떻게 따라 움직일지 정해요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        s = app.settings
        mode = Card("이동 방식")
        self.mode = Segmented([("absolute", "mouse", "절대 모드 (추천)"), ("relative", "touch", "상대 모드")])
        self.mode.changed.connect(lambda k: app.update("mode", k))
        mode.add(self.mode)
        mode.add(label("절대 모드: 몸의 위치가 곧 화면 위치예요.  ·  상대 모드: 터치패드처럼 움직인 만큼만 이동해요. "
                       "조금밖에 못 움직이면 상대 모드가 편할 수 있어요.", "muted", wrap=True))
        lay.addWidget(mode)

        tune = Card("세부 조절", "처음엔 그대로 두고, 불편할 때만 조금씩 바꿔 보세요.")
        self.rows = {
            "filter_strength": SliderRow("떨림 보정", "커서가 떨리면 올리고, 굼뜨면 내려요.", 0, 100, 5, s.filter_strength, "{:.0f}"),
            "sensitivity": SliderRow("감도", "화면 끝까지 잘 안 닿으면 올려요. (절대 모드)", 0.5, 3.0, 0.1, s.sensitivity),
            "rel_gain": SliderRow("속도", "커서가 움직이는 거리예요. (상대 모드)", 0.3, 5.0, 0.1, s.rel_gain),
            "accel": SliderRow("가속", "빨리 움직이면 더 멀리 가요. (상대 모드)", 0.0, 1.5, 0.1, s.accel),
        }
        for key, r in self.rows.items():
            r.valueChanged.connect(lambda v, k=key: app.update(k, v))
            tune.add(r)
        lay.addWidget(tune)
        calib = IconButton("ruler", "가동 범위 다시 측정", "primary")
        calib.clicked.connect(app.open_calibration)
        lay.addLayout(row(calib, None))
        lay.addStretch()

    def refresh(self):
        s = self.app.settings
        self.mode.set_current(s.mode)
        for key, r in self.rows.items():
            r.set_value(getattr(s, key))
        rel = s.mode == "relative"
        self.rows["sensitivity"].setVisible(not rel)
        self.rows["rel_gain"].setVisible(rel)
        self.rows["accel"].setVisible(rel)


class ClickPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("클릭", "원하는 곳에 잠시 머무르면 클릭돼요. 그 방식을 정해요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        s = app.settings
        dwell = Card("머무르기")
        self.rows = {
            "dwell_time": SliderRow("머무는 시간", "실수로 자꾸 눌리면 늘리고, 답답하면 줄여요.", 0.5, 3.0, 0.1, s.dwell_time, "{:.1f}초"),
            "dwell_radius": SliderRow("허용 범위", "손이 떨려 게이지가 자꾸 끊기면 넓혀요.", 15, 90, 5, s.dwell_radius, "{:.0f}px"),
            "kb_dwell_time": SliderRow("키보드 머무는 시간", "키보드 키는 더 빨리 눌리게 할 수 있어요.", 0.4, 2.0, 0.1, s.kb_dwell_time, "{:.1f}초"),
        }
        for key, r in self.rows.items():
            r.valueChanged.connect(lambda v, k=key: app.update(k, v))
            dwell.add(r)
        lay.addWidget(dwell)

        how = Card("머무르면 무엇을 할까요?")
        self.popup = ToggleRow("클릭 메뉴 보여 주기", "클릭·더블클릭·우클릭·드래그·스크롤 중 고를 수 있어요. 실수 클릭도 막아 줘요.",
                               s.dwell_popup)
        self.popup.toggled.connect(lambda v: app.update("dwell_popup", v))
        how.add(self.popup)
        how.add(label("메뉴를 끄면 바로 할 동작", "muted"))
        self.click_type = Segmented([("left", "touch", "클릭"), ("double", "touch", "더블"),
                                     ("right", "mouse", "우클릭"), ("drag", "select_all", "드래그")])
        self.click_type.changed.connect(lambda k: app.update("click_type", k))
        how.add(self.click_type)
        lay.addWidget(how)

        scroll = Card("스크롤")
        self.edge = ToggleRow("화면 위·아래 끝에서 스크롤", "커서를 화면 맨 위나 맨 아래에 두면 그쪽으로 스크롤돼요.", s.edge_scroll)
        self.edge.toggled.connect(lambda v: app.update("edge_scroll", v))
        scroll.add(self.edge)
        scroll.add(label("툴바의 ‘스크롤’을 누르면 마지막으로 클릭한 곳에서 위·아래로 움직인 만큼 스크롤돼요. "
                         "가운데에 머무르면 끝나요. 표정 제스처로도 스크롤할 수 있어요(제스처 메뉴).", "muted", wrap=True))
        lay.addWidget(scroll)
        lay.addStretch()

    def refresh(self):
        s = self.app.settings
        for key, r in self.rows.items():
            r.set_value(getattr(s, key))
        self.popup.set_checked(s.dwell_popup)
        self.edge.set_checked(s.edge_scroll)
        self.click_type.set_current(s.click_type)


class GesturePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("제스처", "표정으로 클릭·드래그·스크롤 같은 기능을 바로 써요. 얼굴이 카메라에 보이면 어떤 부위로 "
                                   "커서를 움직이든 동작해요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)

        start = Card("빠르게 시작", tone="accent")
        start.add(label("처음이라면 추천 세트를 적용해 보세요.  눈썹 올리기 = 클릭  ·  입 벌리기 = 꾹 누르기(드래그)  ·  "
                        "활짝 웃기 = 스크롤  ·  눈 지그시 감기 = 키보드", "muted", wrap=True))
        preset = IconButton("check", "추천 세트 한 번에 적용", "primary")
        preset.clicked.connect(app.apply_gesture_preset)
        clear = IconButton("delete", "모두 해제", "ghost")
        clear.clicked.connect(app.clear_gestures)
        start.add(row(preset, clear, None))
        start.add(label("‘표정 유지하는 동안’ 동작: 표정을 짓고 있는 동안만 이어져요. 예를 들어 입을 벌린 채 커서를 움직이면 "
                        "끌기(드래그), 웃는 채 위·아래로 움직이면 스크롤(마우스 휠을 누른 것처럼)돼요. 표정을 풀면 끝나요.",
                        "small", wrap=True))
        lay.addWidget(start)

        card = Card("내 제스처", "막대가 세로선을 넘으면 인식돼요. 표정을 지어 보며 확인하고, 잘 안 되면 ‘내 표정으로 맞추기’를 눌러 보세요.")
        self.meters, self.combos, self.status, self.cal_btns = {}, {}, {}, {}
        for gid in GESTURE_ORDER:
            g = GESTURES[gid]
            r = QGridLayout()
            r.setHorizontalSpacing(16)
            r.setVerticalSpacing(4)
            r.addWidget(label(g.label, "h2"), 0, 0)
            r.addWidget(label(g.hint, "small"), 1, 0)
            m = Meter()
            r.addWidget(m, 0, 1)
            st = label("", "small")
            r.addWidget(st, 1, 1)
            cb = self._action_combo()
            cb.activated.connect(lambda _=False, x=gid, c=cb: c.currentData() is not None and app.bind_gesture(x, c.currentData()))
            r.addWidget(cb, 0, 2, 2, 1)
            cal = IconButton("emoji", "내 표정으로 맞추기", "ghost", icon_size=18)
            cal.clicked.connect(lambda _=False, x=gid: app.start_gesture_calibration(x))
            r.addWidget(cal, 0, 3, 2, 1)
            r.setColumnStretch(1, 1)
            card.add(r)
            self.meters[gid], self.combos[gid], self.status[gid], self.cal_btns[gid] = m, cb, st, cal
        lay.addWidget(card)

        sens = Card("인식 민감도")
        self.sens = SliderRow("민감도", "잘 인식되지 않으면 올리고, 저절로 실행되면 내려요.", 0.6, 1.8, 0.1,
                              app.settings.gesture_sensitivity, "{:.1f}")
        self.sens.valueChanged.connect(lambda v: app.update("gesture_sensitivity", v))
        sens.add(self.sens)
        lay.addWidget(sens)

        quick = Card("툴바 빠른 실행")
        self.auto = ToggleRow("자주 쓴 기능 자동으로 올리기",
                              "기록을 저장할 때마다 가장 자주 쓴 기능 3개가 툴바에 자동으로 올라와요.", app.settings.auto_quick)
        self.auto.toggled.connect(lambda v: app.update("auto_quick", v))
        quick.add(self.auto)
        self.quick_label = label("", "muted", wrap=True)
        quick.add(self.quick_label)
        clear = IconButton("delete", "직접 추가한 빠른 실행 비우기", "ghost")
        clear.clicked.connect(lambda: app.update("quick_actions", []))
        quick.add(row(clear, None))
        lay.addWidget(quick)
        lay.addStretch()

    def refresh(self):
        s = self.app.settings
        for gid, cb in self.combos.items():
            aid = s.gestures.get(gid, "")
            idx = cb.findData(aid)
            if idx < 0 and aid:
                a = get_action(aid)
                cb.addItem(a.label if a else aid, aid)
                idx = cb.count() - 1
            cb.setCurrentIndex(max(idx, 0))
        self.sens.set_value(s.gesture_sensitivity)
        self.auto.set_checked(s.auto_quick)
        qa = [get_action(a).label for a in self.app.quick_actions() if get_action(a)]
        self.quick_label.setText("지금 툴바: " + (", ".join(qa) if qa else "아직 없어요"))

    def update_live(self, detector):
        for gid, m in self.meters.items():
            m.set(detector.scores.get(gid, 0.0), detector.threshold(gid))


class StatsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("기록", "저장한 사용 기록을 한눈에 봐요. 재활 경과 확인용 참고 자료이며 진단이 아니에요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.days = 30
        self.range = Segmented([("7", "history", "7일"), ("30", "history", "30일"), ("0", "history", "전체")])
        self.range.set_current("30")
        self.range.changed.connect(self._range)
        save = IconButton("save", "지금까지 기록 저장", "primary")
        save.clicked.connect(app.save_session)
        export = IconButton("download", "CSV로 내보내기")
        export.clicked.connect(app.export_csv)
        lay.addLayout(row(self.range, None, save, export))

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t = {k: StatTile(ic, t) for k, ic, t in [
            ("time", "history", "사용 시간"), ("sessions", "calendar", "저장한 기록"), ("acc", "check", "클릭 정확도"),
            ("typed", "keyboard", "입력한 글자")]}
        for t in self.t.values():
            tiles.addWidget(t)
        lay.addLayout(tiles)

        grid = QGridLayout()
        grid.setSpacing(18)
        self.daily = BarChart("{:.0f}분")
        self.rom = LineChart("{:.0f}%")
        self.acc = LineChart("{:.0f}%")
        self.top_actions = HBarChart()
        self.top_words = HBarChart()
        self.heat = HeatMap()
        self.rom_card = Card("움직이는 범위", "주로 쓴 부위가 카메라 화면에서 움직인 폭이에요(가로·세로 평균). "
                                          "늘면 더 크게 움직였다는 뜻이에요.")
        for i, (title, sub, chart) in enumerate([
            ("하루 사용 시간", "", self.daily),
            (None, None, self.rom),
            ("클릭 정확도", "취소하거나 바로 되돌리지 않은 클릭의 비율이에요.", self.acc),
            ("클릭한 위치", "화면에서 자주 누른 곳일수록 진해요.", self.heat),
            ("자주 쓴 기능", "", self.top_actions),
            ("자주 쓴 단어", "키보드로 입력한 단어예요. 자동완성에 쓰여요.", self.top_words),
        ]):
            c = self.rom_card if title is None else Card(title, sub or None)
            c.add(chart)
            grid.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch()

    def _range(self, key):
        self.days = int(key)
        self.refresh()

    def refresh(self):
        all_s = self.app.all_sessions()
        cutoff = time.time() - self.days * 86400 if self.days else 0
        ss = [s for s in all_s if s.get("started", 0) >= cutoff]
        agg = aggregate(ss)
        self.t["time"].set(f"{agg['duration'] / 3600:.1f}시간" if agg["duration"] >= 3600 else f"{agg['duration'] / 60:.0f}분")
        self.t["sessions"].set(f"{len(ss)}회")
        self.t["acc"].set(f"{(1 - (agg['cancels'] + agg['misclicks']) / agg['clicks']) * 100:.0f}%" if agg["clicks"] else "–")
        self.t["typed"].set(f"{agg['typed']:,}")

        ndays = self.days or max(7, min(60, int((time.time() - (ss[0]["started"] if ss else time.time())) / 86400) + 1))
        ndays = min(ndays, 31)
        today = time.localtime()
        day0 = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
        buckets = []
        for i in range(ndays - 1, -1, -1):
            start = day0 - i * 86400
            mins = sum(s["duration"] for s in ss if start <= s["started"] < start + 86400) / 60
            buckets.append((time.strftime("%m/%d", time.localtime(start)) if i % max(1, ndays // 7) == 0 or i == 0 else "", mins))
        self.daily.set_data(buckets)

        main = self.app.settings.part_id
        rs = [s for s in ss if s.get("main_part") == main and s.get("rom_x")]
        self.rom_card.body.itemAt(0).widget().setText(f"움직이는 범위 · {get_part(main).name}")
        self.rom.set_data([(time.strftime("%m/%d", time.localtime(s["started"])), (s["rom_x"] + s["rom_y"]) / 2 * 100)
                           for s in rs][-20:])
        acc = [(time.strftime("%m/%d", time.localtime(s["started"])),
                100 * (1 - (s.get("popup_cancels", 0) + s.get("misclicks", 0)) / s["click_total"]))
               for s in ss if s.get("click_total", 0) >= 3]
        self.acc.set_data(acc[-20:])
        self.heat.set_data(agg["cells"])
        self.top_actions.set_data([(get_action(a).label if get_action(a) else a, n)
                                   for a, n in agg["actions"].most_common(6)])
        words = self.app.top_words(8)
        self.top_words.set_data(words)


class RecCard(QFrame):
    TONES = {"good": "ok", "care": "care", "tip": "accent", "info": None}

    def __init__(self, rec, app, compact=False):
        super().__init__()
        self.setProperty("role", "card")
        tone = self.TONES.get(rec.level)
        if tone:
            self.setProperty("tone", tone)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(14)
        h.addWidget(IconLabel(rec.icon, 26, "accent" if rec.level == "tip" else "text"))
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(rec.title, "h2", wrap=True))
        text.addWidget(label(rec.body, "muted", wrap=True))
        h.addLayout(text, 1)
        if rec.apply:
            b = IconButton("check", rec.apply_label, "primary")
            b.clicked.connect(lambda: app.apply_recommendation(rec))
            h.addWidget(b)
        if not compact or not rec.apply:
            x = IconButton("close", "" if compact else "괜찮아요", "ghost")
            x.clicked.connect(lambda: app.dismiss_recommendation(rec.id))
            h.addWidget(x)


class InsightsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("추천", "저장한 사용 기록을 분석해서 더 편하게 쓰는 방법을 골랐어요. 적용하기 전엔 아무것도 바뀌지 않아요.")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.box = QVBoxLayout()
        self.box.setSpacing(12)
        lay.addLayout(self.box)
        reset = IconButton("refresh", "숨긴 추천 다시 보기", "ghost")
        reset.clicked.connect(lambda: app.update("dismissed_recs", []))
        lay.addLayout(row(reset, None))
        lay.addStretch()

    def refresh(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in recommend(self.app.all_sessions(), self.app.settings):
            self.box.addWidget(RecCard(r, self.app))


class SettingsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        outer, lay = page("설정")
        QVBoxLayout(self).addWidget(outer)
        self.layout().setContentsMargins(0, 0, 0, 0)
        s = app.settings

        prof = Card("사용자 프로필", "사람마다 부위·감도·기록을 따로 저장해요.")
        self.profile = QComboBox()
        self.profile.activated.connect(lambda _: app.switch_profile(self.profile.currentText()))
        new = IconButton("add", "새 프로필")
        new.clicked.connect(self._new_profile)
        prof.add(row(self.profile, new, stretch_last=False))
        lay.addWidget(prof)

        cam = Card("카메라")
        self.cam = QComboBox()
        self.cam.activated.connect(lambda _: app.change_camera(self.cam.currentData()))
        cam.add(self.cam)
        lay.addWidget(cam)

        view = Card("화면과 소리")
        self.toggles = {
            "high_contrast": ToggleRow("고대비", "검정 바탕에 노란 강조색으로 더 또렷하게 보여요.", s.high_contrast),
            "large_text": ToggleRow("큰 글씨", "", s.large_text),
            "voice_alert": ToggleRow("음성 안내", "추적이 끊기거나 상태가 바뀌면 소리로 알려 줘요.", s.voice_alert),
        }
        for k in ("high_contrast", "large_text", "voice_alert"):
            view.add(self.toggles[k])
        lay.addWidget(view)

        care = Card("쉬어 가기")
        self.toggles["break_reminder"] = ToggleRow("40분마다 휴식 알림", "", s.break_reminder)
        self.toggles["fatigue_alert"] = ToggleRow("피로 감지", "움직임이 처음보다 많이 줄면 쉬거나 다른 부위로 바꾸자고 알려 줘요.",
                                                  s.fatigue_alert)
        care.add(self.toggles["break_reminder"])
        care.add(self.toggles["fatigue_alert"])
        lay.addWidget(care)

        priv = Card("개인정보")
        self.toggles["learn_words"] = ToggleRow("입력한 단어 학습", "키보드로 쓴 단어의 빈도만 저장해 자동완성에 써요. 문장은 저장하지 않아요.",
                                                s.learn_words)
        priv.add(self.toggles["learn_words"])
        priv.add(label("카메라 영상은 이 컴퓨터 메모리에서만 처리하고 저장·전송하지 않아요. 저장되는 것은 설정값과 사용 통계뿐이에요. "
                       "이 프로그램은 참고용 보조 도구이며 의료기기가 아니에요.", "muted", wrap=True))
        delete = IconButton("delete", "모든 기록 삭제", "danger")
        delete.clicked.connect(self._delete_all)
        priv.add(row(delete, None))
        lay.addWidget(priv)
        for k, t in self.toggles.items():
            t.toggled.connect(lambda v, key=k: app.update(key, v))

        keys = Card("단축키")
        for k, d in [("Pause  또는  Ctrl+Alt+P", "클릭 멈추기 / 다시 시작"), ("Ctrl+Alt+K", "키보드 열기 / 닫기"),
                     ("Ctrl+Alt+N", "다음 즐겨찾기 부위"), ("Ctrl+Alt+M", "카메라 커서 끄기 / 켜기"),
                     ("Ctrl+Alt+C", "커서 고정 (상대 모드)"), ("Ctrl+Alt+S", "이 창 열기")]:
            keys.add(row(label(k, "h2"), None, label(d, "muted")))
        lay.addWidget(keys)
        lay.addStretch()

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "새 프로필", "프로필 이름 (예: 이름, 집, 치료실)")
        if ok and name.strip():
            self.app.new_profile(name.strip())

    def _delete_all(self):
        if QMessageBox.question(self, "모든 기록 삭제", "모든 프로필, 설정, 사용 기록, 학습한 단어를 지울까요?\n되돌릴 수 없어요.") \
                == QMessageBox.StandardButton.Yes:
            self.app.delete_all()

    def refresh(self):
        app, s = self.app, self.app.settings
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(app.profile_names())
        self.profile.setCurrentText(app.profile_name)
        self.profile.blockSignals(False)
        self.cam.clear()
        for i in app.cameras:
            self.cam.addItem(app.camera_label(i), i)
        self.cam.setCurrentIndex(max(0, self.cam.findData(s.camera_index)))
        for k, t in self.toggles.items():
            t.set_checked(getattr(s, k))


# =========================== 창 ===========================
class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("BodyPointer")
        fit_to_screen(self, 1440, 900)          # 작업 표시줄에 가려 잘리지 않게
        self.setMinimumSize(960, 560)
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(232)
        side_outer = QVBoxLayout(side)
        side_outer.setContentsMargins(0, 0, 0, 0)
        side_scroll = QScrollArea()             # 화면이 낮으면 메뉴를 스크롤
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        side_inner = QWidget()
        side_scroll.setWidget(side_inner)
        side_outer.addWidget(side_scroll)
        sv = QVBoxLayout(side_inner)
        sv.setContentsMargins(16, 22, 16, 18)
        sv.setSpacing(4)
        brand = label("BodyPointer", "h2")
        sv.addWidget(brand)
        self.mini_status = label("", "small")
        sv.addWidget(self.mini_status)
        sv.addSpacing(14)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for key, ic, text in NAV:
            b = IconButton(ic, text, checkable=True, icon_size=20)
            b.setProperty("role", "nav")
            b.setMinimumWidth(196)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav_group.addButton(b)
            sv.addWidget(b)
            self.nav_buttons[key] = b
        sv.addStretch()
        hide = IconButton("hide", "창 숨기기", "ghost")
        hide.clicked.connect(self.close)
        quit_ = IconButton("power", "종료", "danger")
        quit_.clicked.connect(app.quit)
        sv.addWidget(hide)
        sv.addWidget(quit_)
        h.addWidget(side)

        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(app, self.go), "parts": PartsPage(app), "move": MovePage(app), "click": ClickPage(app),
            "gesture": GesturePage(app), "stats": StatsPage(app), "insights": InsightsPage(app),
            "settings": SettingsPage(app),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        h.addWidget(self.stack, 1)
        self.go("home")

    # 미리보기를 가진 페이지들 (앱이 프레임을 넣어 준다)
    @property
    def previews(self):
        cur = self.stack.currentWidget()
        if cur is self.pages["home"]:
            return [self.pages["home"].preview]
        if cur is self.pages["parts"]:
            return [self.pages["parts"].preview]
        return []

    @property
    def preview(self):
        return self.pages["parts"].preview

    @property
    def pick_btn(self):
        return self.pages["parts"].pick_btn

    @property
    def roi_btn(self):
        return self.pages["parts"].roi_btn

    def current_page(self):
        return next(k for k, p in self.pages.items() if p is self.stack.currentWidget())

    def go(self, key):
        self.nav_buttons[key].setChecked(True)
        for b in self.nav_buttons.values():
            b.refresh_icon()
        self.stack.setCurrentWidget(self.pages[key])
        self.pages[key].refresh()
        if key != "parts":
            if self.pick_btn.isChecked():
                self.pick_btn.setChecked(False)
            if self.roi_btn.isChecked():
                self.roi_btn.setChecked(False)
        self.app.on_page_changed(key)

    def refresh(self):
        self.pages[self.current_page()].refresh()
        part = get_part(self.app.settings.part_id)
        for p in (self.pages["home"].preview, self.pages["parts"].preview):
            p.part = part

    def refresh_all(self):
        for p in self.pages.values():
            p.refresh()

    def set_status(self, title, sub, paused, tone, mini):
        self.pages["home"].set_status(title, sub, paused, tone)
        self.mini_status.setText(mini)

    def set_paused(self, paused):
        pass  # 상태 표시는 set_status가 담당
