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


DEVICE_NAME = "HeadMouse-IMU"


def bt_device_names() -> dict:
    """Windows에 페어링된 블루투스 장치 이름 {MAC(대문자 12자리): 이름}. 실패하면 빈 dict."""
    names = {}
    try:
        import winreg
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices")
    except (ImportError, OSError):
        return names
    i = 0
    while True:
        try:
            mac = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(root, mac) as k:
                raw = winreg.QueryValueEx(k, "Name")[0]
            name = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
            names[mac.upper()] = name.rstrip("\x00").strip()
        except OSError:
            continue
    return names


def pick_bt_ports(ports: list[tuple[str, str]], names: dict) -> list[str]:
    """[(포트, MAC)] 중 머리 센서 포트만 고른다.
    이름을 알 수 있으면 'HeadMouse' 장치만 (이어폰·휴대폰 등 다른 블루투스 기기에 연결을 시도하지 않음).
    이름을 하나도 알 수 없을 때만 모든 포트를 시도한다."""
    known = {mac: names[mac] for _, mac in ports if mac in names}
    if not known:
        return [dev for dev, _ in ports]
    return [dev for dev, mac in ports if names.get(mac, "").lower().startswith("headmouse")]


def find_ports():
    """(블루투스 나가는 포트들 중 머리 센서, USB 포트들)."""
    try:
        import serial.tools.list_ports as lp
    except ImportError:
        return [], []
    bt, usb = [], []
    for p in lp.comports():
        hwid = (p.hwid or "").upper()
        if "BTHENUM" in hwid:
            m = re.search(r"&([0-9A-F]{12})_", hwid)
            if m and m.group(1) != "0" * 12:          # 0000…은 PC가 받는 쪽(들어오는) 포트라 제외
                bt.append((p.device, m.group(1)))
        elif p.vid in ESP_VIDS:
            usb.append(p.device)
    return pick_bt_ports(bt, bt_device_names()), usb


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
        self.firmware = None             # 연동판 펌웨어 버전 (type 4 식별 패킷), 예: "2.0"

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
                self.firmware = None
                try:
                    ser.write(b"bi")                 # 바이너리 모드 + 식별 요청 (연동판 펌웨어가 버전으로 답함)
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
                        self._set("running", self._connected_text())
                    else:
                        self._set("error", "머리 센서 오류 — 센서 배선을 확인해 주세요")
                elif typ == 4 and ln == 4 and payload[:2] == b"BP":
                    self.firmware = f"{payload[2]}.{payload[3]}"
                    if self.state == "running":
                        self._set("running", self._connected_text())
                elif typ == 3 and ln >= 20:
                    flags = struct.unpack("<I", payload[16:20])[0]
                    reason, brownouts = (flags >> 8) & 0xFF, flags >> 16
                    if (reason, brownouts) != (self.reset_reason, self.brownouts):
                        self.reset_reason, self.brownouts = reason, brownouts
                        self.power.emit(reason, brownouts)
                elif typ == 1 and ln == 28:
                    t_us, gx, gy, gz, _, _, _ = struct.unpack("<I6f", payload)
                    if self.state not in ("running", "calibrating"):
                        self._set("running", self._connected_text())
                    if last_t is not None:
                        dt = ((t_us - last_t) & 0xFFFFFFFF) / 1e6
                        if 0 < dt < 0.1:
                            self._dev_s += dt
                            self._queue(now, (gx, gy, gz), dt)
                        else:
                            self._offset = None      # 시각이 끊겼으면 다시 맞춘다
                    last_t = t_us
                    count += 1
            self._release(now)
            if now - last_emit >= EMIT_INTERVAL:
                if self._emit(now):
                    last_emit = now
            if now - t0 >= 1.0:
                self.rate_hz = count / (now - t0)
                count, t0 = 0, now
        return got_any

    def _connected_text(self):
        fw = f", 연동판 펌웨어 {self.firmware}" if self.firmware else ""
        return f"머리 센서 연결됨 ({self.transport}{fw})"

    # ---- 재생 버퍼: 묶음으로 온 샘플을 장치 시각대로 고르게 풀어 준다 ----
    def _queue(self, now, g, dt):
        cur = now - self._dev_s
        if self._offset is None or cur < self._offset:
            self._offset = cur                   # 가장 빨리 도착한 샘플이 실제 시각에 가장 가깝다
        else:
            self._offset += CLOCK_DRIFT * max(now - self._offset_t, 0.0)   # 시계 차이를 천천히 따라감
            self._offset = min(self._offset, cur)
        self._offset_t = now
        self._pending.append((self._dev_s, g, dt))

    def _release(self, now):
        while self._pending and (now == float("inf") or
                                 self._pending[0][0] + self._offset + PLAYOUT_DELAY <= now):
            _, g, dt = self._pending.popleft()
            self._rates.append(g)
            for k in range(3):
                if abs(g[k]) > DEADBAND_DPS:
                    self._acc[k] += g[k] * dt

    def _emit(self, now) -> bool:
        if not self._rates:
            return False
        mean = tuple(sum(r[k] for r in self._rates) / len(self._rates) for k in range(3))
        self.motion.emit(now, tuple(self._acc), mean)
        self._acc = [0.0, 0.0, 0.0]
        return True


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
