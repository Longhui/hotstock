#!/usr/bin/env python3
"""
Reddit 热门股票 → 巴菲特 Checklist 分析流水线
==============================================
1. 从 Reddit 多个子版块 RSS 抓取热议帖子
2. 提取股票代码并综合评分
3. 对 top N 公司执行巴菲特 Checklist 分析
4. 输出整体对比总览报告

用法:
    source venv/bin/activate
    python reddit-checklist-pipeline.py                     # 默认 top 10
    python reddit-checklist-pipeline.py --top 5             # top 5
    python reddit-checklist-pipeline.py --min-score 50      # 最低综合分门槛
    python reddit-checklist-pipeline.py --no-save           # 不保存报告
    python reddit-checklist-pipeline.py --debug             # 调试日志

依赖: requests, feedparser, yfinance, beautifulsoup4, lxml
"""

import argparse
import importlib.util
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ── 导入项目现有模块 ──
# sources/reddit_scraper.py (RSS 快速抓取)
from sources.reddit_scraper import SUBREDDITS, extract_tickers, HEADERS

# analyzer (评分引擎)
from analyzer import compute_ticker_scores, get_source_weight

# company_names (公司名称)
from company_names import batch_lookup

# ── 延迟导入 investment-checklist（文件名含连字符，需要 importlib） ──
CHECKLIST_MODULE = None


def _load_checklist():
    """动态加载 investment-checklist 模块。"""
    global CHECKLIST_MODULE
    if CHECKLIST_MODULE is not None:
        return CHECKLIST_MODULE
    spec = importlib.util.spec_from_file_location(
        "investment_checklist",
        os.path.join(BASE_DIR, "investment-checklist.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    CHECKLIST_MODULE = mod
    return mod


CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 1: Reddit RSS 快速抓取 (跳过 Pushshift)              ║
# ╚══════════════════════════════════════════════════════════════╝

def fetch_reddit_rss(sub: str, max_posts: int = 20, max_age_days: int = 7) -> list:
    """从单个 Reddit 子版块 RSS 获取帖子（快速，无需 API 凭证）。"""
    import requests
    import feedparser

    url = f"https://www.reddit.com/r/{sub}/.rss"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    posts = []

    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        if resp.status_code != 200:
            logger.debug(f"  RSS r/{sub}: HTTP {resp.status_code}")
            return []
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            published = entry.get("published_parsed")
            created = None
            if published:
                try:
                    created = datetime(*published[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
            if created and created < cutoff:
                continue

            title = entry.get("title", "")
            summary_raw = entry.get("summary", "") or ""
            summary = re.sub(r"<[^>]+>", "", summary_raw)
            body = f"{title} {summary}"
            link = entry.get("link", "")

            posts.append({
                "source": "reddit",
                "source_sub": sub,
                "title": title,
                "body": summary,
                "url": link,
                "score": 0,
                "comments": 0,
                "created_utc": created,
                "tickers_mentioned": extract_tickers(body),
            })
            if len(posts) >= max_posts:
                break
    except Exception as e:
        logger.debug(f"RSS r/{sub}: {e}")

    return posts


def fetch_all_subs(max_posts_per_sub: int = 20, max_age_days: int = 7,
                   sub_filter: Optional[List[str]] = None) -> list:
    """遍历子版块，聚合 RSS 帖子。"""
    subs = sub_filter or SUBREDDITS
    all_posts = []
    for sub in subs:
        try:
            posts = fetch_reddit_rss(sub, max_posts_per_sub, max_age_days)
            if posts:
                logger.info(f"  r/{sub}: {len(posts)} 条")
            all_posts.extend(posts)
        except Exception as e:
            logger.warning(f"r/{sub}: {e}")
        time.sleep(0.3)  # 限流礼貌
    return all_posts


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 2: 评分与排名                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def rank_tickers(posts: list) -> List[Dict[str, Any]]:
    """使用项目已有的评分引擎对 ticker 排名。"""
    # 转成 Post dataclass
    from sources import Post
    post_objects = []
    for p in posts:
        post_objects.append(Post(
            source=p["source"],
            source_sub=p["source_sub"],
            title=p["title"],
            body=p["body"],
            url=p["url"],
            score=p["score"],
            comments=p["comments"],
            created_utc=p["created_utc"],
            tickers_mentioned=p["tickers_mentioned"],
        ))
    scores = compute_ticker_scores(post_objects)
    return scores


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 3: 巴菲特 Checklist 分析                              ║
# ╚══════════════════════════════════════════════════════════════╝

def analyze_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """对单个 ticker 执行投资 Checklist 分析。"""
    mod = _load_checklist()
    try:
        # 识别公司
        company = mod.identify_company(ticker)
        if company.get("error"):
            logger.warning(f"  ⚠️ {ticker}: {company['error']}")
            return None

        # 数据收集
        data = mod.collect_financial_data(company)
        news = mod.collect_news(ticker)

        # 信息评级
        grade, grade_desc = mod.grade_information_availability(company)

        # 六关评分
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
            "sector": data.get("sector", ""),
            "price": data.get("price", 0),
            "market_cap": data.get("market_cap", 0),
            "pe": data.get("pe_ttm"),
            "roe": data.get("roe_pct"),
            "gross_margin": data.get("gross_margin_pct"),
            "scores": {
                "能力圈": g1,
                "好生意": g2,
                "护城河": g3,
                "管理层": g4,
                "安全边际": g5,
                "决策纪律": g6,
            },
            "total": total,
            "passed_gates": passed,
            "vetoes": vetoes,
            "data": data,
            "gates": gates,
            "news": news,
            "info_grade": grade,
        }
    except Exception as e:
        logger.warning(f"  ⚠️ {ticker} 分析异常: {e}")
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 4: 综合报告生成                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def star(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def conclusion_text(passed: int) -> str:
    if passed >= 4:
        return "✅ 通过"
    elif passed >= 3:
        return "❓ 灰色"
    else:
        return "❌ 未通过"


def generate_comprehensive_report(
    reddit_summary: str,
    results: List[Dict[str, Any]],
    top_n: int,
) -> str:
    """生成综合对比报告。"""
    lines = []
    lines.append(f"# Reddit 热门股票 · 巴菲特 Checklist 综合分析报告\n")
    lines.append(f"**分析日期**：{CURRENT_DATE}\n")
    lines.append(f"**分析覆盖**：Top {top_n} 热门股\n")
    lines.append(f"**数据来源**：Reddit RSS（{len(results)}只可分析）+ yfinance\n")

    # ── Reddit 热度概况 ──
    lines.append("---\n")
    lines.append("## Reddit 讨论概况\n")
    lines.append(reddit_summary)
    lines.append("")

    # ── 对比总览表 ──
    lines.append("---\n")
    lines.append("## 巴菲特 Checklist 对比总览\n")
    lines.append("")
    lines.append("| # | Ticker | 公司 | 市值 | PE | ROE | 毛利 | 能力圈 | 好生意 | 护城河 | 管理层 | 安全边际 | 纪律 | 总分 | 结论 |")
    lines.append("|---|--------|------|------|-----|------|------|--------|--------|--------|--------|---------|------|------|------|")

    for i, r in enumerate(results, 1):
        name = r["company_name"]
        if len(name) > 16:
            name = name[:15] + "…"
        mcap = f"{r['market_cap'] / 1e9:.1f}B" if r["market_cap"] else "N/A"
        pe = f"{r['pe']:.1f}" if r["pe"] else "N/A"
        roe = f"{r['roe']:.0f}%" if r["roe"] else "N/A"
        gm = f"{r['gross_margin']:.0f}%" if r["gross_margin"] else "N/A"
        s = r["scores"]
        total = r["total"]
        concl = conclusion_text(r["passed_gates"])
        lines.append(
            f"| {i} | {r['ticker']} | {name} | {mcap} | {pe} | {roe} | {gm} "
            f"| {star(s['能力圈'])} | {star(s['好生意'])} | {star(s['护城河'])} "
            f"| {star(s['管理层'])} | {star(s['安全边际'])} | {star(s['决策纪律'])} "
            f"| {total}/25 | {concl} |"
        )

    # ── 逐公司详细评分 ──
    lines.append("")
    lines.append("---\n")
    lines.append("## 逐公司详细分析\n")

    for r in results:
        ticker = r["ticker"]
        name = r["company_name"]
        s = r["scores"]
        total = r["total"]
        lines.append(f"---\n")
        lines.append(f"### {name}（{ticker}）— {total}/25\n")
        lines.append(f"| 关卡 | 评分 | 状态 |")
        lines.append(f"|------|:----:|:----:|")

        for gate_name in ["能力圈", "好生意", "护城河", "管理层", "安全边际", "决策纪律"]:
            score = s[gate_name]
            status = "✅ 通过" if score >= 3 else "❌ 不通过"
            lines.append(f"| {gate_name} | {star(score)} | {status} |")

        lines.append(f"")
        lines.append(f"**结论**：{conclusion_text(r['passed_gates'])}（{r['passed_gates']}/5 关通过）")

        # 否决触发
        if r["vetoes"]:
            lines.append(f"\n**⚠️ 触发否决**：")
            for v in r["vetoes"]:
                lines.append(f"- {v}")

        # 核心数据
        lines.append(f"\n**核心数据**：")
        d = r["data"]
        core_items = [
            ("股价", f"{d.get('price', 0):.2f}"),
            ("市值", f"{d.get('market_cap', 0) / 1e9:.1f}B"),
            ("PE", f"{d.get('pe_ttm', 'N/A')}"),
            ("前瞻PE", f"{d.get('forward_pe', 'N/A')}"),
            ("PB", f"{d.get('pb', 'N/A')}"),
            ("ROE", f"{d.get('roe_pct', 'N/A')}%" if d.get("roe_pct") else "N/A"),
            ("毛利率", f"{d.get('gross_margin_pct', 'N/A')}%" if d.get("gross_margin_pct") else "N/A"),
            ("净利率", f"{d.get('net_margin_pct', 'N/A')}%" if d.get("net_margin_pct") else "N/A"),
            ("收入增速", f"{d.get('revenue_growth_pct', 'N/A')}%" if d.get("revenue_growth_pct") else "N/A"),
        ]
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        for k, v in core_items:
            lines.append(f"| {k} | {v} |")

        # 近期新闻
        news = r.get("news", [])
        if news:
            lines.append(f"\n**近期新闻（{len(news)}条）**：")
            for n in news[:4]:
                title = n.get("title", "")
                date = n.get("date", "")
                lines.append(f"- [{date}] {title}")

        lines.append("")

    # ── 总结 ──
    lines.append("---\n")
    lines.append("## 总结\n")

    passed_count = sum(1 for r in results if r["passed_gates"] >= 4)
    gray_count = sum(1 for r in results if 3 <= r["passed_gates"] < 4)
    fail_count = sum(1 for r in results if r["passed_gates"] < 3)

    lines.append(f"- ✅ **通过 Checklist**（≥4关）：{passed_count} 只")
    lines.append(f"- ❓ **灰色地带**（3关通过）：{gray_count} 只")
    lines.append(f"- ❌ **未通过**（<3关）：{fail_count} 只")
    lines.append("")
    lines.append("### Checklist 通过公司")
    for r in results:
        if r["passed_gates"] >= 4:
            lines.append(f"- ✅ {r['company_name']}（{r['ticker']}）")
    lines.append("")
    lines.append("### 灰色地带公司")
    for r in results:
        if 3 <= r["passed_gates"] < 4:
            lines.append(f"- ❓ {r['company_name']}（{r['ticker']}）— 总分 {r['total']}/25")
    lines.append("")
    lines.append("### 未通过公司")
    for r in results:
        if r["passed_gates"] < 3:
            lines.append(f"- ❌ {r['company_name']}（{r['ticker']}）— 总分 {r['total']}/25")

    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 Reddit → 巴菲特 Checklist 流水线自动生成*")
    lines.append(f"*分析日期: {CURRENT_DATE}*")
    lines.append("*⚠️ 免责声明：本报告为自动化分析工具，不构成投资建议*")
    lines.append("")
    lines.append("*\"投资的第一条规则是不要亏损。第二条规则是不要忘记第一条。\" — 沃伦·巴菲特*")

    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════════╗
# ║  Main                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def main():
    parser = argparse.ArgumentParser(
        description="Reddit 热门股票 → 巴菲特 Checklist 分析流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  source venv/bin/activate\n"
            "  python reddit-checklist-pipeline.py\n"
            "  python reddit-checklist-pipeline.py --top 5\n"
            "  python reddit-checklist-pipeline.py --min-score 50 --no-save\n"
            "  python reddit-checklist-pipeline.py --subs wallstreetbets,stocks\n"
        ),
    )
    parser.add_argument("--top", type=int, default=8, help="分析前 N 只热门股 (默认 8)")
    parser.add_argument("--min-score", type=float, default=0,
                        help="最低综合分门槛 (默认 0 = 不限)")
    parser.add_argument("--max-posts", type=int, default=25,
                        help="每子版块最多抓取帖子数 (默认 25)")
    parser.add_argument("--no-save", action="store_true",
                        help="不保存报告到文件")
    parser.add_argument("--output", "-o",
                        help="输出报告到指定文件路径")
    parser.add_argument("--subs",
                        help="指定子版块，逗号分隔 (默认全部)")
    parser.add_argument("--debug", action="store_true",
                        help="调试日志")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    sub_list = [s.strip() for s in args.subs.split(",")] if args.subs else None

    # 优先使用价值投资类子版块（信号质量更高）
    if sub_list is None:
        sub_list = [
            "ValueInvesting", "SecurityAnalysis",
            "investing", "stocks", "StockMarket",
            "wallstreetbets",
        ]
        logger.info(f"  使用精选子版块: {', '.join(sub_list)}")

    # 已知非股票 ticker 黑名单
    NON_STOCK_TICKERS = {
        "FY", "GMT", "AWS", "LTA", "DS", "CEO", "CFO", "ATH", "YTD",
        "IPO", "ETF", "REIT", "YOY", "Q1", "Q2", "Q3", "Q4",
        "FOMO", "HODL", "WSB", "BTFD", "IMO", "TLDR",
    }

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Reddit 热门股票 → 巴菲特 Checklist 分析流水线          ║")
    print(f"║  {CURRENT_DATE}                                    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Step 1: RSS 抓取 ──
    print("🌐 Step 1/4: 抓取 Reddit RSS ...")
    all_posts = fetch_all_subs(
        max_posts_per_sub=args.max_posts,
        sub_filter=sub_list,
    )
    posts_with_tickers = [p for p in all_posts if p.get("tickers_mentioned")]
    print(f"  ✅ 总帖子: {len(all_posts)}，含股票代码: {len(posts_with_tickers)}")

    if not posts_with_tickers:
        print("❌ 未获取到含股票代码的帖子，退出。")
        sys.exit(1)

    # ── Step 2: 评分排名 ──
    print(f"\n📊 Step 2/4: 评分排名 ...")
    all_scores = rank_tickers(all_posts)
    if not all_scores:
        print("❌ 评分结果为空，退出。")
        sys.exit(1)

    # 过滤：排除已知非个股（ETF、指数等）
    NON_STOCK_TICKERS = {
        "FY", "GMT", "AWS", "LTA", "DS", "DR",
        "CEO", "CFO", "ATH", "YTD", "IPO", "ETF", "REIT",
        "FOMO", "HODL", "WSB", "BTFD", "IMO", "TLDR",
        "CNQQ", "KWEB", "VOO", "VTI", "SPY", "QQQ", "DIA", "IWM",
    }
    filtered = [s for s in all_scores
                if s["score"] >= args.min_score
                and s["ticker"] not in NON_STOCK_TICKERS]
    top_scores = filtered[:args.top]
    logger.info(f"  排名前 {len(top_scores)}: {', '.join(s['ticker'] for s in top_scores)}")

    # 查公司名称
    tickers_to_query = [s["ticker"] for s in top_scores]
    logger.info(f"🔍 查询公司名称...")
    names = batch_lookup(tickers_to_query)

    # ── Step 3: 巴菲特 Checklist 分析 ──
    target_tickers = [s["ticker"] for s in top_scores]
    print(f"\n📋 Step 3/4: 执行巴菲特 Checklist 分析（{len(target_tickers)} 只）...")

    results: List[Dict[str, Any]] = []
    for ticker in target_tickers:
        print(f"  🔍 分析 {ticker}...", end=" ", flush=True)
        result = analyze_ticker(ticker)
        if result:
            results.append(result)
            name = result["company_name"]
            total = result["total"]
            concl = conclusion_text(result["passed_gates"])
            print(f"✅ {name} — {total}/25 {concl}")
        else:
            print(f"⚠️ 跳过（数据不足）")

    if not results:
        print("\n❌ 没有成功完成任何分析。")
        sys.exit(1)

    # ── 生成 Reddit 热度摘要 ──
    reddit_lines = []
    reddit_lines.append(f"**Reddit 子版块**：{', '.join(sub_list or SUBREDDITS)}")
    reddit_lines.append(f"**抓取帖子**：{len(all_posts)} 条（含 ticker: {len(posts_with_tickers)} 条）")
    reddit_lines.append(f"**热门 Ticker 排名**：\n")
    reddit_lines.append(f"| 排名 | Ticker | 公司 | 综合分 | 提及 | 来源数 |")
    reddit_lines.append(f"|:---:|:------:|------|:-----:|:----:|:-----:|")
    for i, item in enumerate(top_scores, 1):
        name = names.get(item["ticker"], "")
        reddit_lines.append(
            f"| {i} | {item['ticker']} | {name} | {item['score']:.1f} | "
            f"{item['mentions']} | {len(item['sources'])} |"
        )
    reddit_summary = "\n".join(reddit_lines)

    # ── Step 4: 综合报告 ──
    print(f"\n📄 Step 4/4: 生成综合报告...")
    report = generate_comprehensive_report(reddit_summary, results, args.top)

    # 保存或打印
    if not args.no_save:
        output_dir = os.path.join(BASE_DIR, "output")
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = args.output or os.path.join(output_dir, f"巴菲特Checklist-Reddit综合报告_{timestamp}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  💾 报告已保存: {path}")
    elif args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  📄 报告已输出: {args.output}")

    # 打印摘要
    print(f"\n{'=' * 60}")
    print(f"  ✅ 流水线完成！")
    print(f"  Reddit 抓取: {len(all_posts)} 帖子")
    print(f"  Checklist 分析: {len(results)}/{len(target_tickers)} 只")
    print(f"  通过: {sum(1 for r in results if r['passed_gates'] >= 4)} 只")
    print(f"  灰色: {sum(1 for r in results if 3 <= r['passed_gates'] < 4)} 只")
    print(f"  未通过: {sum(1 for r in results if r['passed_gates'] < 3)} 只")
    print(f"{'=' * 60}")
    print()

    # 终端输出简短排名
    print("📊 热门股 Checklist 评分排名")
    print(f"{'#':>3s}  {'Ticker':>6s}  {'公司':<20s}  {'总分':>4s}  {'结论'}")
    print("-" * 55)
    for i, r in enumerate(results, 1):
        name = r["company_name"]
        if len(name) > 18:
            name = name[:17] + "…"
        print(f"{i:3d}  {r['ticker']:>6s}  {name:<20s}  {r['total']:>3d}/25  {conclusion_text(r['passed_gates'])}")


if __name__ == "__main__":
    main()
