"""머리 센서 리모컨 (ESP32 + MPU6500 자이로, 블루투스 SPP 또는 USB).

카메라가 약한 상황(어두움, 얼굴 가림, 누운 자세, 카메라 끔)에서 커서를 대신 움직인다.
자이로는 '돌린 각도'만 알려 주므로 상대 이동(머리를 돌린 만큼 커서 이동)으로 쓴다.

통신 형식 (리틀 엔디언): [A5 5B][type][len][payload][checksum = (type + len + payload) & 0xFF]
  type 1 (28B): uint32 t_us, float gx gy gz (deg/s, 바이어스 제거), float ax ay az (g)
  type 2 (2B) : uint8 WHO_AM_I, uint8 상태 (0 보정 중 / 1 동작 / 2 센서 오류)
  type 3 (20B, 1초마다 진단): uint32 ×4, uint32 플래그
        플래그 bit0 블루투스 연결, bit8~15 직전 리셋 원인(9 = 저전압), bit16~31 전원을 켠 뒤 저전압 리셋 횟수
PC → 장치: 'c' 자이로 다시 보정, 'b' 바이너리 모드

펌웨어는 배터리 절약을 위해 IMU 패킷을 8개(40ms)씩 묶어 보낸다.
그대로 쓰면 커서가 40ms마다 툭툭 움직이므로, 장치 시각 기준으로 고르게 풀어 주는 재생 버퍼를 둔다 (지연 약 50ms).
"""
import re
import struct
import time
from collections import deque

from PySide6.QtCore import QThread, Signal

ESP_VIDS = {0x10C4, 0x1A86, 0x303A, 0x0403}      # CP210x, CH340, Espressif, FTDI
HEADER = b"\xA5\x5B"
NO_DATA_TIMEOUT = 4.0
FAILED_PORT_BACKOFF = 60.0                       # 우리 센서가 아닌 포트는 1분 동안 다시 시도하지 않음
EMIT_INTERVAL = 0.010                            # 약 100Hz로 UI에 전달
DEADBAND_DPS = 1.5                               # 이보다 느린 회전은 무시 (가만히 있을 때 흐름 방지)
PLAYOUT_DELAY = 0.05                             # 재생 버퍼 지연 (펌웨어 묶음 40ms + 여유)
CLOCK_DRIFT = 0.002                              # 장치·PC 시계 차이가 천천히 달라지는 것을 따라가는 정도 (초/초)
RESET_BROWNOUT = 9                               # esp_reset_reason: 저전압


def find_ports():
    """(블루투스 나가는 포트들, USB 포트들)."""
    try:
        import serial.tools.list_ports as lp
    except ImportError:
        return [], []
    bt, usb = [], []
    for p in lp.comports():
        hwid = (p.hwid or "").upper()
        if "BTHENUM" in hwid:
            m = re.search(r"&([0-9A-F]{12})_", hwid)
            if m and m.group(1) != "0" * 12:
                bt.append(p.device)
        elif p.vid in ESP_VIDS:
            usb.append(p.device)
    return bt, usb


class RemoteWorker(QThread):
    """백그라운드에서 연결·수신. motion(t, (d0, d1, d2), (r0, r1, r2)): 센서 축별 회전 각도 변화(도)와 각속도(도/초)."""
    motion = Signal(float, object, object)
    status = Signal(str, str)        # (상태, 설명) 상태: searching / connecting / calibrating / running / error / off
    power = Signal(int, int)         # (직전 리셋 원인, 저전압 리셋 횟수) — 바뀔 때만

    def __init__(self):
        super().__init__()
        self._stop = False
        self._ser = None
        self._failed = {}
        self.state = "off"
        self.transport = ""
        self.rate_hz = 0.0
        self.last_rx = 0.0
        self.reset_reason = None
        self.brownouts = 0

    # ---- 메인 스레드에서 ----
    def recalibrate(self):
        self._send(b"c")

    def stop(self):
        self._stop = True
        self.wait(3000)

    def connected(self) -> bool:
        return self.state in ("running", "calibrating") and time.monotonic() - self.last_rx < 1.0

    def _send(self, data):
        ser = self._ser
        if ser is not None:
            try:
                ser.write(data)
            except Exception:
                pass

    def _set(self, state, text=""):
        if state != self.state or text:
            self.state = state
            self.status.emit(state, text)

    # ---- 작업 스레드 ----
    def run(self):
        try:
            import serial
        except ImportError:
            self._set("error", "pyserial이 설치되지 않았어요")
            return
        while not self._stop:
            bt, usb = find_ports()
            now = time.monotonic()
            ports = [(p, "USB") for p in usb] + [(p, "블루투스") for p in bt]   # 유선이 더 안정적이라 먼저
            ports = [(p, k) for p, k in ports if now - self._failed.get(p, -1e9) > FAILED_PORT_BACKOFF]
            if not ports:
                self._set("searching", "머리 센서를 찾는 중 (블루투스 ‘HeadMouse-IMU’ 페어링 또는 USB 연결)")
                self._sleep(3.0)
                continue
            for dev, kind in ports:
                if self._stop:
                    break
                self._set("connecting", f"{dev} ({kind}) 연결 시도 중")
                try:
                    ser = serial.Serial(dev, 921600, timeout=0.005, write_timeout=1)
                except Exception:
                    self._failed[dev] = time.monotonic()
                    continue
                self._ser = ser
                self.transport = kind
                try:
                    ser.write(b"b")
                    ok = self._read_loop(ser)
                except Exception:
                    ok = False
                finally:
                    self._ser = None
                    try:
                        ser.close()
                    except Exception:
                        pass
                if not ok:
                    self._failed[dev] = time.monotonic()
                else:
                    self._set("searching", "머리 센서 연결이 끊겼어요. 다시 찾는 중")
            self._sleep(1.0)
        self._set("off")

    def _sleep(self, s):
        end = time.monotonic() + s
        while not self._stop and time.monotonic() < end:
            time.sleep(0.05)

    def _read_loop(self, ser) -> bool:
        buf = bytearray()
        got_any = False
        last_rx = time.monotonic()
        last_t = None
        self._acc = [0.0, 0.0, 0.0]
        self._rates = deque(maxlen=8)
        self._pending = deque()          # 재생 버퍼: (장치 시각 s, (gx, gy, gz), dt)
        self._dev_s = 0.0                # 장치 시각 (32비트 마이크로초 넘침을 풀어 누적)
        self._offset = None              # PC 시각 - 장치 시각 (가장 빨리 도착한 값 기준)
        self._offset_t = 0.0
        last_emit = time.monotonic()
        count, t0 = 0, time.monotonic()
        while not self._stop:
            data = ser.read(ser.in_waiting or 1)
            now = time.monotonic()
            if data:
                buf += data
            elif now - last_rx > NO_DATA_TIMEOUT:
                self._release(float("inf"))
                self._emit(now)
                return got_any
            while True:
                i = buf.find(HEADER)
                if i < 0:
                    del buf[:-1]
                    break
                if i:
                    del buf[:i]
                if len(buf) < 5:
                    break
                typ, ln = buf[2], buf[3]
                if ln > 64:
                    del buf[:2]
                    continue
                if len(buf) < 5 + ln:
                    break
                payload = bytes(buf[4:4 + ln])
                if (typ + ln + sum(payload)) & 0xFF != buf[4 + ln]:
                    del buf[:2]
                    continue
                del buf[:5 + ln]
                last_rx = self.last_rx = now
                got_any = True
                if typ == 2 and ln == 2:
                    st = payload[1]
                    if st == 0:
                        self._set("calibrating", "머리 센서 보정 중 — 2초 동안 머리를 가만히 두세요")
                        last_t = None
                    elif st == 1:
                        self._set("running", f"머리 센서 연결됨 ({self.transport})")
                    else:
                        self._set("error", "머리 센서 오류 — 센서 배선을 확인해 주세요")
                elif typ == 1 and ln == 28:
                    t_us, gx, gy, gz, _, _, _ = struct.unpack("<I6f", payload)
                    if self.state not in ("running", "calibrating"):
                        self._set("running", f"머리 센서 연결됨 ({self.transport})")
                    if last_t is not None:
                        dt = ((t_us - last_t) & 0xFFFFFFFF) / 1e6
                        if 0 < dt < 0.1:
                            g = (gx, gy, gz)
                            rates.append(g)
                            for k in range(3):
                                if abs(g[k]) > DEADBAND_DPS:
                                    acc[k] += g[k] * dt
                    last_t = t_us
                    count += 1
            if now - last_emit >= EMIT_INTERVAL and rates:
                mean = tuple(sum(r[k] for r in rates) / len(rates) for k in range(3))
                self.motion.emit(now, tuple(acc), mean)
                acc = [0.0, 0.0, 0.0]
                last_emit = now
            if now - t0 >= 1.0:
                self.rate_hz = count / (now - t0)
                count, t0 = 0, now
        return got_any


class NodDetector:
    """고개 끄덕임(빠르게 숙였다 드는 동작) = 클릭. 자이로라 어두워도 정확하다."""

    def __init__(self, threshold=70.0, window=0.45, cooldown=1.0):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self._down_at = None
        self._last = -1e9

    def update(self, pitch_rate: float, yaw_rate: float, t: float) -> bool:
        """pitch_rate: 아래로 숙이면 +. 숙임 → 바로 듦이 window 안에 있으면 True."""
        if abs(yaw_rate) > 60:               # 고개를 크게 돌리는 중이면 끄덕임으로 보지 않음
            self._down_at = None
            return False
        if pitch_rate > self.threshold:
            self._down_at = self._down_at or t
        elif pitch_rate < -self.threshold and self._down_at is not None:
            if t - self._down_at <= self.window and t - self._last >= self.cooldown:
                self._down_at = None
                self._last = t
                return True
        if self._down_at is not None and t - self._down_at > self.window:
            self._down_at = None
        return False


def fit_axes(right_turn: list[float], head_down: list[float]):
    """방향 맞추기: '오른쪽으로 돌리기' 때와 '고개 숙이기' 때 축별 누적 각도로 화면 x·y에 맞는 축과 부호를 찾는다."""
    xa = max(range(3), key=lambda k: abs(right_turn[k]))
    rest = [k for k in range(3) if k != xa]
    ya = max(rest, key=lambda k: abs(head_down[k]))
    if abs(right_turn[xa]) < 8 or abs(head_down[ya]) < 6:       # 충분히 움직이지 않음
        return None
    return {"x": [xa, 1 if right_turn[xa] > 0 else -1], "y": [ya, 1 if head_down[ya] > 0 else -1]}
