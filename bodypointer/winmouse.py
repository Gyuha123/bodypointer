"""Windows 마우스 입력 (FR-20) - ctypes로 SetCursorPos / SendInput 직접 호출.

좌표는 모두 물리 픽셀(주 모니터 기준). Qt가 프로세스를 Per-Monitor DPI aware로 설정하므로
GetSystemMetrics / SetCursorPos 는 물리 픽셀로 동작한다.
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("현재 버전은 Windows만 지원합니다 (macOS/Linux는 P1).")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)


def _send(*flags_data):
    arr = (INPUT * len(flags_data))()
    for i, (flags, data) in enumerate(flags_data):
        arr[i].type = INPUT_MOUSE
        arr[i].u.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move(x: float, y: float):
    user32.SetCursorPos(int(round(x)), int(round(y)))


def left_click():
    _send((MOUSEEVENTF_LEFTDOWN, 0), (MOUSEEVENTF_LEFTUP, 0))


def right_click():
    _send((MOUSEEVENTF_RIGHTDOWN, 0), (MOUSEEVENTF_RIGHTUP, 0))


def double_click():
    left_click()
    time.sleep(0.05)
    left_click()


def left_down():
    _send((MOUSEEVENTF_LEFTDOWN, 0))


def left_up():
    _send((MOUSEEVENTF_LEFTUP, 0))


def wheel(notches: float):
    """양수=위로, 음수=아래로. 커서 아래 창이 받는다."""
    _send((MOUSEEVENTF_WHEEL, int(notches * WHEEL_DELTA)))


WM_MOUSEWHEEL = 0x020A
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


# ---------------- 키보드 입력 (키보드 바, 편집 동작) ----------------
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "space": 0x20,
    "plus": 0xBB, "minus": 0xBD, "f5": 0x74, "hangul": 0x15,
}
_EXTENDED = {0x2E, 0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x5B}


def _key_input(vk=0, scan=0, flags=0):
    i = INPUT()
    i.type = INPUT_KEYBOARD
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


def _send_inputs(items):
    if not items:
        return
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))


def _vk(name: str) -> int:
    name = name.lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"알 수 없는 키: {name}")


def press_combo(*keys: str):
    """press_combo('ctrl', 'c') 처럼 조합 키를 누르고 뗀다."""
    vks = [_vk(k) for k in keys]
    _send_inputs([_key_input(v) for v in vks] + [_key_input(v, flags=KEYEVENTF_KEYUP) for v in reversed(vks)])


def type_text(text: str):
    """유니코드 글자를 그대로 입력 (한글 입력기 상태와 무관)."""
    items = []
    data = text.encode("utf-16-le")
    for k in range(0, len(data), 2):
        code = int.from_bytes(data[k:k + 2], "little")
        items += [_key_input(scan=code, flags=KEYEVENTF_UNICODE),
                  _key_input(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    _send_inputs(items)


def backspace(n: int = 1):
    if n > 0:
        v = VK["backspace"]
        _send_inputs([x for _ in range(n) for x in (_key_input(v), _key_input(v, flags=KEYEVENTF_KEYUP))])


user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
GA_ROOT = 2


def root_window_at(x: float, y: float) -> int:
    """(x, y)에 보이는 최상위 창 핸들 (자체 UI 위인지 판별용)."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return user32.GetAncestor(hwnd, GA_ROOT) or 0


def wheel_at(x: float, y: float, notches: float):
    """커서를 옮기지 않고 (x, y) 아래 창에 휠 메시지를 보낸다 (가상 스크롤 버튼용)."""
    x, y = int(x), int(y)
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return
    delta = int(notches * WHEEL_DELTA) & 0xFFFF
    user32.PostMessageW(hwnd, WM_MOUSEWHEEL, delta << 16, ((y & 0xFFFF) << 16) | (x & 0xFFFF))


# ---------------- 전역 단축키 (FR-11, FR-34) ----------------
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_PAUSE = 0x13


class HotkeyListener:
    """RegisterHotKey 기반 전역 단축키. callback(name)은 별도 스레드에서 호출된다."""

    def __init__(self, bindings: dict[str, tuple[int, int]], callback):
        self.bindings = bindings
        self.callback = callback
        self._thread_id = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self.failed: list[str] = []

    def start(self):
        self._thread.start()

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        ids = {}
        for i, (name, (mods, vk)) in enumerate(self.bindings.items(), start=1):
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = name
            else:
                self.failed.append(name)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.callback(ids[msg.wParam])
                except Exception as e:  # 콜백 오류로 단축키 스레드가 죽지 않게
                    print("hotkey callback error:", e)
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
