"""
多源热议股票分析引擎
- 跨源聚合 ticker 提及
- 综合评分（提及频次 × 来源多样性 × 热度加权）
- 输出排名列表（含公司名称）
"""

import logging
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Optional
from sources import Post
from company_names import batch_lookup

logger = logging.getLogger(__name__)


# 来源权重（各平台可信度 / 信号质量）
SOURCE_WEIGHTS = {
    "reddit": {
        "ValueInvesting": 2.0,
        "SecurityAnalysis": 2.0,
        "investing": 1.5,
        "stocks": 1.3,
        "StockMarket": 1.3,
        "wallstreetbets": 0.8,
        "pennystocks": 0.6,
        "smallstreetbets": 0.6,
        "thetagang": 0.9,
        "biotech_stocks": 1.2,
        "dividends": 1.5,
        "REITs": 1.3,
        "SPACs": 0.7,
        "default": 1.0,
    },
    "stocktwits": 0.9,
    "SeekingAlpha": 1.2,
    "InvestorPlace": 0.8,
    "NASDAQ": 0.9,
    "YahooFinance": 0.9,
    "GoogleNews": 0.6,
}


def get_source_weight(source: str, sub: str = "") -> float:
    """获取来源权重"""
    if source == "reddit":
        subs = SOURCE_WEIGHTS.get("reddit", {})
        return subs.get(sub, subs.get("default", 1.0))
    return SOURCE_WEIGHTS.get(source, 0.7)


def compute_ticker_scores(posts: list[Post]) -> list[dict]:
    """
    对每个 ticker 综合评分，返回排序后的列表
    评分维度：
      1. 提及次数（count）
      2. 来源多样性（不同平台 / 子版块数）
      3. 热度分（点赞 + 评论 的加权和）
      4. 时间衰减（近期提及加分）
    """
    now = datetime.now(timezone.utc)

    ticker_data = defaultdict(lambda: {
        "count": 0,
        "sources": set(),
        "source_subs": set(),
        "total_score": 0,
        "total_comments": 0,
        "timestamps": [],
        "avg_engagement": 0.0,
    })

    for post in posts:
        if not post.tickers_mentioned:
            continue
        weight = get_source_weight(post.source, post.source_sub)

        for ticker in set(post.tickers_mentioned):
            d = ticker_data[ticker]
            d["count"] += 1
            d["sources"].add(post.source)
            if post.source_sub:
                d["source_subs"].add(f"{post.source}/{post.source_sub}")
            d["total_score"] += int(post.score * weight)
            d["total_comments"] += post.comments
            if post.created_utc:
                d["timestamps"].append(post.created_utc)

    if not ticker_data:
        return []

    cutoff_24h = now.timestamp() - 86400

    scored = []
    for ticker, data in ticker_data.items():
        count = data["count"]
        source_diversity = len(data["sources"]) + len(data["source_subs"]) * 0.3
        hot_score = (data["total_score"] + data["total_comments"] * 2) / max(count, 1)

        recent_count = sum(
            1 for t in data["timestamps"]
            if t.timestamp() >= cutoff_24h
        )
        time_boost = 1.0 + (recent_count / max(count, 1)) * 0.5

        composite = (
            count * 1.0 +
            source_diversity * 1.5 +
            min(hot_score, 100) * 0.3 +
            time_boost * 2.0
        )

        scored.append({
            "ticker": ticker,
            "score": round(composite, 1),
            "mentions": count,
            "sources": sorted(data["sources"]),
            "source_subs": sorted(data["source_subs"])[:5],
            "hot_score": round(hot_score, 1),
            "recent_ratio": round(recent_count / max(count, 1), 2),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def attach_company_names(scores: list[dict], top_n: int = 30) -> list[dict]:
    """只给前 top_n 名查公司名称，避免查询数百个噪声 ticker"""
    for item in scores[top_n:]:
        item["company_name"] = ""
    tickers = [item["ticker"] for item in scores[:top_n]]
    names = batch_lookup(tickers)
    for item in scores[:top_n]:
        item["company_name"] = names.get(item["ticker"], "")
    return scores


def print_report(scores: list[dict], top_n: int = 30) -> str:
    """生成可读报告文本（含公司名称）"""
    if not scores:
        return "⚠️ 过去一周未发现明显的股票讨论信号。"

    lines = []
    lines.append("=" * 80)
    lines.append("📊  多源股票论坛热议榜（过去 7 天）")
    lines.append(f"📅  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"{'#':>3s}  {'Ticker':>6s}  {'公司名称':<28s}  {'综合分':>6s}  {'提及':>4s}  {'来源':>4s}")
    lines.append("-" * 80)

    for i, item in enumerate(scores[:top_n], 1):
        name = item.get("company_name", "") or ""
        if len(name) > 26:
            name = name[:25] + "…"
        src_str = ", ".join(sorted(set(item["sources"])))
        lines.append(
            f"{i:3d}  {item['ticker']:>6s}  {name:<28s}  "
            f"{item['score']:6.1f}  "
            f"{item['mentions']:4d}  "
            f"{len(item['sources']):4d}  "
            f"{src_str}"
        )

    lines.append("")
    lines.append("-" * 80)
    lines.append("📋 说明:")
    lines.append("  · 综合分 = 提及次数×1 + 来源多样性×1.5 + 热度分×0.3 + 近期活跃度×2")
    lines.append("  · 来源: Reddit(13子版块) + SeekingAlpha + YahooFinance + NASDAQ + InvestorPlace + GoogleNews")
    lines.append("  · 公司名称来自 yfinance，仅供参考")
    lines.append("")
    lines.append("⚠️ 注意: 本榜单仅反映论坛讨论热度，不构成投资建议")
    lines.append("=" * 80)

    return "\n".join(lines)


def print_short_list(scores: list[dict], top_n: int = 15) -> str:
    """生成简短潜力股列表（含公司名称）"""
    if not scores:
        return "⚠️ 过去一周未发现明显信号。"

    max_ticker_len = max(len(s["ticker"]) for s in scores[:top_n])
    max_name_len = max(len(s.get("company_name", "")) for s in scores[:top_n])
    max_name_len = min(max(max_name_len, 8), 28)

    lines = []
    lines.append("🔥  多源热议潜力股 TOP {}（过去 7 天）\n".format(top_n))
    header = f"  {'排名':>3s}  {'Ticker':>{max_ticker_len}s}  {'公司名称':<{max_name_len}s}  {'综合分':>6s}  {'来源数':>4s}  {'来源明细'}"
    lines.append(header)
    lines.append("-" * len(header))

    for i, item in enumerate(scores[:top_n], 1):
        name = item.get("company_name", "") or ""
        if len(name) > max_name_len:
            name = name[:max_name_len - 1] + "…"
        src_detail = ", ".join(item["source_subs"][:3])
        lines.append(
            f"  {i:3d}  {item['ticker']:>{max_ticker_len}s}  {name:<{max_name_len}s}  "
            f"{item['score']:6.1f}  "
            f"{len(item['sources']):4d}  "
            f"{src_detail}"
        )

    lines.append("")
    lines.append("⚠️ 仅反映论坛讨论热度，不构成投资建议")
    return "\n".join(lines)
