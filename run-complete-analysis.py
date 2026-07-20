#!/usr/bin/env python3
"""
完整分析流水线：读取已有 Reddit 热门股票数据 → 执行巴菲特 Checklist
====================================================================
从 output/ticker_report_*.json 读取 Reddit/News 热门股数据，
对每只个股执行 Checklist 分析，输出综合对比报告。

用法:
    source venv/bin/activate
    python run-complete-analysis.py                          # 使用最新 JSON
    python run-complete-analysis.py --top 8                  # 分析前 8 只
    python run-complete-analysis.py --json output/report.json # 指定数据源
    python run-complete-analysis.py --no-save                # 不保存报告
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

# ── 动态加载 investment-checklist（文件名含连字符） ──
_CHECKLIST = None


def _load_checklist():
    global _CHECKLIST
    if _CHECKLIST is not None:
        return _CHECKLIST
    spec = importlib.util.spec_from_file_location(
        "ic", str(BASE_DIR / "investment-checklist.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CHECKLIST = mod
    return mod


# ── ETF / 非个股关键词 ──
ETF_KEYWORDS = ["ETF", "Index", "Trust", "Mutual Fund"]
NON_STOCK_TICKERS = {
    "FY", "GMT", "AWS", "LTA", "DS", "DR", "DD", "DTE",
    "CEO", "CFO", "ATH", "YTD", "IPO", "REIT",
    "FOMO", "HODL", "WSB", "IMO", "TLDR", "ET",
    "PLC", "SCHD", "ATAI",
}


def star(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def conclusion_text(passed: int) -> str:
    return "✅ 通过" if passed >= 4 else "❓ 灰色" if passed >= 3 else "❌ 未通过"


# ╔══════════════════════════════════════════════════════════════╗
# ║  加载 Reddit 数据                                           ║
# ╚══════════════════════════════════════════════════════════════╝

def load_reddit_data(json_path: Optional[str] = None) -> Dict:
    """加载最新的或指定的 Reddit 热门股报告。"""
    if json_path:
        path = Path(json_path)
    else:
        output_dir = BASE_DIR / "output"
        json_files = sorted(output_dir.glob("ticker_report_*.json"))
        if not json_files:
            logger.error("❌ output/ 目录无 JSON 报告。先运行: python main.py --short --top 20 --json")
            sys.exit(1)
        path = json_files[-1]

    with open(path) as f:
        data = json.load(f)

    logger.info(f"📂 数据源: {path.name}")
    logger.info(f"   生成时间: {data['generated_at'][:19]}")
    logger.info(f"   来源: {', '.join(data['sources_used'])}")
    logger.info(f"   总帖: {data['total_posts']} | 含 ticker: {data['posts_with_tickers']} | 总提及: {data['total_ticker_mentions']}")
    return data


def filter_stocks(data: Dict, top_n: int) -> List[Dict]:
    """从 JSON 数据中筛选出个股（排除 ETF 等）。"""
    stocks = []
    for item in data["top_tickers"]:
        t = item["ticker"]
        name = item.get("company_name", "")
        if t in NON_STOCK_TICKERS:
            continue
        if any(k in name for k in ETF_KEYWORDS):
            continue
        stocks.append(item)
        if len(stocks) >= top_n:
            break
    return stocks


# ╔══════════════════════════════════════════════════════════════╗
# ║  执行 Checklist 分析                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def analyze_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """对单个股票执行完整 Checklist 分析。"""
    mod = _load_checklist()
    try:
        company = mod.identify_company(ticker)
        if company.get("error"):
            return None

        data = mod.collect_financial_data(company)
        news = mod.collect_news(ticker)
        grade, desc = mod.grade_information_availability(company)

        g1, g1r = mod.gate1_circle_of_competence(data, company)
        g2, g2d = mod.gate2_good_business(data)
        g3, g3d = mod.gate3_moat(data, company)
        g4, g4d = mod.gate4_management(data, company)
        g5, g5d = mod.gate5_safety_margin(data)
        g6, g6w = mod.gate6_decision_discipline(data)

        gates = {
            "gate1_score": g1, "gate1_reason": g1r,
            "gate2_score": g2, "gate2_details": g2d,
            "gate3_score": g3, "gate3_details": g3d,
            "gate4_score": g4, "gate4_details": g4d,
            "gate5_score": g5, "gate5_details": g5d,
            "gate6_score": g6, "gate6_warnings": g6w,
        }
        vetoes = mod.quick_veto_checklist(data, gates)
        total = g1 + g2 + g3 + g4 + g5
        passed = sum(1 for s in (g1, g2, g3, g4, g5) if s >= 3)

        return {
            "ticker": ticker,
            "company_name": company.get("name", ticker),
            "scores": {"能力圈": g1, "好生意": g2, "护城河": g3,
                       "管理层": g4, "安全边际": g5, "决策纪律": g6},
            "total": total,
            "passed": passed,
            "vetoes": vetoes,
            "data": {
                "price": data.get("price", 0),
                "mcap": data.get("market_cap", 0),
                "pe": data.get("pe_ttm"),
                "forward_pe": data.get("forward_pe"),
                "pb": data.get("pb"),
                "roe": data.get("roe_pct"),
                "gm": data.get("gross_margin_pct"),
                "nm": data.get("net_margin_pct"),
                "rg": data.get("revenue_growth_pct"),
                "fcf": data.get("free_cf"),
                "de": data.get("debt_to_equity"),
                "div": data.get("dividend_yield_pct"),
            },
            "news": news,
            "reddit_score": 0,  # will fill later
            "reddit_mentions": 0,
        }
    except Exception as e:
        logger.debug(f"  {ticker} 异常: {e}")
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  报告生成                                                   ║
# ╚══════════════════════════════════════════════════════════════╝

def generate_report(
    reddit_data: Dict,
    reddit_stocks: List[Dict],
    results: List[Dict[str, Any]],
) -> str:
    """生成综合对比报告。"""
    lines = []

    # ── 头部 ──
    lines.append("# Reddit 热门股票 · 巴菲特 Checklist 综合分析报告\n")
    lines.append(f"**分析日期**：{CURRENT_DATE}\n")
    lines.append(f"**数据来源**：{', '.join(reddit_data['sources_used'])}")
    lines.append(f"**数据时间**：{reddit_data['generated_at'][:19]}")
    lines.append(f"**总帖数**：{reddit_data['total_posts']} | **含代码**：{reddit_data['posts_with_tickers']} | **总提及**：{reddit_data['total_ticker_mentions']}\n")

    # ── Reddit 热度排行 ──
    lines.append("---\n")
    lines.append("## Reddit/News 热议个股排名\n")
    lines.append(f"| # | Ticker | 公司名称 | 综合分 | 提及 | 来源 |")
    lines.append(f"|---|--------|---------|:-----:|:----:|------|")
    for i, s in enumerate(reddit_stocks, 1):
        name = s.get("company_name", "")
        if len(name) > 28:
            name = name[:27] + "…"
        src = ", ".join(s["sources"][:2])
        lines.append(f"| {i} | {s['ticker']} | {name} | {s['score']:.1f} | {s['mentions']} | {src} |")
    lines.append("")

    # ── Checklist 对比总览 ──
    lines.append("---\n")
    lines.append("## 巴菲特 Checklist 对比总览\n")
    lines.append("| # | Ticker | 公司 | 市值 | PE | ROE | 毛利 | 能力圈 | 好生意 | 护城河 | 管理层 | 安全边际 | 纪律 | 总分 | 结论 |")
    lines.append("|---|--------|------|------|-----|------|------|--------|--------|--------|--------|---------|------|------|------|")

    for i, r in enumerate(results, 1):
        d = r["data"]
        name = r["company_name"][:16]
        mcap = f"{d['mcap']/1e9:.1f}B" if d['mcap'] else "N/A"
        pe = f"{d['pe']:.1f}" if d['pe'] else "N/A"
        roe = f"{d['roe']:.0f}%" if d['roe'] else "N/A"
        gm = f"{d['gm']:.0f}%" if d['gm'] else "N/A"
        s = r["scores"]
        lines.append(
            f"| {i} | {r['ticker']} | {name} | {mcap} | {pe} | {roe} | {gm} "
            f"| {star(s['能力圈'])} | {star(s['好生意'])} | {star(s['护城河'])} "
            f"| {star(s['管理层'])} | {star(s['安全边际'])} | {star(s['决策纪律'])} "
            f"| {r['total']}/25 | {conclusion_text(r['passed'])} |"
        )
    lines.append("")

    # ── 逐公司详情 ──
    lines.append("---\n")
    lines.append("## 逐公司详细分析\n")
    for r in results:
        lines.append("---\n")
        lines.append(f"### {r['company_name']}（{r['ticker']}）— {r['total']}/25\n")
        lines.append("| 关卡 | 评分 | 状态 |")
        lines.append("|------|:----:|:----:|")
        for gate_name in ["能力圈", "好生意", "护城河", "管理层", "安全边际", "决策纪律"]:
            score = r["scores"][gate_name]
            status = "✅ 通过" if score >= 3 else "❌ 不通过"
            lines.append(f"| {gate_name} | {star(score)} | {status} |")

        lines.append(f"\n**结论**：{conclusion_text(r['passed'])}（{r['passed']}/5 关通过）")

        if r["vetoes"]:
            lines.append("\n**⚠️ 触发否决**：")
            for v in r["vetoes"]:
                lines.append(f"- {v}")

        lines.append("\n**核心数据**：")
        d = r["data"]
        core = [
            ("股价", f"{d['price']:.2f}" if d['price'] else "N/A"),
            ("市值", f"{d['mcap']/1e9:.1f}B" if d['mcap'] else "N/A"),
            ("PE", f"{d['pe']}" if d['pe'] else "N/A"),
            ("前瞻PE", f"{d['forward_pe']}" if d['forward_pe'] else "N/A"),
            ("PB", f"{d['pb']}" if d['pb'] else "N/A"),
            ("ROE", f"{d['roe']:.1f}%" if d['roe'] else "N/A"),
            ("毛利率", f"{d['gm']:.1f}%" if d['gm'] else "N/A"),
            ("净利率", f"{d['nm']:.1f}%" if d['nm'] else "N/A"),
            ("收入增速", f"{d['rg']:.1f}%" if d['rg'] else "N/A"),
            ("自由现金流", f"{d['fcf']:,.0f}" if d['fcf'] else "N/A"),
            ("负债/权益", f"{d['de']:.1f}%" if d['de'] else "N/A"),
            ("股息率", f"{d['div']:.2f}%" if d['div'] else "N/A"),
        ]
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        for k, v in core:
            lines.append(f"| {k} | {v} |")

        if r["news"]:
            lines.append(f"\n**近期新闻（{len(r['news'])}条）**：")
            for n in r["news"][:4]:
                lines.append(f"- [{n.get('date', '')}] {n.get('title', '')}")
        lines.append("")

    # ── 总结 ──
    lines.append("---\n")
    lines.append("## 总结\n")
    passed_count = sum(1 for r in results if r["passed"] >= 4)
    gray_count = sum(1 for r in results if 3 <= r["passed"] < 4)
    fail_count = sum(1 for r in results if r["passed"] < 3)

    lines.append(f"- ✅ **通过 Checklist**（≥4 关）：{passed_count} 只")
    lines.append(f"- ❓ **灰色地带**（3 关通过）：{gray_count} 只")
    lines.append(f"- ❌ **未通过**（<3 关）：{fail_count} 只\n")

    for cat, icon, label in [
        (["通过"], "✅", "通过 Checklist"),
        (["灰色"], "❓", "灰色地带"),
        (["未通过"], "❌", "未通过"),
    ]:
        subset = [r for r in results if
                  (cat[0] == "通过" and r["passed"] >= 4) or
                  (cat[0] == "灰色" and 3 <= r["passed"] < 4) or
                  (cat[0] == "未通过" and r["passed"] < 3)]
        if subset:
            lines.append(f"**{icon} {label}**：")
            for r in subset:
                lines.append(f"- {r['company_name']}（{r['ticker']}）— {r['total']}/25")
            lines.append("")

    lines.append("---")
    lines.append(f"*报告由 Reddit → 巴菲特 Checklist 流水线自动生成*")
    lines.append(f"*分析日期：{CURRENT_DATE}*")
    lines.append("*⚠️ 免责声明：本报告为自动化分析工具，不构成投资建议*")
    lines.append("")
    lines.append('*"投资的第一条规则是不要亏损。第二条规则是不要忘记第一条。" — 沃伦·巴菲特*')

    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════════╗
# ║  Main                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def main():
    parser = argparse.ArgumentParser(
        description="Reddit 热门股票 → 巴菲特 Checklist 分析",
    )
    parser.add_argument("--top", type=int, default=10, help="分析前 N 只 (默认 10)")
    parser.add_argument("--json", type=str, help="指定 JSON 数据文件")
    parser.add_argument("--output", "-o", type=str, help="输出报告路径")
    parser.add_argument("--no-save", action="store_true", help="不保存报告")
    args = parser.parse_args()

    # Step 1: 加载 Reddit 数据
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Reddit 热门股票 → 巴菲特 Checklist 完整分析            ║")
    print(f"║  {CURRENT_DATE}                                    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    print("📂 Step 1/4: 加载 Reddit 热门股数据...")
    reddit_data = load_reddit_data(args.json)
    reddit_stocks = filter_stocks(reddit_data, args.top)
    print(f"  ✅ 筛选出 {len(reddit_stocks)} 只个股待分析")
    print(f"  📋 {', '.join(s['ticker'] for s in reddit_stocks)}")
    print()

    # Step 2: 执行 Checklist
    print("📊 Step 2/4: 执行巴菲特 Checklist...")
    results = []
    for i, stock in enumerate(reddit_stocks, 1):
        ticker = stock["ticker"]
        name = stock.get("company_name", "")
        print(f"  [{i}/{len(reddit_stocks)}] 🔍 {ticker} {name[:30]}...", end=" ", flush=True)
        result = analyze_ticker(ticker)
        if result:
            result["reddit_score"] = stock["score"]
            result["reddit_mentions"] = stock["mentions"]
            results.append(result)
            print(f"✅ {result['total']}/25 {conclusion_text(result['passed'])}")
        else:
            print("⚠️ 数据不足，跳过")
    print()

    if not results:
        print("❌ 没有成功完成任何分析。")
        sys.exit(1)

    # Step 3: 生成报告
    print("📄 Step 3/4: 生成综合报告...")
    report = generate_report(reddit_data, reddit_stocks[:args.top], results)

    if not args.no_save:
        output_dir = BASE_DIR / "output"
        output_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = args.output or str(output_dir / f"巴菲特Checklist-Reddit综合报告_{ts}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  💾 报告已保存: {path}")
    elif args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  📄 报告已输出: {args.output}")

    # Step 4: 输出摘要
    print()
    print("📊 Step 4/4: 结果摘要")
    print(f"  ✅ 通过（≥4关）：{sum(1 for r in results if r['passed'] >= 4)} 只")
    print(f"  ❓ 灰色（3关）：{sum(1 for r in results if 3 <= r['passed'] < 4)} 只")
    print(f"  ❌ 未通过（<3关）：{sum(1 for r in results if r['passed'] < 3)} 只")
    print()

    print(f"{'='*70}")
    print(f"{'#':>3s} {'Ticker':>6s} {'公司':<20s} {'Reddit分':>6s} {'总分':>4s} {'结论'}")
    print(f"{'='*70}")
    for i, r in enumerate(results, 1):
        name = r["company_name"][:18]
        print(f"{i:3d} {r['ticker']:>6s} {name:<20s} {r['reddit_score']:6.1f} {r['total']:>3d}/25 {conclusion_text(r['passed'])}")


if __name__ == "__main__":
    main()
