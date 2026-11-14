"""카메라 + 인식 (FR-01, FR-02, FR-04, FR-05, FR-12, NFR-05~08).

MediaPipe는 Qt WebEngine(서명된 Chromium) 안에서 WASM으로 실행한다.
Windows 스마트 앱 컨트롤이 켜진 PC에서는 MediaPipe 파이썬 패키지의 서명 없는 네이티브 DLL이 차단되기 때문.
같은 .task 모델을 쓰고, 모든 처리는 이 PC 안(127.0.0.1)에서만 이뤄진다. 영상은 저장·외부 전송하지 않는다.

구조: 숨김 QWebEnginePage(web/vision.html) ──QWebChannel──▶ JsBridge ──▶ VisionWorker.result(FrameResult)
사용자 지정 영역(ROI) 추적은 OpenCV CSRT로 Python에서 한다.
"""
from __future__ import annotations

import base64
import http.server
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings

from .parts import Part

POSE_MIN_VISIBILITY = 0.5
CHROMIUM_FLAGS = ("--disable-background-timer-throttling --disable-renderer-backgrounding "
                  "--disable-backgrounding-occluded-windows --disable-background-media-suspend "
                  "--autoplay-policy=no-user-gesture-required")


def prepare_environment():
    """QApplication 생성 전에 호출: 숨김 페이지가 느려지지 않도록 백그라운드 절전을 끈다."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " " + CHROMIUM_FLAGS).strip()


def _root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


@dataclass
class FrameResult:
    t: float
    image: QImage | None
    face: list | None = None
    pose: list | None = None
    hands: list = field(default_factory=list)
    roi: tuple | None = None
    blend: dict | None = None      # 얼굴 표정 점수 (제스처용)
    fps: float = 0.0
    low_light: bool = False
    camera_ok: bool = True


def _weighted(pts, points, with_conf):
    sw = sx = sy = sc = 0.0
    for idx, w in points:
        p = pts[idx]
        c = p[2] if with_conf else 1.0
        ww = w * max(c, 0.05)              # 신뢰도가 높은 점에 더 무게를 둔다
        sx += p[0] * ww
        sy += p[1] * ww
        sc += c * w
        sw += ww
    total_w = sum(w for _, w in points)
    return sx / sw, sy / sw, sc / total_w


def extract_sample(res: FrameResult, part: Part):
    """선택 부위의 (x, y, 신뢰도). 해당 모델 결과가 없으면 None."""
    if part.source == "face" and res.face:
        return _weighted(res.face, part.points, False)
    if part.source == "pose" and res.pose:
        return _weighted(res.pose, part.points, True)
    if part.source == "hand" and res.hands:
        for side, pts in res.hands:
            if part.side is None or side == part.side:
                return _weighted(pts, part.points, False)
        return None
    if part.source == "roi" and res.roi:
        x, y, w, h = res.roi
        return x + w / 2, y + h / 2, 1.0
    return None


def extract_point(res: FrameResult, part: Part):
    """미리보기 표시용: 신뢰도 기준을 넘는 좌표 (x, y), 없으면 None."""
    s = extract_sample(res, part)
    if s is None or s[2] < part.min_conf:
        return None
    return s[0], s[1]


# ---------------- 로컬 전용 HTTP 서버 (127.0.0.1) ----------------
class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
                      ".task": "application/octet-stream", ".html": "text/html; charset=utf-8"}
    qwebchannel_js = b""

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/qwebchannel.js":
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(self.qwebchannel_js)))
            self.end_headers()
            self.wfile.write(self.qwebchannel_js)
            return
        if not (path.startswith("/bodypointer/web/") or path.startswith("/models/")) or ".." in path:
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def _start_server() -> int:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        _Handler.qwebchannel_js = bytes(f.readAll())
        f.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(_root())))
    threading.Thread(target=srv.serve_forever, daemon=True, name="local-http").start()
    return srv.server_address[1]


class _JsBridge(QObject):
    config = Signal(str)            # Python → JS 설정

    def __init__(self, worker: "VisionWorker"):
        super().__init__()
        self.worker = worker

    @Slot()
    def ready(self):
        self.worker._send_config()

    @Slot(str)
    def onResult(self, s):
        self.worker._on_js_result(s)

    @Slot(str)
    def onStatus(self, s):
        self.worker.status.emit(s)

    @Slot(str)
    def onCameras(self, s):
        try:
            self.worker.cameras.emit(json.loads(s))
        except ValueError:
            pass


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"[vision.js] {message} ({source}:{line})")


def _create_csrt():
    for make in (getattr(cv2, "TrackerCSRT_create", None),
                 getattr(getattr(cv2, "TrackerCSRT", None), "create", None),
                 getattr(getattr(cv2, "legacy", None), "TrackerCSRT_create", None)):
        if make:
            return make()
    raise RuntimeError("OpenCV CSRT 추적기를 찾을 수 없습니다 (opencv-contrib-python 필요).")


class VisionWorker(QObject):
    result = Signal(object)         # FrameResult
    status = Signal(str)
    cameras = Signal(list)          # 카메라 이름 목록

    def __init__(self, camera_index: int = 0):
        super().__init__()
        self.camera_index = camera_index
        self._sources = {"face"}
        self.pose_model = "lite"
        self.send_preview = True
        self._tracker = None
        self._roi_request = None
        self._page = None
        self._last_cfg = None

    # ---- 앱에서 호출 (모두 메인 스레드) ----
    def start(self):
        port = _start_server()
        profile = QWebEngineProfile(self)            # 디스크에 아무것도 남기지 않는 임시 프로필
        self._page = _Page(profile, self)
        s = self._page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self._grant_camera()
        self._bridge = _JsBridge(self)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)
        self._page.load(QUrl(f"http://127.0.0.1:{port}/bodypointer/web/vision.html"))
        self.status.emit("인식 엔진을 준비하는 중…")

    def _grant_camera(self):
        page = self._page
        if hasattr(page, "permissionRequested"):        # Qt 6.8+
            page.permissionRequested.connect(lambda perm: perm.grant())
        if hasattr(page, "featurePermissionRequested"):  # 이전 버전 호환
            def grant(url, feature):
                page.setFeaturePermission(url, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)
            page.featurePermissionRequested.connect(grant)

    def set_sources(self, sources: set[str], pose_model: str = "lite"):
        self._sources = set(sources)
        self.pose_model = pose_model
        self._send_config()

    def track_roi(self, box):
        self._roi_request = tuple(box)
        self._send_config()

    def clear_roi(self):
        self._tracker = None

    def change_camera(self, index: int):
        self.camera_index = index
        self._send_config()

    def stop(self):
        if self._page is not None:
            self._page.deleteLater()
            self._page = None

    # ---- 내부 ----
    def _need_frames(self):
        return self.send_preview or "roi" in self._sources

    def _send_config(self):
        if self._page is None:
            return
        cfg = {"sources": sorted(self._sources - {"roi"}), "camera": self.camera_index,
               "wantFrames": self._need_frames(), "frameW": 320, "poseModel": self.pose_model}
        s = json.dumps(cfg)
        if s != self._last_cfg:
            self._last_cfg = s
            self._bridge.config.emit(s)

    def _on_js_result(self, s: str):
        t = time.monotonic()
        try:
            d = json.loads(s)
        except ValueError:
            return
        if not d.get("cameraOk", True):
            self.result.emit(FrameResult(t, None, camera_ok=False))
            return
        res = FrameResult(t, None, face=d.get("face"), pose=d.get("pose"),
                          hands=[(side, pts) for side, pts in d.get("hands") or []], blend=d.get("blend"),
                          fps=d.get("fps", 0.0), low_light=bool(d.get("lowLight")))
        jpg = d.get("jpg")
        if jpg:
            raw = base64.b64decode(jpg.split(",", 1)[1])
            if self.send_preview:
                res.image = QImage.fromData(raw, "JPG")
            if "roi" in self._sources:
                res.roi = self._track(cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR))
        self.result.emit(res)
        self._send_config()      # 미리보기 필요 여부가 바뀌었으면 반영

    def _track(self, frame):
        """CSRT 영역 추적. 프레임은 이미 거울 모드이므로 좌표 변환 없이 정규화만 한다."""
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if self._roi_request is not None:
            x, y, bw, bh = self._roi_request
            self._roi_request = None
            try:
                self._tracker = _create_csrt()
                self._tracker.init(frame, (int(x * w), int(y * h), max(int(bw * w), 6), max(int(bh * h), 6)))
            except Exception as e:
                self._tracker = None
                self.status.emit(f"영역 추적을 시작하지 못했습니다: {e}")
        if self._tracker is None:
            return None
        ok, box = self._tracker.update(frame)
        if not ok:
            return None
        bx, by, bw, bh = box
        return bx / w, by / h, bw / w, bh / h


def list_cameras() -> list[int]:
    """웹 엔진이 실제 목록을 알려주기 전까지 쓰는 기본값."""
    return [0]
