#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# 激活虚拟环境
source venv/bin/activate 2>/dev/null || {
    echo "❌ 未找到虚拟环境，正在创建..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt feedparser
}

echo "════════════════════════════════════════════"
echo "  多源论坛热议股票扫描器"
echo "  数据源: Reddit + SeekingAlpha + YahooFinance"
echo "         + NASDAQ + InvestorPlace + GoogleNews"
echo "════════════════════════════════════════════"
echo ""

# 检查 .env 文件（可选）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 运行
python main.py --short --top 20 --json

echo ""
echo "✅ 完成！报告已保存到 output/ 目录"
