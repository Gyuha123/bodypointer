"""BodyPointer 실행 진입점.

탐색기에서 더블클릭해도 동작하도록:
- 시스템 Python으로 열렸으면 프로젝트의 .venv Python(콘솔 없는 pythonw)으로 다시 실행한다.
- 오류가 나면 창이 조용히 닫히지 않게 메시지 상자로 알리고 로그를 남긴다.
"""
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PYW = HERE / ".venv" / "Scripts" / "pythonw.exe"
LOG = Path(os.environ.get("APPDATA", HERE)) / "BodyPointer" / "error.log"


def _message_box(text: str):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "BodyPointer 오류", 0x10)
    else:
        print(text, file=sys.stderr)


def _relaunch_in_venv() -> bool:
    """가상환경 밖에서 실행됐으면 가상환경 Python으로 다시 실행하고 True를 돌려준다."""
    in_venv = Path(sys.prefix).resolve() == (HERE / ".venv").resolve()
    if in_venv or os.environ.get("BODYPOINTER_RELAUNCHED"):
        return False
    if not VENV_PYW.exists():
        _message_box("가상환경(.venv)이 없습니다.\n\nbodypointer 폴더에서 다음을 실행하세요:\n"
                     "python -m venv .venv\n.venv\\Scripts\\pip install -r requirements.txt")
        return True
    env = dict(os.environ, BODYPOINTER_RELAUNCHED="1")
    subprocess.Popen([str(VENV_PYW), str(HERE / "main.py"), *sys.argv[1:]], cwd=str(HERE), env=env,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


def _already_running() -> bool:
    """같은 앱이 이미 켜져 있으면 True (Windows 이름 있는 뮤텍스)."""
    if sys.platform != "win32":
        return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _already_running.handle = kernel32.CreateMutexW(None, False, "Local\\BodyPointer.SingleInstance")
    return kernel32.GetLastError() == 183   # ERROR_ALREADY_EXISTS


def main():
    os.chdir(HERE)
    if _already_running():
        _message_box("BodyPointer가 이미 실행 중이에요.\n\n화면 오른쪽 툴바의 ‘설정’을 누르거나 Ctrl+Alt+S 로 창을 열 수 있어요.")
        return 0
    sys.path.insert(0, str(HERE))
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from bodypointer import vision
    vision.prepare_environment()                     # 웹 엔진 백그라운드 절전 끄기 (QApplication 이전)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("BodyPointer")
    qapp.setQuitOnLastWindowClosed(False)            # 설정 창을 닫아도 툴바로 계속 사용
    if sys.platform != "win32":
        _message_box("현재 버전은 Windows만 지원합니다.")
        return 1
    from bodypointer.app import App
    app = App(qapp)  # noqa: F841 (참조 유지)
    return qapp.exec()


if __name__ == "__main__":
    if _relaunch_in_venv():
        sys.exit(0)
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        err = traceback.format_exc()
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            LOG.write_text(err, "utf-8")
        except OSError:
            pass
        _message_box(f"실행 중 오류가 났습니다.\n\n{err[-1500:]}\n\n로그: {LOG}")
        sys.exit(1)
