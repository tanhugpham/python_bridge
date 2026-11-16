@echo off
setlocal EnableExtensions
title FISHINGLIVE - START + TEST
cd /d "%~dp0"

echo ==========================================
echo       FISHINGLIVE CONTROL CENTER
echo ==========================================

set "PYTHONPATH=src"
set "BRIDGE_MODE=mock"
set "BRIDGE_HOST=127.0.0.1"
set "BRIDGE_PORT=8787"

set "PYTHON="
set "PY_RUN="

rem Chon Python dau tien chay duoc va co fastapi + uvicorn.
rem .venv313 co the la ban copy tu may khac (base interpreter khong con),
rem nen phai thu that chu khong chi kiem tra file ton tai.
rem Tham so 2 la lenh tuong doi de cua so Bridge khoi loi quote khi path co dau cach.
call :FIND_PYTHON "%~dp0.venv313\Scripts\python.exe" ".venv313\Scripts\python.exe"
call :FIND_PYTHON "%~dp0.venv\Scripts\python.exe" ".venv\Scripts\python.exe"

if not defined PYTHON (
    py.exe -3 -c "import fastapi, uvicorn" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON=py.exe"
        set "PY_RUN=py.exe -3"
    )
)

if not defined PYTHON (
    python.exe -c "import fastapi, uvicorn" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON=python.exe"
        set "PY_RUN=python.exe"
    )
)

if not defined PYTHON (
    echo ERROR: Khong tim thay Python chay duoc co fastapi + uvicorn.
    echo.
    echo Da thu: .venv313, .venv, py -3, python.exe
    echo Tao moi truong moi bang lenh sau:
    echo     py -3 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    echo Goi y: thu muc .venv313 hien tai duoc tao tren may khac
    echo voi base interpreter C:\Users\ctytp\...\Python313 khong ton tai.
    echo Co the xoa thu muc .venv313 de tranh nham lan.
    pause
    exit /b 1
)

echo Python: %PYTHON%

echo.
echo [1/3] Kiem tra Bridge...

curl.exe -fsS --max-time 3 http://127.0.0.1:8787/v1/health >nul 2>&1

if errorlevel 1 (
    echo Khoi dong Python Bridge...
    rem PYTHONPATH, BRIDGE_MODE, BRIDGE_HOST, BRIDGE_PORT duoc ke thua tu batch nay.
    rem /D dat thu muc lam viec; lenh tuong doi nen khong bi loi quote khi path co dau cach.
    start "FISHINGLIVE PYTHON BRIDGE" /D "%~dp0" cmd /k "%PY_RUN% -m fishinglive_bridge"
)

echo.
echo [2/3] Cho Bridge san sang...

set /a attempts=0

:CHECK
curl.exe -fsS --max-time 3 http://127.0.0.1:8787/v1/health >nul 2>&1

if not errorlevel 1 goto READY

set /a attempts+=1

if %attempts% GEQ 20 (
    echo ERROR: Bridge khong phan hoi.
    echo Kiem tra cua so Python Bridge.
    pause
    exit /b 1
)

rem ping thay cho timeout: timeout loi khi stdin bi redirect / khong co console.
ping -n 3 127.0.0.1 >nul
goto CHECK

:READY
echo Bridge: ONLINE

echo.
echo [3/3] Mo HTML Control Center...

start "" "http://127.0.0.1:8787/host"

echo.
echo ==========================================
echo FISHINGLIVE READY
echo HTML: http://127.0.0.1:8787/host
echo MODE: MOCK
echo ==========================================

echo.
echo Mo Roblox Studio va nhan Play de test.
echo Khong dong cua so Python Bridge khi dang test.
pause
exit /b 0

rem Tham so %1: duong dan python.exe. %2: lenh tuong doi cho cua so Bridge.
rem Chi nhan interpreter chay duoc va import duoc fastapi + uvicorn
rem (venv copy tu may khac se truot).
:FIND_PYTHON
if defined PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYTHON=%~1"
set "PY_RUN=%~2"
exit /b 0