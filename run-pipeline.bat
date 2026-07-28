@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 日志文件（记录错误信息）
set LOGFILE=%~dp0output\pipeline_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log
if not exist "%~dp0output" mkdir "%~dp0output"

echo ════════════════════════════════════════════>>"%LOGFILE%"
echo   Reddit 热门股票 → 巴菲特 Checklist 分析流水线>>"%LOGFILE%"
echo   开始时间：%DATE% %TIME%>>"%LOGFILE%"
echo ════════════════════════════════════════════>>"%LOGFILE%"

echo ════════════════════════════════════════════
echo   Reddit 热门股票 → 巴菲特 Checklist 分析流水线
echo ════════════════════════════════════════════
echo.

REM ── 检查虚拟环境 ──
if not exist "venv\Scripts\python.exe" (
    echo ❌ 未找到虚拟环境 (venv)，请先执行初始化：
    echo    python -m venv venv
    echo    venv\Scripts\pip install -r requirements.txt
    echo    （5 秒后自动退出）
    timeout /t 5 >nul
    exit /b 1
)

echo 🔧 激活虚拟环境 ...
call venv\Scripts\activate.bat

REM 设置 UTF-8 编码，避免 GBK 无法显示 emoji
set PYTHONIOENCODING=utf-8

echo.
echo 🚀 运行流水线（Top 8，使用代理 127.0.0.1:3067）...
echo.

python reddit-checklist-pipeline.py --top 8 >>"%LOGFILE%" 2>&1

set EXIT_CODE=%ERRORLEVEL%
echo.
echo ✅ 执行完毕！退出码：%EXIT_CODE%>>"%LOGFILE%"
if %EXIT_CODE% NEQ 0 (
    echo ❌ 流水线执行失败，请查看日志：%LOGFILE%>>"%LOGFILE%"
)
echo ════════════════════════════════════════════>>"%LOGFILE%"
echo.
if %EXIT_CODE% NEQ 0 (
    echo ❌ 执行失败（退出码 %EXIT_CODE%），日志：%LOGFILE%
) else (
    echo ✅ 执行完毕！
)
echo 报告已保存到 output/ 目录
echo 窗口将在 10 秒后自动关闭...
