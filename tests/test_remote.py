"""머리 센서 리모컨: 패킷 해석, 방향 맞추기, 고개 끄덕, 엔진 상대 입력."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodypointer import remote  # noqa: E402
from bodypointer.remote import NodDetector, RemoteWorker, fit_axes  # noqa: E402
from tests.test_core import FakeHooks, FakeMouse  # noqa: E402
from bodypointer.engine import Engine  # noqa: E402
from bodypointer.profile import Settings  # noqa: E402


def packet(typ, payload):
    return b"\xA5\x5B" + bytes([typ, len(payload)]) + payload + bytes([(typ + len(payload) + sum(payload)) & 0xFF])


class FakeSerial:
    def __init__(self, data):
        self.data = bytearray(data)

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, n):
        chunk, self.data = bytes(self.data[:n]), self.data[n:]
        return chunk


class PacketTest(unittest.TestCase):
    def test_parse_and_integrate(self):
        data = packet(2, bytes([0x70, 1]))                       # MPU6500, 동작 중
        data += b"\x00\x13garbage"                                # 쓰레기 바이트는 건너뛴다
        for i in range(201):                                      # 1초, z축 30°/s
            data += packet(1, struct.pack("<I6f", i * 5000, 0.0, 0.5, 30.0, 0, 0, 1))
        bad = bytearray(packet(1, struct.pack("<I6f", 999, 500.0, 0, 0, 0, 0, 1)))
        bad[-1] ^= 0xFF                                           # 체크섬이 틀린 패킷은 무시
        data += bytes(bad)
        w = RemoteWorker()
        got, states = [], []
        w.motion.connect(lambda t, d, r: got.append(d))
        w.status.connect(lambda s, txt: states.append(s))
        old_timeout, old_emit = remote.NO_DATA_TIMEOUT, remote.EMIT_INTERVAL
        remote.NO_DATA_TIMEOUT, remote.EMIT_INTERVAL = 0.2, 0.0
        try:
            self.assertTrue(w._read_loop(FakeSerial(data)))
        finally:
            remote.NO_DATA_TIMEOUT, remote.EMIT_INTERVAL = old_timeout, old_emit
        total = [sum(d[k] for d in got) for k in range(3)]
        self.assertAlmostEqual(total[2], 30.0, delta=0.5)          # 1초 × 30°/s
        self.assertAlmostEqual(total[1], 0.0, delta=0.01)          # 데드밴드(1.5°/s) 아래는 무시
        self.assertAlmostEqual(total[0], 0.0, delta=0.01)
        self.assertIn("running", states)


class TimedSerial:
    """펌웨어처럼 40ms마다 IMU 패킷 8개를 한꺼번에 내보내는 가짜 포트 (실제 시간 기준)."""

    def __init__(self, bursts, interval=0.04):
        import time
        self.t0 = time.monotonic()
        self.bursts, self.interval, self.sent = bursts, interval, 0
        self.buf = bytearray()

    @property
    def in_waiting(self):
        self._fill()
        return len(self.buf)

    def _fill(self):
        import time
        due = int((time.monotonic() - self.t0) / self.interval) + 1
        while self.sent < min(due, len(self.bursts)):
            self.buf += self.bursts[self.sent]
            self.sent += 1

    def read(self, n):
        import time
        self._fill()
        if not self.buf:
            time.sleep(0.002)
            return b""
        chunk, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return chunk


class BatchSmoothingTest(unittest.TestCase):
    def test_batched_packets_are_spread_out(self):
        import time
        bursts, i = [packet(2, bytes([0x70, 1]))], 0
        for b in range(15):                                       # 0.6초 = 15묶음 × 8개, z축 50°/s
            chunk = b""
            for _ in range(8):
                chunk += packet(1, struct.pack("<I6f", i * 5000, 0.0, 0.0, 50.0, 0, 0, 1))
                i += 1
            bursts.append(chunk)
        w = RemoteWorker()
        got = []
        w.motion.connect(lambda t, d, r: got.append((t, d[2])))
        old = remote.NO_DATA_TIMEOUT
        remote.NO_DATA_TIMEOUT = 0.3
        try:
            w._read_loop(TimedSerial(bursts))
        finally:
            remote.NO_DATA_TIMEOUT = old
        total = sum(d for _, d in got)
        self.assertAlmostEqual(total, 50.0 * 119 * 0.005, delta=0.5)   # 각도는 빠짐없이
        moving = [d for _, d in got if d > 0]
        # 묶음(40ms)마다 한 번이 아니라, 그 사이에도 여러 번 나눠서 나온다 → 부드러운 이동
        self.assertGreater(len(moving), 15 * 2)
        self.assertLess(max(moving), 50.0 * 0.04 * 0.75)

    def test_power_diagnostics(self):
        flags = 1 | (9 << 8) | (3 << 16)                          # 블루투스 연결, 직전 리셋 = 저전압, 3회
        data = packet(2, bytes([0x70, 1])) + packet(3, struct.pack("<5I", 200, 0, 200, 5000, flags))
        w = RemoteWorker()
        power = []
        w.power.connect(lambda r, b: power.append((r, b)))
        old = remote.NO_DATA_TIMEOUT
        remote.NO_DATA_TIMEOUT = 0.1
        try:
            w._read_loop(FakeSerial(data))
        finally:
            remote.NO_DATA_TIMEOUT = old
        self.assertEqual(power, [(9, 3)])
        self.assertEqual(w.brownouts, 3)


class PortPickTest(unittest.TestCase):
    def test_only_headmouse_ports(self):
        ports = [("COM8", "DCCCE68109C8"), ("COM6", "3C8A1F5DCF96")]
        names = {"DCCCE68109C8": "Galaxy Buds Pro (09C8)", "3C8A1F5DCF96": "HeadMouse-IMU"}
        self.assertEqual(remote.pick_bt_ports(ports, names), ["COM6"])     # 이어폰에는 연결 시도 안 함

    def test_unknown_names_try_all(self):
        ports = [("COM8", "AAAAAAAAAAAA"), ("COM6", "BBBBBBBBBBBB")]
        self.assertEqual(remote.pick_bt_ports(ports, {}), ["COM8", "COM6"])

    def test_known_but_no_headmouse(self):
        self.assertEqual(remote.pick_bt_ports([("COM8", "DCCCE68109C8")], {"DCCCE68109C8": "Galaxy Buds"}), [])

    def test_firmware_info_packet(self):
        data = packet(2, bytes([0x70, 1])) + packet(4, b"BP" + bytes([2, 0]))
        w = RemoteWorker()
        w.transport = "블루투스"
        texts = []
        w.status.connect(lambda s, t: texts.append(t))
        old = remote.NO_DATA_TIMEOUT
        remote.NO_DATA_TIMEOUT = 0.1
        try:
            w._read_loop(FakeSerial(data))
        finally:
            remote.NO_DATA_TIMEOUT = old
        self.assertEqual(w.firmware, "2.0")
        self.assertIn("연동판 펌웨어 2.0", texts[-1])


class AxesAndNodTest(unittest.TestCase):
    def test_fit_axes(self):
        axes = fit_axes([-2.0, 1.0, -35.0], [25.0, 3.0, 4.0])
        self.assertEqual(axes, {"x": [2, -1], "y": [0, 1]})
        self.assertIsNone(fit_axes([1, 2, 3], [1, 2, 1]))        # 너무 적게 움직임

    def test_nod(self):
        n = NodDetector()
        fired = [n.update(r, 0, i * 0.02) for i, r in enumerate([0] * 5 + [120] * 6 + [-120] * 6 + [0] * 10)]
        self.assertEqual(sum(fired), 1)
        n2 = NodDetector()
        slow = [n2.update(r, 0, i * 0.02) for i, r in enumerate([120] * 6 + [0] * 40 + [-120] * 6)]
        self.assertEqual(sum(slow), 0)                            # 천천히 숙였다 드는 건 끄덕임 아님
        n3 = NodDetector()
        turning = [n3.update(r, 100, i * 0.02) for i, r in enumerate([120] * 6 + [-120] * 6)]
        self.assertEqual(sum(turning), 0)                         # 고개를 돌리는 중엔 무시


class EngineDeltaTest(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse()
        self.hooks = FakeHooks()
        self.e = Engine(Settings(dwell_popup=False, dwell_time=1.0), self.hooks)

    def tearDown(self):
        self.mouse.restore()

    def test_delta_moves_and_dwell_clicks(self):
        self.e.set_paused(False)
        t = 0.0
        for _ in range(30):
            t += 1 / 60
            self.e.on_delta(10, -5, t)
        self.assertEqual(self.mouse.pos, (960 + 300, 540 - 150))
        for _ in range(80):                                       # 가만히 → 머무르기 클릭
            t += 1 / 60
            self.e.on_delta(0, 0, t)
        self.assertEqual(self.mouse.count("left_click"), 1)

    def test_delta_clamped_to_screen(self):
        for i in range(200):
            self.e.on_delta(100, 100, i / 60)
        self.assertEqual(self.mouse.pos, (1919, 1079))


if __name__ == "__main__":
    unittest.main()
