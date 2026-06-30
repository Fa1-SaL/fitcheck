@echo off
setlocal
title FitCheck Installer & Launcher

:: Navigate to script directory to ensure relative paths work
cd /d "%~dp0"

echo [1/5] Checking Python installation...
set PYTHON_CMD=

:: Check if 'python' command works and is real (not Windows Store alias)
python -c "import sys; sys.exit(0)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=python
    goto :PYTHON_FOUND
)

:: Check if 'py' launcher works
py -3 -c "import sys; sys.exit(0)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py -3
    goto :PYTHON_FOUND
)

:: Check default per-user install locations across Python versions
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" (
        set PYTHON_CMD="%%~D\python.exe"
        goto :PYTHON_FOUND
    )
)

:: Check system-wide install locations across Python versions
for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" (
        set PYTHON_CMD="%%~D\python.exe"
        goto :PYTHON_FOUND
    )
)
for /d %%D in ("%ProgramFiles(x86)%\Python3*") do (
    if exist "%%~D\python.exe" (
        set PYTHON_CMD="%%~D\python.exe"
        goto :PYTHON_FOUND
    )
)

:: Python not found - Download and Install
echo Python was not detected on your system.
echo [2/5] Downloading Python 3.11 automatically (approx. 25 MB)...
set PYTHON_INSTALLER=%TEMP%\python_installer_311.exe

:: Try curl first (built into Windows 10/11), fallback to PowerShell
curl --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%PYTHON_INSTALLER%" "https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe"
) else (
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe' -OutFile '%PYTHON_INSTALLER%'"
)

if not exist "%PYTHON_INSTALLER%" (
    echo.
    echo ERROR: Failed to download Python installer. Please check your internet connection.
    pause
    exit /b 1
)

echo.
echo Installing Python silently (this may take 1-2 minutes)...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Python installation failed.
    pause
    exit /b 1
)
del "%PYTHON_INSTALLER%" >nul 2>&1

:: Verify installation location
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" (
        set PYTHON_CMD="%%~D\python.exe"
        goto :PYTHON_FOUND
    )
)

python -c "import sys; sys.exit(0)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=python
    goto :PYTHON_FOUND
)

echo.
echo ERROR: Python was installed, but could not be located. Please restart this script.
pause
exit /b 1

:PYTHON_FOUND
echo Python detected: %PYTHON_CMD%
echo.

echo [3/5] Checking virtual environment...
if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment (venv)...
    %PYTHON_CMD% -m venv venv
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
) else (
    echo Virtual environment already exists.
)
echo.

echo [4/5] Checking app dependencies...
set NEED_INSTALL=0
if not exist "venv\Scripts\streamlit.exe" set NEED_INSTALL=1
if not exist "venv\.installed" set NEED_INSTALL=1

if %NEED_INSTALL% EQU 1 (
    echo Installing required app dependencies (this may take a couple minutes on first launch)...
    venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
    venv\Scripts\pip.exe install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo ERROR: Failed to install required dependencies.
        pause
        exit /b 1
    )
    type nul > "venv\.installed"
    echo Dependencies installed successfully.
) else (
    echo Dependencies already installed. Skipping pip install for fast startup.
)
echo.

echo [5/5] Launching FitCheck App...
echo Starting local Streamlit server...
venv\Scripts\streamlit.exe run app/main.py

pause
