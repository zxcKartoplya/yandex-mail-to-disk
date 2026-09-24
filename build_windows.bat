@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.12+ from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)
if not exist .venv-win\Scripts\python.exe py -3 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install -q -r requirements-dev.txt || goto :error
.venv-win\Scripts\python.exe -m pytest -q || goto :error
.venv-win\Scripts\pyinstaller.exe --onefile --console --clean --noconfirm --specpath build --workpath build --name YandexMailToDisk "%~dp0run.py" || goto :error
echo.
echo Done: dist\YandexMailToDisk.exe
pause
exit /b 0
:error
echo.
echo Build failed, see messages above.
pause
exit /b 1
