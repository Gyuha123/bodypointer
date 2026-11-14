@echo off
chcp 65001 >nul
rem 더블클릭으로 BodyPointer 실행 (콘솔 창 없이)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 가상환경이 없습니다. 이 폴더에서 다음을 실행하세요:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
