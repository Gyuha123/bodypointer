"""사용 기록 (FR-50): 세션 동안 메모리에 모았다가, 사용자가 '기록 저장'을 누르면 로컬 SQLite에 남긴다.

저장하는 것: 통계 수치(사용 시간, 클릭 수, 가동 범위, 떨림 등), 기능별 사용 횟수, 입력한 단어 빈도.
저장하지 않는 것: 영상, 사진, 원본 랜드마크 시계열, 입력한 문장 원문 (§6).
"""
import csv
import json
import math
import sqlite3
import time
from collections import Counter, deque
from contextlib import closing
from pathlib import Path

import numpy as np

from .profile import APP_DIR

GRID_W, GRID_H = 16, 9
SEQ_WINDOW = 6.0          # 이 시간 안에 이어서 쓴 두 동작을 '연속 사용'으로 본다
UNDO_WINDOW = 3.0         # 클릭 직후 실행 취소 → 잘못 누른 것으로 본다


class SessionRecorder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.time()
        self._t0 = time.monotonic()
        self._last_t = None
        self.part_secs = Counter()
        self.active_secs = 0.0
        self.lost_secs = 0.0
        self.lost_events = 0
        self.clicks = Counter()
        self.popup_cancels = 0
        self.misclicks = 0
        self.actions = Counter()
        self.action_sources = Counter()
        self.seqs = Counter()
        self.gestures = Counter()
        self.words = Counter()
        self.bigrams = Counter()
        self.typed_chars = 0
        self.saved_chars = 0
        self.click_cells = Counter()
        self._rom = {}                      # part → deque[(x, y)] (가동 범위 계산용 표본, 저장 안 함)
        self._speed_sum = self._speed_n = 0.0
        self._jitter_sum = self._jitter_n = 0.0
        self._prev_cursor = None
        self._last_action = None
        self._last_click_t = -1e9
        self._recent = deque()              # (t, x, y) 최근 10분 표본 (피로 감지용, 저장 안 함)
        self._baseline = None
        self._last_fatigue = -1e9

    @property
    def duration(self) -> float:
        return time.monotonic() - self._t0

    def has_data(self) -> bool:
        return self.active_secs > 5 or bool(self.clicks) or bool(self.actions) or bool(self.words)

    # ---------- 프레임마다 ----------
    def on_frame(self, part_id: str, raw, cursor, t: float, lost: bool, screen_w: int):
        dt = 0.0 if self._last_t is None else min(t - self._last_t, 0.5)
        self._last_t = t
        if lost or raw is None:
            self.lost_secs += dt
            self._prev_cursor = None
            return
        self.active_secs += dt
        self.part_secs[part_id] += dt
        q = self._rom.setdefault(part_id, deque(maxlen=6000))
        q.append(raw)
        self._recent.append((t, raw[0], raw[1]))
        while self._recent and t - self._recent[0][0] > 600:
            self._recent.popleft()
        if self._prev_cursor is not None and dt > 0:
            d = math.dist(cursor, self._prev_cursor)
            speed = d / dt
            if speed > 80:
                self._speed_sum += speed / screen_w        # 화면 폭 대비 속도 (해상도 무관)
                self._speed_n += 1
            else:
                self._jitter_sum += d
                self._jitter_n += 1
        self._prev_cursor = cursor

    def on_lost(self):
        self.lost_events += 1

    # ---------- 이벤트 ----------
    def on_click(self, kind: str, pt, screen):
        self.clicks[kind] += 1
        self._last_click_t = time.monotonic()
        cx = min(int(pt[0] / screen[0] * GRID_W), GRID_W - 1)
        cy = min(int(pt[1] / screen[1] * GRID_H), GRID_H - 1)
        self.click_cells[f"{cx},{cy}"] += 1

    def on_popup_cancel(self):
        self.popup_cancels += 1

    def on_action(self, action_id: str, source: str):
        now = time.monotonic()
        if action_id == "undo" and now - self._last_click_t < UNDO_WINDOW:
            self.misclicks += 1
        self.actions[action_id] += 1
        self.action_sources[source] += 1
        if self._last_action and self._last_action[0] != action_id and now - self._last_action[1] <= SEQ_WINDOW:
            self.seqs[f"{self._last_action[0]}+{action_id}"] += 1
        self._last_action = (action_id, now)

    def on_gesture(self, gid: str):
        self.gestures[gid] += 1

    def on_word(self, word: str, prev: str | None):
        self.words[word] += 1
        if prev:
            self.bigrams[(prev, word)] += 1

    def on_typed(self, n_chars: int, saved: int = 0):
        self.typed_chars += n_chars
        self.saved_chars += saved

    # ---------- 계산 ----------
    @staticmethod
    def _rom_of(points) -> tuple[float, float]:
        if len(points) < 30:
            return 0.0, 0.0
        a = np.asarray(points)
        lo, hi = np.percentile(a, [5, 95], axis=0)
        return float(hi[0] - lo[0]), float(hi[1] - lo[1])

    def fatigue_check(self, t: float) -> bool:
        """세션 초반보다 최근 2분의 움직임 범위가 크게 줄면 True (FR-44). 10분에 한 번만."""
        if self.duration < 480 or t - self._last_fatigue < 600 or len(self._recent) < 300:
            return False
        if self._baseline is None:
            early = [(x, y) for tt, x, y in self._recent if tt - self._recent[0][0] < 180]
            rx, ry = self._rom_of(early)
            if rx * ry <= 0:
                return False
            self._baseline = rx * ry
        late = [(x, y) for tt, x, y in self._recent if t - tt < 120]
        rx, ry = self._rom_of(late)
        if len(late) > 200 and rx * ry < 0.45 * self._baseline:
            self._last_fatigue = t
            return True
        return False

    def summary(self, main_part: str | None = None) -> dict:
        part = main_part or (self.part_secs.most_common(1)[0][0] if self.part_secs else None)
        rx, ry = self._rom_of(self._rom.get(part, ())) if part else (0.0, 0.0)
        dwell_clicks = sum(self.clicks.values())
        return {
            "duration": round(self.duration, 1),
            "active_secs": round(self.active_secs, 1),
            "lost_secs": round(self.lost_secs, 1),
            "lost_events": self.lost_events,
            "main_part": part,
            "part_secs": {k: round(v, 1) for k, v in self.part_secs.items()},
            "rom_x": round(rx, 4), "rom_y": round(ry, 4),
            "speed": round(self._speed_sum / self._speed_n, 4) if self._speed_n else 0.0,
            "jitter": round(self._jitter_sum / self._jitter_n, 3) if self._jitter_n else 0.0,
            "clicks": dict(self.clicks),
            "click_total": dwell_clicks,
            "popup_cancels": self.popup_cancels,
            "misclicks": self.misclicks,
            "actions": dict(self.actions),
            "action_sources": dict(self.action_sources),
            "seqs": dict(self.seqs),
            "gestures": dict(self.gestures),
            "typed_chars": self.typed_chars,
            "saved_chars": self.saved_chars,
            "click_cells": dict(self.click_cells),
            "hour": time.localtime(self.started).tm_hour,
        }


class UsageStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or APP_DIR / "usage.db")

    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, profile TEXT, started REAL, ended REAL, data TEXT);
            CREATE TABLE IF NOT EXISTS words(profile TEXT, word TEXT, count INTEGER, last REAL, PRIMARY KEY(profile, word));
            CREATE TABLE IF NOT EXISTS bigrams(profile TEXT, w1 TEXT, w2 TEXT, count INTEGER, PRIMARY KEY(profile, w1, w2));
        """)
        return c

    def save(self, profile: str, rec: SessionRecorder, main_part: str | None, learn_words: bool = True) -> dict:
        summary = rec.summary(main_part)
        now = time.time()
        with closing(self._conn()) as c, c:
            c.execute("INSERT INTO sessions(profile, started, ended, data) VALUES (?,?,?,?)",
                      (profile, rec.started, now, json.dumps(summary, ensure_ascii=False)))
            if learn_words:
                for w, n in rec.words.items():
                    c.execute("INSERT INTO words VALUES (?,?,?,?) ON CONFLICT(profile, word) "
                              "DO UPDATE SET count = count + excluded.count, last = excluded.last", (profile, w, n, now))
                for (w1, w2), n in rec.bigrams.items():
                    c.execute("INSERT INTO bigrams VALUES (?,?,?,?) ON CONFLICT(profile, w1, w2) "
                              "DO UPDATE SET count = count + excluded.count", (profile, w1, w2, n))
        return summary

    def sessions(self, profile: str, days: int | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        q, args = "SELECT started, ended, data FROM sessions WHERE profile = ?", [profile]
        if days:
            q += " AND started >= ?"
            args.append(time.time() - days * 86400)
        with closing(self._conn()) as c:
            rows = c.execute(q + " ORDER BY started", args).fetchall()
        out = []
        for started, ended, data in rows:
            d = json.loads(data)
            d["started"], d["ended"] = started, ended
            out.append(d)
        return out

    def words(self, profile: str):
        if not self.path.exists():
            return {}, {}, {}
        with closing(self._conn()) as c:
            w = c.execute("SELECT word, count, last FROM words WHERE profile = ?", (profile,)).fetchall()
            b = c.execute("SELECT w1, w2, count FROM bigrams WHERE profile = ?", (profile,)).fetchall()
        return {x[0]: x[1] for x in w}, {(x[0], x[1]): x[2] for x in b}, {x[0]: x[2] for x in w}

    def export_csv(self, profile: str, path: Path) -> Path:
        """세션 요약을 CSV로 내보내기 (FR-52). 치료사에게 전달용."""
        rows = self.sessions(profile)
        cols = ["날짜", "사용 시간(분)", "주 사용 부위", "가동 범위 가로(%)", "가동 범위 세로(%)", "이동 속도",
                "떨림(px)", "클릭 수", "클릭 취소", "추적 끊김 횟수", "입력 글자 수"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for s in rows:
                w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started"])),
                            round(s["duration"] / 60, 1), s.get("main_part") or "",
                            round(s.get("rom_x", 0) * 100, 1), round(s.get("rom_y", 0) * 100, 1),
                            s.get("speed", 0), s.get("jitter", 0), s.get("click_total", 0),
                            s.get("popup_cancels", 0), s.get("lost_events", 0), s.get("typed_chars", 0)])
        return path
