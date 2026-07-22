@echo off
chcp 65001 >nul
cd /d "%~dp0"

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
echo 🚀 运行流水线（Top 15，使用代理 127.0.0.1:3067）...
echo.

python reddit-checklist-pipeline.py --top 15

echo.
echo ✅ 执行完毕！
echo.
echo 报告已保存到 output/ 目录
echo 窗口将在 10 秒后自动关闭...
