@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Log file
set LOGDATE=%DATE:/=%
set LOGFILE=%~dp0output\pipeline_%LOGDATE:~0,8%.log
if not exist "%~dp0output" mkdir "%~dp0output"

echo ==============================================>>"%LOGFILE%"
echo HotStock Pipeline Report>>"%LOGFILE%"
echo Start: %DATE% %TIME%>>"%LOGFILE%"
echo ==============================================>>"%LOGFILE%"

echo ==============================================
echo   HotStock Reddit ^> Buffett Checklist Pipeline
echo ==============================================
echo.

IF NOT EXIST "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run: python -m venv venv >>"%LOGFILE%"
    echo [ERROR] Virtual environment not found
    echo Auto exit in 5 seconds...
    timeout /t 5 >nul
    exit /b 1
)

echo [INFO] Activating venv...>>"%LOGFILE%"
call venv\Scripts\activate.bat

set PYTHONIOENCODING=utf-8

echo.
echo [RUN] Pipeline: Top 8 stocks...
echo.

python reddit-checklist-pipeline.py --top 8 >>"%LOGFILE%" 2>&1

set EXIT_CODE=%ERRORLEVEL%
echo [DONE] Exit code: %EXIT_CODE%>>"%LOGFILE%"
echo ==============================================>>"%LOGFILE%"

echo.
if %EXIT_CODE% NEQ 0 (
    echo [ERROR] Pipeline failed (exit=%EXIT_CODE%), log: %LOGFILE%
) else (
    echo [OK] Pipeline completed!
)
echo Reports saved to output/ directory
echo Window closes in 10 seconds...
