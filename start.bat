@echo off
title J.A.R.V.I.S. Starter Kit
cd /d "%~dp0"

echo.
echo   ============================================
echo     J.A.R.V.I.S.  -  Starter Kit
echo   ============================================
echo.

REM --- find a working Python (auto-installs 3.11 if missing) ---
REM Use `python --version` (not just `where`) so we see through the Microsoft
REM Store "python.exe" alias stub, which is on PATH but doesn't actually run.
python --version >nul 2>nul
if %errorlevel% equ 0 (set "PY=python" & goto :havepy)

echo   [!] Python was not found - downloading Python 3.11 now.
echo       (one-time, no admin needed; ~25 MB)
echo.
set "PYINST=%TEMP%\python-3.11.9-amd64.exe"
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PYINST%'"
if not exist "%PYINST%" (
  echo   [!] Could not download Python. Check your internet connection, or install
  echo       Python 3.11 manually from https://python.org ^(tick "Add Python to PATH"^).
  pause
  exit /b
)
echo   Installing Python 3.11 (this can take a minute)...
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "%PY%" goto :havepy
python --version >nul 2>nul
if %errorlevel% equ 0 (set "PY=python" & goto :havepy)
echo   [!] Python was installed but not detected in this window. Please CLOSE this
echo       window and run start.bat again to finish.
pause
exit /b

:havepy

REM --- first-run: install the few small dependencies ---
if not exist ".deps_installed" (
  echo   Installing dependencies ^(one time only^)...
  "%PY%" -m pip install --upgrade pip >nul 2>nul
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   [!] Dependency install failed. Check your internet connection and try again.
    pause
    exit /b
  )
  echo done> ".deps_installed"
)

echo.
echo   Starting J.A.R.V.I.S. ...  the browser will open shortly.
echo   ^(Leave this window open while you use him. Close it to shut down.^)
echo.

REM --- open the UI a moment after the server starts ---
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:5000"

"%PY%" app.py
pause
