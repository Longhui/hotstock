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
import random
import re
import smtplib
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

# ── 代理配置（显式传参，不用环境变量——requests 对 env var 处理不一致） ──
_PROXY = "http://127.0.0.1:3067"
_PROXIES = {"http": _PROXY, "https": _PROXY}
logger.info(f"  🌐 代理 {_PROXY}")

# 全局超时（yfinance 连接有时较慢）
os.environ["YFINANCE_TIMEOUT"] = "60"
import socket
socket.setdefaulttimeout(60)

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
# ║  Step 1: Reddit RSS 抓取 (limit=100, 无需 API 凭证)        ║
# ╚══════════════════════════════════════════════════════════════╝

def fetch_reddit_rss(sub: str, max_posts: int = 100, max_age_days: int = 7) -> list:
    """从 Reddit RSS 获取帖子（limit=100，自动重试，无需 API 凭证）。"""
    import requests
    import feedparser

    limit = min(max_posts, 100)
    url = f"https://www.reddit.com/r/{sub}/.rss?limit={limit}"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    posts = []

    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=30, headers=HEADERS, proxies=_PROXIES)
            if resp.status_code != 200:
                logger.debug(f"  RSS r/{sub}: HTTP {resp.status_code} (attempt {attempt+1})")
                if attempt == 0:
                    time.sleep(3)
                    continue
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

                title = entry.get("title", "") or ""
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
            break  # 成功则跳出重试循环
        except Exception as e:
            logger.debug(f"RSS r/{sub} (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(3)
                continue
    return posts


def fetch_all_subs(max_posts_per_sub: int = 100, max_age_days: int = 7,
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
        time.sleep(1.0)  # 限流礼貌
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
    """对单个 ticker 执行投资 Checklist 分析（Futu 优先，yfinance 兜底）。"""
    mod = _load_checklist()

    # ── 优先尝试 Futu API（更快、不限流） ──
    futu_data = None
    futu_financials = None
    try:
        _tools_dir = os.path.join(BASE_DIR, "tools")
        if _tools_dir not in sys.path:
            sys.path.insert(0, _tools_dir)
        import futu_data as _fd
        fp = _fd.FutuDataProvider()
        if fp.health_check():
            futu_data = fp.get_all_data(ticker)
            if futu_data:
                logger.debug(f"  ✅ Futu: {ticker} 数据获取成功")
                # 尝试获取财报指标（gross_margin/FCF 等，短超时）
                futu_financials = fp.get_financials(ticker)
                if futu_financials:
                    futu_data.update(futu_financials)
                    logger.debug(f"  ✅ Futu financials: {ticker}")
    except Exception as e:
        logger.debug(f"  Futu 不可用: {e}")

    # ── yfinance 调用（带缓存 + 重试） ──
    max_retries = 2 if futu_data else 3
    company = None

    for attempt in range(1, max_retries + 1):
        try:
            company = mod.identify_company(ticker)
            if company.get("error"):
                logger.warning(f"  ⚠️ {ticker}: {company['error']}")
                # 有 Futu 数据但 yfinance 识别失败时，构造最小 company 对象
                if futu_data and not company.get("is_listed", True):
                    company = {
                        "ticker": ticker,
                        "name": futu_data.get("company_name", ticker),
                        "error": None,
                        "is_listed": True,
                        "raw_info": {},
                    }
                else:
                    return None
            break
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt < max_retries:
                wait = attempt * 5
                logger.debug(f"  ⏳ {ticker} 第{attempt}次失败，{wait}s后重试...")
                time.sleep(wait)
            else:
                logger.warning(f"  ⚠️ {ticker}: 重试{max_retries}次仍失败 — {e}")
    if company is None:
        logger.warning(f"  ⚠️ {ticker}: 连接失败，跳过")
        return None

    try:
        # 数据收集：传入 Futu 数据，减少 yfinance 依赖
        data = None
        news = []
        for attempt in range(1, max_retries + 1):
            try:
                data = mod.collect_financial_data(company, futu_data=futu_data)
                news = mod.collect_news(ticker)
                break
            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt < max_retries:
                    wait = attempt * 5
                    logger.debug(f"  ⏳ {ticker} 数据收集第{attempt}次失败，{wait}s后重试...")
                    time.sleep(wait)
                else:
                    logger.warning(f"  ⚠️ {ticker}: 数据收集重试{max_retries}次仍失败 — {e}")
        if data is None:
            return None

        # 标记数据来源（用于报告显示）
        data["data_source"] = "Futu + yfinance" if futu_data else "yfinance"

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

        # ── 安全边际降级规则 ──
        # 生意再好，价格太贵也不应通过（ROI 的关键来源是买入价格）
        if g5 < 3 and passed >= 4:
            passed -= 1  # 通过 → 灰色
        elif g5 < 2 and passed >= 3:
            passed -= 1  # 灰色 → 不通过

        # ── 双重边缘降级规则 ──
        # 当安全边际和纪律同时处于边缘时，说明"贵 + 追涨"，应降级
        if g5 <= 3 and g6 <= 3 and passed >= 4:
            passed -= 1  # 通过 → 灰色
        elif g5 <= 3 and g6 <= 2 and passed >= 3:
            passed -= 1  # 灰色 → 不通过
        # 软降级：好公司 + 贵价 + 有追涨信号 → 强制灰色
        if g5 <= 3 and g6 <= 4 and passed >= 4 and g1 >= 4 and g3 >= 4:
            passed = max(passed - 2, 2)

        # ── 否决清单 ──
        if vetoes:
            passed = 0  # 触发否决 → 直接不通过

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
# ║  MD → PDF 转换 (markdown + Edge headless)                  ║
# ╚══════════════════════════════════════════════════════════════╝

def md_to_pdf(md_path: str) -> Optional[str]:
    """将 Markdown 报告转为 PDF（用于邮件附件）。"""
    try:
        import markdown as md_lib
    except ImportError:
        logger.warning("  ⚠️ markdown 库未安装，无法生成 PDF")
        return None

    # Edge 可执行文件路径
    EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

    if not os.path.exists(EDGE_PATH):
        logger.warning("  ⚠️ 未找到 Edge 浏览器，无法生成 PDF")
        return None

    pdf_path = md_path.replace(".md", ".pdf")

    try:
        # 1. 读取 MD
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()

        # 2. MD → HTML
        html_body = md_lib.markdown(md_content, extensions=["tables", "fenced_code"])

        # 3. 包裹打印样式 HTML
        html_full = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @page {{ margin: 2cm; }}
  body {{
    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
    font-size: 12pt;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
  }}
  h1 {{ font-size: 20pt; color: #1a1a2e; border-bottom: 2px solid #1a1a2e; padding-bottom: 6px; }}
  h2 {{ font-size: 16pt; color: #16213e; margin-top: 24px; }}
  h3 {{ font-size: 13pt; color: #0f3460; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 11pt;
  }}
  th, td {{
    border: 1px solid #ccc;
    padding: 6px 10px;
    text-align: left;
  }}
  th {{ background: #1a1a2e; color: #fff; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 10pt; }}
  pre {{ background: #f5f5f5; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{
    border-left: 4px solid #1a1a2e;
    margin: 12px 0;
    padding: 8px 16px;
    background: #f9f9f9;
    color: #555;
  }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
  .star {{ color: #f5a623; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

        # 4. 临时 HTML 文件
        html_path = md_path.replace(".md", "_temp.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_full)

        # 5. Edge headless → PDF
        import subprocess
        result = subprocess.run(
            [
                EDGE_PATH,
                "--headless",
                f"--print-to-pdf={os.path.abspath(pdf_path)}",
                "--print-to-pdf-no-header",
                os.path.abspath(html_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"  ⚠️ Edge PDF 转换返回非零: {result.stderr[:200]}")
        os.remove(html_path)

        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            logger.info(f"  📄 PDF 已生成: {os.path.basename(pdf_path)}")
            return pdf_path
        else:
            logger.warning("  ⚠️ PDF 文件异常（为空或过小）")
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            return None

    except Exception as e:
        logger.warning(f"  ⚠️ PDF 生成失败: {e}")
        # 清理临时文件
        html_path = md_path.replace(".md", "_temp.html")
        if os.path.exists(html_path):
            os.remove(html_path)
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  邮件发送                                                   ║
# ╚══════════════════════════════════════════════════════════════╝

SMTP_CONFIG = {
    "server": "smtp.163.com",
    "port": 465,
    "user": "mystock666@163.com",
    "password": "RJdvWhm7c9reVeCT",
    "recipient": "mystock666@163.com",
}


def send_email(report_path: Optional[str], results: List[Dict[str, Any]], top_n: int) -> bool:
    """将分析结果通过邮件发送：正文为总结，附件为完整报告。"""
    passed_count = sum(1 for r in results if r["passed_gates"] >= 4)
    gray_count = sum(1 for r in results if 3 <= r["passed_gates"] < 4)
    fail_count = sum(1 for r in results if r["passed_gates"] < 3)

    # ── 邮件正文：总结摘要 ──
    body_lines = [
        f"Reddit 热门股票 · 巴菲特 Checklist 分析报告",
        f"分析日期: {CURRENT_DATE}",
        f"分析覆盖: Top {min(top_n, len(results))} 热门股（成功分析 {len(results)} 只）",
        "",
        "━━━ 分析概况 ━━━",
        f"  ✅ 通过（>=4关）: {passed_count} 只",
        f"  ❓ 灰色地带（3关）: {gray_count} 只",
        f"  ❌ 未通过（<3关）: {fail_count} 只",
        "",
        "━━━ 评分排名 ━━━",
    ]

    # 排名表头
    max_ticker = max(len(r["ticker"]) for r in results)
    header = f"  {'#':>3s}  {'Ticker':>{max_ticker}s}  公司 {'总分':>4s}  结论"
    body_lines.append(header)
    body_lines.append("  " + "-" * (len(header) - 2))

    for i, r in enumerate(results, 1):
        name = r["company_name"]
        name_display = name[:18] + "…" if len(name) > 18 else name
        body_lines.append(
            f"  {i:3d}  {r['ticker']:>{max_ticker}s}  {name_display:<18s}  "
            f"{r['total']:>3d}/25  {conclusion_text(r['passed_gates'])}"
        )
    body_lines.append("")

    # 分组列出
    if passed_count:
        body_lines.append("✅ 通过 Checklist 的公司：")
        for r in results:
            if r["passed_gates"] >= 4:
                body_lines.append(f"  · {r['company_name']}（{r['ticker']}）— {r['total']}/25")
        body_lines.append("")

    if gray_count:
        body_lines.append("❓ 灰色地带（需自行判断）：")
        for r in results:
            if 3 <= r["passed_gates"] < 4:
                body_lines.append(f"  · {r['company_name']}（{r['ticker']}）— {r['total']}/25")
        body_lines.append("")

    if fail_count:
        body_lines.append("❌ 未通过 Checklist：")
        for r in results:
            if r["passed_gates"] < 3:
                body_lines.append(f"  · {r['company_name']}（{r['ticker']}）— {r['total']}/25")
        body_lines.append("")

    body_lines.append("详细分析报告（PDF）已附在附件中。")
    body_lines.append("")
    body_lines.append("⚠️ 免责声明：本报告为自动化分析工具，不构成投资建议。")
    body_text = "\n".join(body_lines)

    # ── 构造邮件 ──
    msg = MIMEMultipart()
    msg["From"] = SMTP_CONFIG["user"]
    msg["To"] = SMTP_CONFIG["recipient"]
    msg["Subject"] = f"Reddit 热门股 · 巴菲特 Checklist 分析报告 ({CURRENT_DATE})"
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    # 附上报告文件（优先转 PDF）
    attachment_path = None
    if report_path and os.path.exists(report_path):
        # 尝试转为 PDF
        pdf_path = md_to_pdf(report_path)
        if pdf_path:
            attachment_path = pdf_path
        else:
            attachment_path = report_path

        with open(attachment_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
        encoders.encode_base64(attachment)

        ext = os.path.splitext(attachment_path)[1]
        mime_type = "application/pdf" if ext == ".pdf" else "text/markdown"
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=("utf-8", "", os.path.basename(attachment_path)),
        )
        msg.attach(attachment)

    # ── 发送 ──
    try:
        with smtplib.SMTP_SSL(SMTP_CONFIG["server"], SMTP_CONFIG["port"], timeout=30) as server:
            server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
            server.send_message(msg)
        logger.info(f"📧 邮件已发送至 {SMTP_CONFIG['recipient']}")
        return True
    except Exception as e:
        logger.error(f"❌ 邮件发送失败: {e}")
        return False


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
    parser.add_argument("--top", type=int, default=15, help="分析前 N 只热门股 (默认 15)")
    parser.add_argument("--min-score", type=float, default=0,
                        help="最低综合分门槛 (默认 0 = 不限)")
    parser.add_argument("--max-posts", type=int, default=100,
                        help="每子版块最多抓取帖子数 (默认 100，RSS limit=100 上限)")
    parser.add_argument("--no-save", action="store_true",
                        help="不保存报告到文件")
    parser.add_argument("--output", "-o",
                        help="输出报告到指定文件路径")
    parser.add_argument("--subs",
                        help="指定子版块，逗号分隔 (默认全部)")
    parser.add_argument("--debug", action="store_true",
                        help="调试日志")
    parser.add_argument("--email", action="store_true", default=True,
                        help="发送邮件报告（默认开启）")
    parser.add_argument("--no-email", action="store_false", dest="email",
                        help="不发送邮件报告")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    sub_list = [s.strip() for s in args.subs.split(",")] if args.subs else None

    # 优先使用价值投资类子版块（信号质量更高）
    if sub_list is None:
        sub_list = [
            "ValueInvesting", "SecurityAnalysis",
            "investing",
        ]
        logger.info(f"  使用价值投资子版块: {', '.join(sub_list)}")

    # ── Ticker 验证函数 ──
    def is_valid_ticker(t: str) -> bool:
        """过滤掉明显不是股票代码的文本。"""
        # 长度：NYSE/NASDAQ ticker 通常 1-5 个大写字母
        if not (1 <= len(t) <= 5):
            return False
        # 必须全部是大写字母
        if not re.fullmatch(r'[A-Z][A-Z0-9]*', t):
            return False
        # 全是数字
        if t.isdigit():
            return False
        # 常见 Reddit 用语 / 非股票缩写
        if t in _NON_STOCK:
            return False
        # 单字母 A 到 Z（极少是真实股票，更可能是语境误提）
        if len(t) == 1:
            return False
        # 常见英文单词（Reddit 正文中容易误提取）
        if t in _COMMON_WORDS:
            return False
        return True

    # 非股票缩写（Reddit 高频用语 / 财经缩写 / 通用术语）
    _NON_STOCK = {
        "FY", "GMT", "AWS", "LTA", "DS", "DR", "CEO", "CFO", "COO", "CTO",
        "ATH", "YTD", "IPO", "ETF", "REIT", "YOY", "Q1", "Q2", "Q3", "Q4",
        "FOMO", "HODL", "WSB", "BTFD", "IMO", "TLDR", "FYI", "AMA", "ELI5",
        "TIL", "PSA", "DAE", "OP", "EDIT", "LOL", "ROFL", "SMH", "BTH",
        "OTC", "SEC", "IRS", "GDP", "CPI", "PPI", "EPS", "DIV", "PEG",
        "ROI", "ROA", "EBITDA", "PE", "PB", "PS", "EV", "TTM", "MRQ",
        "UK", "EU", "USA", "NYC", "LAX", "SFO", "ATL",
        "IMO", "ICYDK", "AFAIK", "IIRC", "YMMV",
    }

    # 常见英文单词（可能被 extract_tickers 误认为股票代码）
    _COMMON_WORDS = {
        "BIG", "HOT", "NEW", "TOP", "BEST", "FREE", "GOOD", "HIGH", "LOW",
        "BUY", "SELL", "HOLD", "CALL", "PUT", "SAFE", "RISK", "DEAL",
        "DUE", "UP", "DOWN", "YES", "NO", "OUT", "ALL", "ANY", "EACH",
        "FACT", "IDEA", "LIST", "NOTE", "ONCE", "ONLY", "OPEN", "PLAN",
        "PASS", "STOP", "THEN", "TRUE", "VERY", "WEEK", "WORK", "YEAR",
        "TECH", "SOFT", "BANK", "GOLD", "OIL", "GAS", "WIND", "SOLAR",
        "BOND", "DEBT", "CASH", "TAX", "BILL", "COIN", "CORE", "DATA",
        "EDGE", "FAST", "FLOW", "FUND", "GAIN", "GROWTH", "LEAD", "LIFE",
        "MASS", "MORE", "MOVE", "NEED", "NEXT", "PAID", "PAIR", "PICK",
        "POST", "RATE", "REAL", "RISE", "SAVE", "SHIP", "SHOP", "SIDE",
        "SIGN", "SITE", "SIZE", "SOLD", "SPOT", "STAR", "STEP", "TALK",
        "TEAM", "TERM", "TURN", "UNDER", "WALK", "WALL", "WANT", "WARN",
        "WAVE", "WIDE", "WILL", "WISH", "YOUR", "TL", "RH",
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

    # 过滤：排除已知非个股（ETF、指数等），使用增强验证
    filtered = [s for s in all_scores
                if s["score"] >= args.min_score
                and is_valid_ticker(s["ticker"])]
    top_scores = filtered[:args.top]
    logger.info(f"  排名前 {len(top_scores)}: {', '.join(s['ticker'] for s in top_scores)}")

    # 查公司名称
    tickers_to_query = [s["ticker"] for s in top_scores]
    logger.info(f"🔍 查询公司名称...")
    names = batch_lookup(tickers_to_query)

    # ── Step 3: 巴菲特 Checklist 分析（过滤 ETF/基金） ──
    target_tickers = []
    filtered_out = []
    for s in top_scores:
        name = names.get(s["ticker"], "")
        # 排除 ETF、基金、债券等非个股
        if any(kw in name.upper() for kw in ("ETF", "FUND", "BOND", "INDEX", "TRUST", "PORTFOLIO")):
            filtered_out.append(f"{s['ticker']}（{name}）")
            continue
        target_tickers.append(s["ticker"])
    if filtered_out:
        logger.info(f"  排除 ETF/基金: {', '.join(filtered_out)}")
    print(f"\n📋 Step 3/4: 执行巴菲特 Checklist 分析（{len(target_tickers)} 只）...")

    results: List[Dict[str, Any]] = []
    for idx, ticker in enumerate(target_tickers):
        # 每个 ticker 之间延时 5~10 秒，避免触发 Yahoo Finance 限流
        if idx > 0:
            delay = random.uniform(5, 10)
            logger.debug(f"  等待 {delay:.1f}s 避免限流...")
            time.sleep(delay)

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
    report_path = None
    if not args.no_save:
        output_dir = os.path.join(BASE_DIR, "output")
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = args.output or os.path.join(output_dir, f"巴菲特Checklist-Reddit综合报告_{timestamp}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  💾 报告已保存: {report_path}")
    elif args.output:
        report_path = args.output
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  📄 报告已输出: {report_path}")

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

    # ── 邮件发送 ──
    if args.email:
        print(f"\n📧 正在发送邮件报告...")
        send_email(report_path, results, args.top)


if __name__ == "__main__":
    main()
