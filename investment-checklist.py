#!/usr/bin/env python3
"""
巴菲特价值投资买入前 Checklist —— 独立可执行脚本
===================================================
基于巴菲特/芒格价值投资框架的系统化买入前检查清单。

来源: ~/.claude/commands/investment-checklist.md
数据源: Yahoo Finance (yfinance)

Usage:
    python3 investment-checklist.py AAPL
    python3 investment-checklist.py 0700.HK
    python3 investment-checklist.py =TSLA
    python3 investment-checklist.py AAPL MSFT GOOGL    # 多公司
    python3 investment-checklist.py --output report.md AAPL

Dependencies:
    pip install yfinance pandas numpy
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import yfinance as yf
    import numpy as np
except ImportError as e:
    print(f"❌ 缺少依赖: {e}")
    print("请安装所需包:  pip install yfinance pandas numpy")
    sys.exit(1)

# ── yfinance 缓存（防限流） ──
_YF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_cache")
os.makedirs(_YF_CACHE_DIR, exist_ok=True)
_YF_CACHE_TTL = 6 * 3600  # 6 小时


def _yf_get_info(ticker: str) -> dict:
    """带文件缓存的 yfinance info 获取，缓存命中直接返回 dict。"""
    import json
    cache_key = ticker.strip().upper()
    cache_path = os.path.join(_YF_CACHE_DIR, f"{cache_key}.json")

    # 缓存命中且未过期 → 直接返回缓存数据
    if os.path.exists(cache_path):
        try:
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < _YF_CACHE_TTL:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and cached.get("currentPrice"):
                    return cached
        except Exception:
            pass

    # 缓存未命中 → 正常请求
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        if info and info.get("currentPrice"):
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, default=str)
        return info
    except Exception as e:
        # 请求失败但有旧缓存 → 用旧缓存兜底
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        raise e

# ── 导入 financial_rigor （精确计算引擎） ──
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
from tools.financial_rigor import verify_valuation, three_scenario_valuation, fmt_number

CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")
REPORT_DIR = os.path.expanduser("~/巴菲特Checklist")


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 1: 解析输入 — 识别公司                               ║
# ╚══════════════════════════════════════════════════════════════╝

def identify_company(raw: str) -> Dict[str, Any]:
    """通过股票代码获取公司基础信息（带缓存）。"""
    ticker = raw.strip().upper()
    try:
        info = _yf_get_info(ticker)
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is None and not info.get("longName"):
            return {"ticker": ticker, "error": f"无法获取 {ticker} 的数据，请检查代码是否正确"}

        return {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "exchange": info.get("exchange", "N/A"),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "country": info.get("country", "N/A"),
            "website": info.get("website", ""),
            "employees": info.get("fullTimeEmployees", "N/A"),
            "description": (info.get("longBusinessSummary") or "")[:300],
            "is_listed": True,
            "raw_info": info,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "is_listed": False}


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 1.5: AI 研究偏见预警                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def grade_information_availability(company: Dict[str, Any]) -> Tuple[str, str]:
    """根据数据丰富度评级 A / B / C。"""
    info = company.get("raw_info", {})
    checks = [
        info.get("currentPrice") or info.get("regularMarketPrice"),
        info.get("totalRevenue"),
        info.get("trailingEps"),
        info.get("fiftyTwoWeekHigh"),
        info.get("returnOnEquity"),
    ]
    score = sum(1 for c in checks if c is not None)
    mcap = info.get("marketCap", 0)

    if score >= 4 and mcap > 1e9:
        return "A", '上市多年、数据充裕 — 正常执行，但警惕"共识陷阱"——所有指标看起来都清晰不代表真的确定'
    elif score >= 2:
        return "B", '数据有限需推算 — 每个推算指标标注置信度，"好生意"判断加权考虑数据可靠性'
    return "C", '信息极度稀缺 — 不勉强填满六关表格，诚实标注"数据不足无法判断"，聚焦可验证的核心问题'


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 2: 数据收集                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def collect_financial_data(company: Dict[str, Any],
                           futu_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """收集全面的财务数据。

    Args:
        company: identify_company() 返回的公司信息（含 yfinance raw_info）
        futu_data: 可选，FutuDataProvider 获取的实时行情数据（更准更快）
    """
    info = company.get("raw_info", {})
    d: Dict[str, Any] = {}

    # ── Price & Valuation ──
    # 优先用 Futu 实时行情（更准），yfinance 兜底
    if futu_data:
        d["price"] = futu_data.get("price") or info.get("currentPrice") or info.get("regularMarketPrice", 0)
        d["market_cap"] = futu_data.get("market_cap") or info.get("marketCap", 0)
        d["pe_ttm"] = futu_data.get("pe_ttm") or info.get("trailingPE")
        d["pb"] = futu_data.get("pb") or info.get("priceToBook")
        d["dividend_yield_pct"] = futu_data.get("dividend_yield_pct")
        d["eps_ttm"] = futu_data.get("eps_ttm") or info.get("trailingEps")
        d["bvps"] = futu_data.get("bvps") or info.get("bookValue")
    else:
        d["price"] = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        d["market_cap"] = info.get("marketCap", 0)
        d["pe_ttm"] = info.get("trailingPE")
        d["pb"] = info.get("priceToBook")
        div_yield = info.get("dividendYield")
        d["dividend_yield_pct"] = round(div_yield * 100, 2) if div_yield else None
        d["eps_ttm"] = info.get("trailingEps")
        d["bvps"] = info.get("bookValue")

    d["forward_pe"] = info.get("forwardPE")
    fcf_yield = info.get("freeCashflowYield")
    d["fcf_yield_pct"] = round(fcf_yield, 1) if fcf_yield else None
    d["ps"] = info.get("priceToSalesTrailing12Months")

    # ── Profitability ──
    # 这些字段 yfinance 有，Futu 需要从财报算（暂时以 yfinance 为准）
    d["gross_margin_pct"] = round(info.get("grossMargins", 0) * 100, 1) if info.get("grossMargins") else None
    d["net_margin_pct"] = round(info.get("profitMargins", 0) * 100, 1) if info.get("profitMargins") else None
    roe_yf = info.get("returnOnEquity")
    roe_ft = futu_data.get("roe_pct") if futu_data else None
    d["roe_pct"] = roe_ft or (round(roe_yf * 100, 1) if roe_yf else None)
    d["roa_pct"] = round(info.get("returnOnAssets", 0) * 100, 1) if info.get("returnOnAssets") else None

    # ── Growth ──
    d["revenue_growth_pct"] = round(info.get("revenueGrowth", 0) * 100, 1) if info.get("revenueGrowth") else None
    d["earnings_growth_pct"] = round(info.get("earningsGrowth", 0) * 100, 1) if info.get("earningsGrowth") else None

    # ── Financial Health ──
    d["total_debt"] = info.get("totalDebt")
    d["total_cash"] = info.get("totalCash")
    d["operating_cf"] = info.get("operatingCashFlow")
    d["free_cf"] = info.get("freeCashflow")
    d["debt_to_equity"] = info.get("debtToEquity")
    if d["total_cash"] is not None and d["total_debt"] is not None:
        d["net_cash"] = d["total_cash"] - d["total_debt"]
    else:
        d["net_cash"] = None

    # ── Per Share ──
    d["eps_forward"] = info.get("forwardEps")
    d["fcf_per_share"] = info.get("freeCashflowPerShare")

    # ── Ownership ──
    ins = info.get("heldPercentInsiders")
    d["insider_pct"] = round(ins * 100, 1) if ins else None
    inst = info.get("heldPercentInstitutions")
    d["institution_pct"] = round(inst * 100, 1) if inst else None

    # ── Management ──
    d["ceo"] = futu_data.get("ceo") if futu_data else None
    if not d["ceo"]:
        d["ceo"] = info.get("companyOfficers", [{}])[0].get("name") if info.get("companyOfficers") else None

    # ── Historical Price ──
    # 优先用 Futu K 线（实时），yfinance 兜底
    if futu_data and futu_data.get("price_history"):
        d["price_history"] = futu_data["price_history"]
        if len(futu_data["price_history"]) > 1:
            first = futu_data["price_history"][0]["close"]
            last = futu_data["price_history"][-1]["close"]
            d["5y_return"] = round((last / first - 1) * 100, 1) if first > 0 else None
    else:
        try:
            hist = yf.Ticker(company["ticker"]).history(period="5y")
            if not hist.empty:
                d["price_history"] = hist
                d["5y_return"] = round((hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100, 1)
        except Exception:
            pass

    # ── Sector / Industry ──
    d["sector"] = info.get("sector", "")
    d["industry"] = info.get("industry", "")

    # ── 52周高/低（优先 Futu 实时数据，yfinance 兜底） ──
    if futu_data:
        d["52w_high"] = futu_data.get("52w_high")
        d["52w_low"] = futu_data.get("52w_low")
        d["net_profit"] = futu_data.get("net_profit")
        d["net_asset"] = futu_data.get("net_asset")
    else:
        d["52w_high"] = info.get("fiftyTwoWeekHigh")
        d["52w_low"] = info.get("fiftyTwoWeekLow")
        d["net_profit"] = info.get("netIncomeToCommon")
        d["net_asset"] = info.get("bookValue") * (info.get("sharesOutstanding", 0) or 1) if info.get("bookValue") else None

    return d


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 2b: 近6个月重大事件（来自 yfinance 新闻）             ║
# ╚══════════════════════════════════════════════════════════════╝

def collect_news(ticker: str, max_items: int = 8) -> List[Dict[str, str]]:
    """从 yfinance 获取近6个月的重要新闻事件。"""
    try:
        stock = yf.Ticker(ticker)
        raw = stock.news or []
    except Exception:
        return []

    now = datetime.now(timezone.utc)
    cutoff = 180  # 6个月
    items: List[Dict[str, str]] = []

    for article in raw[:max_items * 2]:  # 多取一些再过滤
        if len(items) >= max_items:
            break
        try:
            content = article.get("content", {})
            pub_ts = content.get("pubDate", "")
            if pub_ts:
                pub = datetime.fromisoformat(pub_ts.replace("Z", "+00:00"))
                delta = (now - pub).days
                if delta > cutoff:
                    continue
            else:
                pub_ts = ""
                delta = -1

            items.append({
                "title": content.get("title", ""),
                "summary": (content.get("summary") or "")[:200],
                "date": pub_ts[:10] if pub_ts else "",
                "source": content.get("provider", {}).get("displayName", ""),
                "url": content.get("clickThroughUrl", {}).get("url", ""),
            })
        except Exception:
            continue

    return items


# ╔══════════════════════════════════════════════════════════════╗
# ║  Step 3: 六关评分                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def star(n: int) -> str:
    """返回星级符号，如 ★★★★☆"""
    return "★" * n + "☆" * (5 - n)


# ─── 第一关：能力圈 ─────────────────────────────────

def gate1_circle_of_competence(data: Dict[str, Any], company: Dict[str, Any]) -> Tuple[int, str]:
    """评分：商业模式的可理解性与10年确定性。"""
    sector = (data.get("sector") or "").lower()
    industry = (data.get("industry") or "").lower()

    simple_keywords = [
        "consumer", "beverage", "food", "retail", "bank", "insurance",
        "utility", "real estate", "transportation", "manufacturing",
        "alcoholic", "tobacco", "household", "personal care",
    ]
    complex_keywords = [
        "semiconductor", "biotechnology", "pharmaceutical", "quantum",
        "artificial intelligence", "cloud computing", "blockchain",
        "cryptocurrency", "fintech", "internet content",
    ]

    is_simple = any(k in sector or k in industry for k in simple_keywords)
    is_complex = any(k in sector or k in industry for k in complex_keywords)

    score = 3
    reasons = []

    if is_simple and not is_complex:
        score = 5
        reasons.append("商业模式简单清晰（行业特征）")
    elif is_simple and is_complex:
        score = 3
        reasons.append("业务有简单部分但也有技术门槛")
    elif is_complex:
        score = 2
        reasons.append(f"行业({industry})变化快或技术门槛高，难以预判未来")
    else:
        score = 3
        reasons.append("模式基本可理解")

    # 长期历史 → 确定性加分
    hist = data.get("price_history")
    if hist is not None and len(hist) >= 2000:
        score += 0.5
        reasons.append("超过8年公开交易历史")

    # 收入稳定 → 可预测
    rg = data.get("revenue_growth_pct")
    if rg is not None and abs(rg) < 5:
        score += 0.5
        reasons.append("收入稳定可预测")
    elif rg is not None and rg > 30:
        score -= 0.5
        reasons.append("高速增长期，未来不确定性高")

    final = max(1, min(5, round(score)))
    return final, "，".join(reasons)


# ─── 第二关：好生意 ─────────────────────────────────

def gate2_good_business(data: Dict[str, Any]) -> Tuple[int, Dict[str, Tuple[bool, str]]]:
    """评分：ROE、毛利率、FCF、资本效率、负债水平。

    注：对银行/保险等金融行业放宽毛利率等指标要求，
    更侧重 ROE 持续性和资本效率。
    """
    details: Dict[str, Tuple[bool, str]] = {}
    sector = (data.get("sector") or "").lower()
    industry = (data.get("industry") or "").lower()
    is_financial = any(kw in sector + industry for kw in ["bank", "financial", "insurance", "diversified financial", "money center"])

    # 1. ROE（核心指标，贯穿所有行业）
    roe = data.get("roe_pct")
    if roe is not None:
        if roe > 20:
            details["roe"] = (True, f"{roe:.1f}% > 20%，卓越")
        elif roe > 12:
            details["roe"] = (True, f"{roe:.1f}% > 12%，优秀" + ("（金融行业标准）" if is_financial else ""))
        elif roe > 8:
            details["roe"] = (is_financial, f"{roe:.1f}%，金融业可接受" if is_financial else f"{roe:.1f}%，一般")
        else:
            details["roe"] = (False, f"{roe:.1f}%，偏低")
    else:
        details["roe"] = (False, "数据不足")

    # 2. 毛利率（金融行业不适用，豁免）
    gm = data.get("gross_margin_pct")
    if gm is not None:
        if is_financial:
            details["gross_margin"] = (True, f"{gm:.1f}%（金融业参考）")
        elif gm > 60:
            details["gross_margin"] = (True, f"{gm:.1f}% > 60%，极强定价权")
        elif gm > 40:
            details["gross_margin"] = (True, f"{gm:.1f}% > 40%，有定价权")
        elif gm > 20:
            details["gross_margin"] = (False, f"{gm:.1f}%，一般")
        else:
            details["gross_margin"] = (False, f"{gm:.1f}%，偏低")
    elif is_financial:
        details["gross_margin"] = (True, "金融行业，毛利率不适用")
    else:
        details["gross_margin"] = (False, "数据不足")

    # 3. 自由现金流/分红（金融以分红能力代理）
    fcf = data.get("free_cf")
    ocf = data.get("operating_cf")
    div_yield = data.get("dividend_yield_pct")
    if fcf is not None and ocf is not None and ocf != 0:
        ratio = fcf / ocf
        if fcf > 0 and ratio > 0.7:
            details["fcf"] = (True, f"FCF 为正（{ratio:.0%} of OCF），强健")
        elif fcf > 0:
            details["fcf"] = (True, f"FCF 为正，良好")
        else:
            details["fcf"] = (False, f"FCF 为负 ({fcf:,.0f})，警示")
    elif is_financial and div_yield and div_yield > 1:
        details["fcf"] = (True, f"股息率 {div_yield:.2f}%，金融业以分红验证现金流")
    else:
        details["fcf"] = (False, "数据不足")

    # 4. 资本效率（金融用 ROA，但标准不同）
    roa = data.get("roa_pct")
    if roa is not None:
        if is_financial:
            if roa > 1.5:
                details["capital_intensity"] = (True, f"ROA {roa:.2f}%，金融业优秀")
            elif roa > 0.8:
                details["capital_intensity"] = (True, f"ROA {roa:.2f}%，金融业稳健")
            elif roa > 0.3:
                details["capital_intensity"] = (True, f"ROA {roa:.2f}%，金融业可接受")
            else:
                details["capital_intensity"] = (False, f"ROA {roa:.2f}%，金融业偏低")
        else:
            if roa > 10:
                details["capital_intensity"] = (True, f"ROA {roa:.1f}%，轻资产模式")
            elif roa > 5:
                details["capital_intensity"] = (True, f"ROA {roa:.1f}%，适中")
            else:
                details["capital_intensity"] = (False, f"ROA {roa:.1f}%，重资产模式")
    else:
        if is_financial:
            details["capital_intensity"] = (True, "金融业，资本效率不适用")
        else:
            details["capital_intensity"] = (False, "数据不足")

    # 5. 负债水平（金融行业杠杆天生高，豁免）
    dte = data.get("debt_to_equity")
    net_cash = data.get("net_cash")
    if dte is not None:
        if is_financial:
            details["debt"] = (True, f"D/E {dte:.0f}%（金融行业常态）")
        elif dte < 30:
            details["debt"] = (True, f"D/E {dte:.0f}%，负债很低")
        elif dte < 60:
            details["debt"] = (True, f"D/E {dte:.0f}%，负债可控")
        elif dte < 100:
            details["debt"] = (False, f"D/E {dte:.0f}%，偏高")
        else:
            details["debt"] = (False, f"D/E {dte:.0f}%，过高")
    elif net_cash is not None and net_cash > 0:
        details["debt"] = (True, f"净现金状态 ({net_cash:,.0f})，财务极健康")
    else:
        details["debt"] = (False, "数据不足")

    passed = sum(1 for v in details.values() if v[0])
    return max(1, min(5, passed)), details


# ─── 第三关：护城河 ─────────────────────────────────

def gate3_moat(data: Dict[str, Any], company: Dict[str, Any]) -> Tuple[int, Dict[str, Tuple[str, str]]]:
    """评分：品牌、转换成本、网络效应、规模、技术壁垒。"""
    info = company.get("raw_info", {})
    industry = (data.get("industry") or "").lower()
    sector = (data.get("sector") or "").lower()
    country = (company.get("country") or "").lower()
    details: Dict[str, Tuple[str, str]] = {}

    is_financial = any(kw in sector + industry for kw in ["bank", "financial", "insurance", "diversified financial", "money center"])
    # 双寡头/寡头垄断行业（通常受监管，进入壁垒极高）
    oligopoly = ["bank", "telecom", "utility", "railroad", "credit card", "exchange"]

    # 1. 品牌 / 定价权
    gm = data.get("gross_margin_pct")
    if gm is not None:
        if is_financial:
            details["brand"] = ("具备", f"金融业，品牌信任为护城河（数据仅供参考）")
        elif gm > 50:
            details["brand"] = ("具备", f"毛利率 {gm:.1f}%，强定价权")
        elif gm > 30:
            details["brand"] = ("部分", f"毛利率 {gm:.1f}%，有一定定价能力")
        else:
            details["brand"] = ("较弱", f"毛利率 {gm:.1f}%，定价权不足")
    else:
        details["brand"] = ("待查", "数据不足")

    # 2. 转换成本（金融行业极高：账户迁移成本 + 信用记录绑定性）
    if is_financial:
        details["switching_cost"] = ("具备", "金融业账户迁移成本极高，客户粘性强")
    elif any(w in industry for w in ["software", "cloud", "enterprise", "database", "erp"]):
        details["switching_cost"] = ("具备", f"行业特征：{industry}，客户转换成本较高")
    elif any(w in industry for w in ["bank", "financial"]):
        details["switching_cost"] = ("可能具备", f"行业特征：{industry}，转换成本较高")
    else:
        details["switching_cost"] = ("一般", f"行业特征：{industry}，转换成本不确定")

    # 3. 寡头 / 监管壁垒（最宽的护城河之一）
    if is_financial:
        # 加拿大五大行是经典寡头案例
        if country == "canada":
            details["oligopoly"] = ("极强", "加拿大五大行寡头垄断，外资进入受限，护城河极宽")
        else:
            details["oligopoly"] = ("可能具备", "金融业受严格监管，新进入者壁垒高")
    elif any(w in industry for w in oligopoly):
        details["oligopoly"] = ("可能具备", f"行业特征：{industry}，寡头格局")
    else:
        details["oligopoly"] = ("不明显", "非寡头垄断行业")

    # 4. 规模 / 成本优势
    rev = info.get("totalRevenue", 0)
    mcap = data.get("market_cap", 0)
    if mcap > 1e11:
        details["scale"] = ("具备", f"超大市值 ({mcap / 1e9:.1f}B)，规模效应显著")
    elif rev > 1e10:
        details["scale"] = ("具备", f"大规模收入 ({rev / 1e9:.1f}B)，规模优势")
    elif rev > 1e9:
        details["scale"] = ("部分", "中等规模，有一定规模优势")
    else:
        details["scale"] = ("有限", "规模优势不明显")

    # 5. 技术 / 专利壁垒（非技术行业不适用，不扣分）
    tech = ["semiconductor", "biotech", "pharmaceutical", "technology", "software", "hardware"]
    if any(w in industry for w in tech):
        rd = info.get("researchAndDevelopment")
        rev_for_rd = info.get("totalRevenue", 1)
        if rd is not None and rev_for_rd > 0:
            rd_ratio = rd / rev_for_rd * 100
            if rd_ratio > 15:
                details["tech_moat"] = ("较强", f"研发投入占收入 {rd_ratio:.1f}%，技术壁垒高")
            elif rd_ratio > 5:
                details["tech_moat"] = ("部分", f"研发投入占比 {rd_ratio:.1f}%，有一定技术积累")
            else:
                details["tech_moat"] = ("一般", f"研发投入占比仅 {rd_ratio:.1f}%")
        else:
            details["tech_moat"] = ("待查", "数据不足")
    else:
        details["tech_moat"] = ("N/A", "非技术驱动行业，不计入")

    strong = sum(1 for v in details.values() if v[0] in ("具备", "较强", "极强", "可能具备"))
    # 寡头垄断额外加分
    oligopoly_boost = 1 if details.get("oligopoly", ("", ""))[0] == "极强" else 0
    mapping = {5: 5, 4: 4, 3: 4, 2: 3, 1: 2, 0: 1}
    return min(5, mapping.get(strong, 1) + oligopoly_boost), details


# ─── 第四关：管理层 ─────────────────────────────────

def gate4_management(data: Dict[str, Any], company: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """评分：内部人持股、股东回馈、盈利质量、治理。"""
    details: Dict[str, Any] = {}

    # 1. 内部人持股
    ins = data.get("insider_pct")
    if ins is not None:
        if ins > 30:
            details["insider_ownership"] = (True, f"内部人持股 {ins:.1f}%，利益高度一致")
        elif ins > 10:
            details["insider_ownership"] = (True, f"内部人持股 {ins:.1f}%，利益基本一致")
        elif ins > 1:
            details["insider_ownership"] = (False, f"内部人持股仅 {ins:.1f}%，绑定不足")
        else:
            details["insider_ownership"] = (False, "内部人几乎无持股，警示")
    else:
        details["insider_ownership"] = ("N/A", "持股数据不可用")

    # 2. 股东回馈（股息 + 回购代理）
    div = data.get("dividend_yield_pct")
    if div is not None and div > 0:
        details["dividend"] = (True, f"股息率 {div:.2f}%，持续回馈股东")
    else:
        details["dividend"] = (False, "无股息或数据不足")

    # 3. 盈利质量（净利率代理）
    nm = data.get("net_margin_pct")
    if nm is not None:
        if nm > 15:
            details["earnings_quality"] = (True, f"净利率 {nm:.1f}%，盈利质量优秀")
        elif nm > 8:
            details["earnings_quality"] = (True, f"净利率 {nm:.1f}%，盈利质量良好")
        elif nm > 0:
            details["earnings_quality"] = (False, f"净利率仅 {nm:.1f}%，盈利质量一般")
        else:
            details["earnings_quality"] = (False, f"净利率 {nm:.1f}%，亏损")
    else:
        details["earnings_quality"] = ("N/A", "数据不足")

    # 4. 治理（上市历史代理）
    hist = data.get("price_history")
    if hist is not None and len(hist) > 60:
        details["governance"] = (True, "有长期公开治理记录")
    else:
        details["governance"] = ("N/A", "数据不足")

    passed = sum(1 for v in details.values() if v is not None and isinstance(v, tuple) and v[0] is True)
    total = sum(1 for v in details.values() if v is not None and isinstance(v, tuple) and v[0] is not None and v[0] != "N/A")

    score = 3  # 默认
    if total > 0:
        ratio = passed / total
        if ratio >= 0.8:
            score = 5
        elif ratio >= 0.6:
            score = 4
        elif ratio >= 0.4:
            score = 3
        elif ratio >= 0.2:
            score = 2
        else:
            score = 1

    return score, details


# ─── 第五关：安全边际 ─────────────────────────────────

def gate5_safety_margin(data: Dict[str, Any]) -> Tuple[int, Dict[str, Tuple[str, str]]]:
    """评分：绝对估值与安全边际。"""
    details: Dict[str, Tuple[str, str]] = {}

    pe = data.get("pe_ttm")
    fpe = data.get("forward_pe")
    pb = data.get("pb")
    div = data.get("dividend_yield_pct")

    # PE
    if pe is not None:
        if pe < 10:
            details["pe"] = ("低估", f"PE={pe:.1f}x，明显低估")
        elif pe < 15:
            details["pe"] = ("合理偏低", f"PE={pe:.1f}x，略低于市场均值")
        elif pe < 25:
            details["pe"] = ("合理", f"PE={pe:.1f}x，估值合理")
        elif pe < 40:
            details["pe"] = ("偏高", f"PE={pe:.1f}x，偏贵")
        else:
            details["pe"] = ("高估", f"PE={pe:.1f}x，严重高估")
    else:
        details["pe"] = ("N/A", "PE 数据不可用")

    # Forward PE
    if fpe is not None:
        if pe is not None and fpe < pe:
            details["forward_pe"] = ("积极", f"前瞻PE={fpe:.1f}x，预期盈利增长")
        else:
            details["forward_pe"] = ("中性", f"前瞻PE={fpe:.1f}x")
    else:
        details["forward_pe"] = ("N/A", "前瞻PE 不可用")

    # PB
    if pb is not None:
        if pb < 1:
            details["pb"] = ("低估", f"PB={pb:.2f}x，破净")
        elif pb < 2:
            details["pb"] = ("合理", f"PB={pb:.2f}x")
        elif pb < 5:
            details["pb"] = ("偏高", f"PB={pb:.2f}x")
        else:
            details["pb"] = ("高估", f"PB={pb:.2f}x，溢价显著")
    else:
        details["pb"] = ("N/A", "PB 数据不可用")

    # 股息率
    if div is not None:
        if div > 4:
            details["dividend"] = ("高", f"股息率 {div:.2f}%")
        elif div > 2:
            details["dividend"] = ("中等", f"股息率 {div:.2f}%")
        elif div > 0:
            details["dividend"] = ("低", f"股息率 {div:.2f}%")
        else:
            details["dividend"] = ("无", "不派息")
    else:
        details["dividend"] = ("N/A", "股息数据不可用")

    # FCF Yield
    fcf_yield = data.get("fcf_yield_pct")
    if fcf_yield is not None:
        if fcf_yield > 8:
            details["fcf_yield"] = ("优秀", f"FCF Yield={fcf_yield:.1f}%")
        elif fcf_yield > 5:
            details["fcf_yield"] = ("良好", f"FCF Yield={fcf_yield:.1f}%")
        elif fcf_yield > 2:
            details["fcf_yield"] = ("一般", f"FCF Yield={fcf_yield:.1f}%")
        else:
            details["fcf_yield"] = ("低", f"FCF Yield={fcf_yield:.1f}%")
    else:
        price = data.get("price", 0)
        fcf_ps = data.get("fcf_per_share")
        if price and fcf_ps:
            calc = (fcf_ps / price) * 100
            if calc > 8:
                details["fcf_yield"] = ("优秀", f"FCF Yield≈{calc:.1f}%（估算）")
            elif calc > 5:
                details["fcf_yield"] = ("良好", f"FCF Yield≈{calc:.1f}%（估算）")
            elif calc > 2:
                details["fcf_yield"] = ("一般", f"FCF Yield≈{calc:.1f}%（估算）")
            else:
                details["fcf_yield"] = ("低", f"FCF Yield≈{calc:.1f}%（估算）")
        else:
            details["fcf_yield"] = ("N/A", "数据不足")

    # ── 三情景估值（使用 financial_rigor 精确计算） ──
    try:
        price = data.get("price", 0)
        eps = data.get("eps_ttm")
        revenue_g = data.get("revenue_growth_pct")
        if price and eps and revenue_g is not None:
            # 从历史增长推导情景参数
            base_g = revenue_g / 100
            scenarios = {
                "乐观": (min(base_g * 1.5, 0.30), 25),
                "中性": (base_g, 20),
                "悲观": (max(base_g * 0.3, 0.0), 15),
            }

            three_section = (
                f"\n\n**三情景估值（financial_rigor 精确计算）**\n\n"
                f"| 情景 | 年增速 | 目标 PE | 目标股价 | 涨跌幅 |\n"
                f"|------|--------|--------|---------|--------|\n"
            )
            for name, (g, pe) in scenarios.items():
                future_eps = eps * (1 + g) ** 3
                target = future_eps * pe
                change = (target / price - 1) * 100
                emoji = {"乐观": "📈", "中性": "📊", "悲观": "📉"}.get(name, "")
                three_section += (
                    f"| {emoji} {name} | {g*100:.0f}% | {pe:.0f}x | "
                    f"{target:.2f} | {change:+.1f}% |\n"
                )
            three_section += "\n*基于当前EPS和历史增速推算，3年预测期*\n"
        else:
            three_section = "\n\n*数据不足，无法生成三情景估值*\n"
    except Exception:
        three_section = "\n\n*三情景估值计算异常*\n"

    # 附加到评估详情中
    details["_three_scenario"] = ("参考", three_section)

    score_map: Dict[str, int] = {
        "低估": 5, "合理偏低": 4, "合理": 3, "偏高": 2, "高估": 1,
        "优秀": 5, "良好": 4, "一般": 3, "低": 2, "无": 2,
        "高": 4, "中等": 3, "积极": 4, "中性": 3,
    }
    scores = [score_map[v[0]] for v in details.values() if v[0] in score_map]
    avg = sum(scores) / len(scores) if scores else 3
    return round(avg), details


# ─── 第六关：决策纪律 ─────────────────────────────────

def gate6_decision_discipline(data: Dict[str, Any]) -> Tuple[int, List[str]]:
    """检查 FOMO、市场预期、容错空间等情绪信号。"""
    warnings: List[str] = []

    # ── 1. 接力棒效应：过去1年涨幅过大（阈值更敏感） ──
    hist = data.get("price_history")
    pe = data.get("pe_ttm")
    roe = data.get("roe_pct")

    if hist is not None and len(hist) > 252:
        recent = hist["Close"].iloc[-252:]
        r1y = (recent.iloc[-1] / recent.iloc[0] - 1) * 100
        if r1y > 80:
            warnings.append(f"📈 过去1年涨幅 {r1y:.0f}%，警惕 FOMO 接力棒效应")
        elif r1y > 50:
            # 涨幅 50%+：如果是金融/公用事业这类慢速行业，明显过热
            if pe and roe:
                peg = pe / roe if roe > 0 else 999
                if peg > 1.5 or r1y > 60:
                    warnings.append(f"📈 1年涨 {r1y:.0f}% + PEG={peg:.1f}x，警惕接力棒效应")
            elif pe and pe > 20:
                warnings.append(f"📈 1年涨 {r1y:.0f}% + PE={pe:.0f}x，警惕追涨")
            elif not pe:
                warnings.append(f"📈 1年涨 {r1y:.0f}%，警惕接力棒效应")
        elif r1y > 40:
            if pe and roe:
                peg = pe / roe if roe > 0 else 999
                if peg > 2:
                    warnings.append(f"📈 1年涨 {r1y:.0f}% + PEG={peg:.1f}x，警惕追涨")
        elif r1y < -40:
            warnings.append(f"📉 过去1年跌幅 {r1y:.0f}%，确认不是价值陷阱")

    # ── 2. PE 过高 + 低增长（博傻） ──
    rg = data.get("revenue_growth_pct")
    if pe is not None and pe > 40:
        warnings.append(f"⚠️ PE={pe:.0f}x，市场预期极高")
    elif pe is not None and pe > 25 and rg is not None and rg < 10:
        warnings.append(f"⚡ PE={pe:.0f}x 但增速仅 {rg:.0f}%，容错空间小")

    # ── 3. 股价接近52周高点（追涨信号） ──
    price = data.get("price")
    high_52w = data.get("52w_high")
    if price and high_52w and high_52w > 0:
        pct_of_high = (price / high_52w) * 100
        if pct_of_high > 95:
            warnings.append(f"📊 股价距52周高仅差 {100-pct_of_high:.0f}%（{price:.0f}/{high_52w:.0f}），追涨风险")

    # ── 4. 超大市值公司 ──
    mcap = data.get("market_cap", 0)
    if mcap > 1.5e12:
        warnings.append("🏛️ 超大市值公司，未来增长空间有限")

    score = max(1, 5 - len(warnings))
    return score, warnings


# ╔══════════════════════════════════════════════════════════════╗
# ║  快速否决清单                                              ║
# ╚══════════════════════════════════════════════════════════════╝

def quick_veto_checklist(data: Dict[str, Any], gates: Dict[str, Any]) -> List[str]:
    """返回触发的否决原因列表。"""
    vetoes: List[str] = []

    # 连续 FCF 为负（单年度代理检查）
    fcf = data.get("free_cf")
    if fcf is not None and fcf < 0:
        vetoes.append("❌ 自由现金流为负 —— 连续3年需人工核查")

    # PE 极高且无增长（博傻嫌疑）
    pe = data.get("pe_ttm")
    rg = data.get("revenue_growth_pct")
    if pe is not None and rg is not None and pe > 50 and rg < 10:
        vetoes.append("❌ PE>50x 但收入增速<10%，有博傻嫌疑")

    return vetoes


# ╔══════════════════════════════════════════════════════════════╗
# ║  镜子测试                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

def mirror_test(company: Dict[str, Any], data: Dict[str, Any], gates: Dict[str, Any]) -> str:
    """生成镜子测试语句。"""
    name = company.get("name", company["ticker"])
    price = data.get("price", 0)
    g1 = gates.get("gate1_score", 3)
    g3 = gates.get("gate3_score", 3)
    g4 = gates.get("gate4_score", 3)
    g5 = gates.get("gate5_score", 3)
    sector = data.get("sector", "")
    industry = data.get("industry", "")

    safety_text = "充足" if g5 >= 4 else "一般" if g5 >= 3 else "不足"
    passed = g1 >= 3 and g3 >= 3 and g5 >= 4

    lines = [
        f"\n> 「我以 {price:.2f} 元买入 {name}，因为：",
        f"> 1. 这门生意的本质是{sector}行业中{industry}业务，能力圈评分 {g1}/5★；",
        f"> 2. 它的护城河评分 {g3}/5★，需要通过深度研究确认趋势；",
        f"> 3. 管理层评分 {g4}/5★，需要进一步验证诚实度和资本配置能力；",
        f"> 4. 当前价格的估值评分 {g5}/5★，安全边际{safety_text}；",
        f"> 5. 即使我错了，下行风险需要根据个人财务状况评估。」",
    ]

    if not passed:
        lines.append("\n**⚠️ 5句话说不完整 = 不买。当前镜子测试未通过。**")
    else:
        lines.append("\n**✅ 镜子测试通过。**")

    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════════╗
# ║  报告生成                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

def generate_report(
    company: Dict[str, Any],
    data: Dict[str, Any],
    gates: Dict[str, Any],
    info_grade: str,
    info_grade_desc: str,
    vetoes: List[str],
    news: Optional[List[Dict[str, str]]] = None,
) -> str:
    """生成完整的 Checklist Markdown 报告。"""
    name = company.get("name", company["ticker"])
    ticker = company["ticker"]
    price = data.get("price", 0)
    mcap = data.get("market_cap", 0)

    g1 = gates["gate1_score"]
    g2 = gates["gate2_score"]
    g3 = gates["gate3_score"]
    g4 = gates["gate4_score"]
    g5 = gates["gate5_score"]
    g6 = gates["gate6_score"]

    total = g1 + g2 + g3 + g4 + g5
    passed = sum(1 for s in (g1, g2, g3, g4, g5) if s >= 3)

    # ── 核心数据表 ──
    def _pct(v):
        return f"{v}%" if v is not None else "N/A"

    def _val(v):
        return f"{v}" if v is not None else "N/A"

    core_rows = [
        ("当前股价", f"{price:.2f}"),
        ("市值", f"{mcap / 1e9:.2f}B" if mcap > 0 else "N/A"),
        ("PE (TTM)", _val(data.get('pe_ttm'))),
        ("前瞻 PE", _val(data.get('forward_pe'))),
        ("PB", _val(data.get('pb'))),
        ("股息率", _pct(data.get('dividend_yield_pct'))),
        ("ROE", _pct(data.get('roe_pct'))),
        ("毛利率", _pct(data.get('gross_margin_pct'))),
        ("净利率", _pct(data.get('net_margin_pct'))),
        ("收入增长率", _pct(data.get('revenue_growth_pct'))),
        ("自由现金流", f"{data.get('free_cf'):,.0f}" if data.get('free_cf') else "N/A"),
        ("负债/权益比", _pct(data.get('debt_to_equity'))),
    ]
    core_table = "\n".join(f"| {k} | {v} |" for k, v in core_rows)

    # ── 各部分 ──
    g2_table = _gate2_table(gates["gate2_details"])
    g3_table = _gate3_table(gates["gate3_details"])
    g4_table = _gate4_table(gates["gate4_details"])
    g5_table = _gate5_table(gates["gate5_details"])
    g6_warnings = gates.get("gate6_warnings", [])

    # ── 否决检查 ──
    if vetoes:
        veto_section = ""
        for v in vetoes:
            veto_section += f"- {v}\n"
        veto_section += "\n**⚠️ 触发否决条件！**\n"
    else:
        veto_section = (
            "- [ ] 说得清楚这家公司怎么赚钱（需人工确认）\n"
            "- [ ] 连续3年自由现金流为负且看不到改善\n"
            "- [ ] 管理层有诚信污点（需人工判断）\n"
            "- [ ] 竞争优势正在被不可逆侵蚀（需人工判断）\n"
            "- [ ] 需要靠「下一个接盘者出更高价」来赚钱\n"
            "- [ ] 无法承受这笔投资归零的后果\n"
            "- [ ] 买入理由主要是「别人都在买」或「最近涨得好」\n"
            "- [ ] 无法用200字以内写清楚买入理由\n"
        )

    # ── 总结 ──
    # 安全边际降级：生意再好，价格太贵也不应通过
    effective_passed = passed
    if g5 < 3 and effective_passed >= 4:
        effective_passed = passed - 1  # 通过 → 灰色
    elif g5 < 2 and effective_passed >= 3:
        effective_passed = passed - 1  # 灰色 → 不通过
    # 双重边缘降级：安全边际和纪律同时弱时降级
    if g5 <= 3 and g6 <= 3 and effective_passed >= 4:
        effective_passed -= 1  # 通过 → 灰色
    elif g5 <= 3 and g6 <= 2 and effective_passed >= 3:
        effective_passed -= 1  # 灰色 → 不通过
    # 软降级：好公司 + 贵价 + 有追涨信号 → 强制灰色
    # 典型场景：RY 这种顶级银行，PE=19x高位，股价距52周高仅差3%
    if g5 <= 3 and g6 <= 4 and effective_passed >= 4 and g1 >= 4 and g3 >= 4:
        effective_passed = max(effective_passed - 2, 2)  # 通过 → 不通过

    if effective_passed >= 4:
        conclusion = "✅ **Checklist 总体评估：通过** — 可以进入深度研究阶段\n"
        if g5 < 3:
            conclusion += "\n⚠️ 注意：安全边际评分较低，建议等待更好的价格"
    elif effective_passed >= 3:
        conclusion = "❓ **灰色地带** — 关键指标有亮点也有顾虑，需投资者自行判断"
    else:
        conclusion = "❌ **未通过 Checklist** — 多项核心指标不达标"

    # ── 镜子测试 ──
    mirror = mirror_test(company, data, gates)

    return f"""# 巴菲特价值投资买入前 Checklist 报告

**公司**：{name}（{ticker}）
**分析日期**：{CURRENT_DATE}
**信息丰富度评级**：{info_grade} 级 — {info_grade_desc}
**交易所**：{company.get("exchange", "N/A")}　**行业**：{data.get("sector", "")} / {data.get("industry", "")}

---

## 1. 核心数据概览

| 指标 | 数值 |
|------|------|
{core_table}

---

## 1b. 近6个月重大事件

{_news_table(news) if news else "无近期新闻数据"}

---

## 2. 六关 Checklist

### 第一关：能力圈 {star(g1)}

**评分**：{g1}/5 ★

{gates["gate1_reason"]}

*评估说明*：基于行业复杂度和商业模式的可理解性自动评分。需投资者根据自身认知做最终判断。

---

### 第二关：好生意 {star(g2)}

**评分**：{g2}/5 ★

| 指标 | 判断 |
|------|------|
{g2_table}

---

### 第三关：护城河 {star(g3)}

**评分**：{g3}/5 ★

| 护城河类型 | 是否具备 | 具体证据 |
|-----------|---------|---------|
{g3_table}

---

### 第四关：管理层 {star(g4)}

**评分**：{g4}/5 ★

| 检查项 | 评估 |
|--------|------|
{g4_table}

---

### 第五关：安全边际 {star(g5)}

**评分**：{g5}/5 ★

| 指标 | 判断 |
|------|------|
{g5_table}

---

### 第六关：决策纪律 {star(g6)}

**评分**：{g6}/5 ★

{chr(10).join(f"- {w}" for w in g6_warnings) if g6_warnings else "- ✅ 未发现明显情绪信号"}

**买入前自问**：
- [ ] 是否因为 FOMO 想买？
- [ ] 是否因为别人推荐才想买？
- [ ] 如果停牌5年你能接受吗？
- [ ] 买入论述能否用200字以内写清楚？

---

## 3. 快速否决清单

{veto_section}

---

## 4. 镜子测试

{mirror}

---

## 5. 综合结论

| 关卡 | 评分 | 状态 |
|------|------|------|
| 第一关：能力圈 | {star(g1)} | {'✅ 通过' if g1 >= 3 else '❌ 不通过'} |
| 第二关：好生意 | {star(g2)} | {'✅ 通过' if g2 >= 3 else '❌ 不通过'} |
| 第三关：护城河 | {star(g3)} | {'✅ 通过' if g3 >= 3 else '❌ 不通过'} |
| 第四关：管理层 | {star(g4)} | {'✅ 通过' if g4 >= 3 else '❌ 不通过'} |
| 第五关：安全边际 | {star(g5)} | {'✅ 通过' if g5 >= 3 else '❌ 不通过'} |
| 第六关：决策纪律 | {star(g6)} | {'✅ 通过' if g6 >= 3 else '❌ 不通过'} |

**总评分**：{total}/25（{passed}/5 关通过）

{conclusion}

---

*报告由 巴菲特价值投资买入前 Checklist 工具（standalone 版）自动生成*
*分析日期：{CURRENT_DATE}* | *数据来源：Yahoo Finance*
*⚠️ 免责声明：本报告为自动化分析工具，不构成投资建议。投资决策需结合个人研究和判断。*

*"投资的第一条规则是不要亏损。第二条规则是不要忘记第一条。" — 沃伦·巴菲特*
"""


def _gate2_table(details: Dict[str, Tuple[bool, str]]) -> str:
    label_map = {
        "roe": "ROE（5年均值）",
        "gross_margin": "毛利率",
        "fcf": "自由现金流",
        "capital_intensity": "资本效率",
        "debt": "负债水平",
    }
    rows = []
    for k, (ok, text) in details.items():
        label = label_map.get(k, k)
        icon = "✅" if ok else ("⚠️" if isinstance(ok, str) else "❌")
        rows.append(f"| {label} | {icon} {text} |")
    return "\n".join(rows)


def _gate3_table(details: Dict[str, Tuple[str, str]]) -> str:
    label_map = {
        "brand": "品牌 / 定价权",
        "switching_cost": "转换成本",
        "network_effect": "网络效应",
        "scale": "成本 / 规模优势",
        "tech_moat": "技术 / 专利壁垒",
    }
    rows = []
    for k, (has, evidence) in details.items():
        label = label_map.get(k, k)
        rows.append(f"| {label} | {has} | {evidence} |")
    return "\n".join(rows)


def _gate4_table(details: Dict[str, Any]) -> str:
    label_map = {
        "insider_ownership": "内部人持股",
        "dividend": "股东回馈（股息）",
        "earnings_quality": "盈利质量",
        "governance": "治理记录",
    }
    rows = []
    for k, v in details.items():
        label = label_map.get(k, k)
        if isinstance(v, tuple):
            ok, text = v
            icon = "✅" if ok is True else "⚠️" if ok == "部分" else "❌"
            rows.append(f"| {label} | {icon} {text} |")
        else:
            rows.append(f"| {label} | {v} |")
    return "\n".join(rows)


def _gate5_table(details: Dict[str, Tuple[str, str]]) -> str:
    label_map = {
        "pe": "PE（TTM）",
        "forward_pe": "前瞻 PE",
        "pb": "PB",
        "dividend": "股息率",
        "fcf_yield": "FCF Yield",
    }
    rows = []
    for k, v in details.items():
        if k == "_three_scenario":
            continue  # 单独输出
        label = label_map.get(k, k)
        status, text = v
        good = status in ("低估", "合理偏低", "优秀", "良好", "积极", "高")
        warn = status in ("合理", "中性", "中等")
        icon = "✅" if good else "⚠️" if warn else "❌"
        rows.append(f"| {label} | {icon} {status} — {text} |")
    raw = "\n".join(rows)

    # 附加三情景估值
    three = details.get("_three_scenario")
    if three:
        raw += three[1]  # 追加三情景估值表
    return raw


def _news_table(news: List[Dict[str, str]]) -> str:
    """渲染新闻事件表格。"""
    if not news:
        return "无近期新闻数据"
    rows = [
        "| 日期 | 标题 | 来源 |",
        "|------|------|------|",
    ]
    for item in news:
        date = item.get("date", "")
        title = item.get("title", "")
        source = item.get("source", "")
        # 如果有 URL 则加链接
        url = item.get("url", "")
        if url:
            title = f"[{title}]({url})"
        rows.append(f"| {date} | {title} | {source} |")
    return "\n".join(rows)


# ╔══════════════════════════════════════════════════════════════╗
# ║  多公司对比总览                                            ║
# ╚══════════════════════════════════════════════════════════════╝

def generate_comparison_table(results: List[Dict[str, Any]]) -> str:
    """生成多公司对比总览 Markdown 表格。"""
    header = "| 公司 | 结论 | 能力圈 | 好生意 | 护城河 | 管理层 | 安全边际 | 决策纪律 | 总评分 |"
    sep = "|------|------|--------|--------|--------|--------|---------|---------|--------|"

    rows = []
    for r in results:
        c = r["company"]
        g = r["gates"]
        name = c.get("name", c["ticker"])
        ticker = c["ticker"]
        g1, g2, g3, g4, g5, g6 = (
            g["gate1_score"], g["gate2_score"], g["gate3_score"],
            g["gate4_score"], g["gate5_score"], g["gate6_score"],
        )
        total = g1 + g2 + g3 + g4 + g5
        passed = sum(1 for s in (g1, g2, g3, g4, g5) if s >= 3)
        if passed >= 4:
            conclusion = "✅ 通过"
        elif passed >= 3:
            conclusion = "❓ 灰色"
        else:
            conclusion = "❌ 未通过"

        rows.append(
            f"| {name}（{ticker}）| {conclusion} | {star(g1)} | {star(g2)} | {star(g3)} | {star(g4)} | {star(g5)} | {star(g6)} | {total}/25 |"
        )

    report = "# 巴菲特价值投资买入前 Checklist — 多公司对比总览\n\n"
    report += f"**分析日期**：{CURRENT_DATE}\n\n"
    report += header + "\n" + sep + "\n" + "\n".join(rows) + "\n"

    # 单独附件各公司详细报告
    report += "\n\n---\n## 各公司详细报告\n\n"
    for r in results:
        report += f"- [{r['company']['name']}（{r['company']['ticker']}）](<巴菲特Checklist-{r['company']['name']}-{r['company']['ticker']}.md>)\n"

    return report


# ╔══════════════════════════════════════════════════════════════╗
# ║  Main                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def analyze_one(ticker: str) -> Optional[Dict[str, Any]]:
    """对单个公司执行完整 Checklist 分析并输出进度。"""
    pad = f"{ticker:>10}"
    print(f"\n  {'─' * 50}")
    print(f"  🔍  正在分析：{pad}")
    print(f"  {'─' * 50}")

    # Step 1
    print(f"  📋 Step 1/6：识别公司……", end=" ")
    company = identify_company(ticker)
    if company.get("error"):
        print(f"❌ {company['error']}")
        return None
    print(f"✅  {company['name']}（{company['exchange']}）")

    # Step 1.5
    print(f"  📋 信息评级……", end=" ")
    grade, desc = grade_information_availability(company)
    print(f"✅  {grade} 级")

    # Step 2
    print(f"  📋 Step 2/6：收集数据……", end=" ")
    data = collect_financial_data(company)
    print(f"✅  核心数据已获取")

    # Step 2b — 新闻
    print(f"  📋 获取近期新闻……", end=" ")
    news = collect_news(company["ticker"])
    print(f"✅  {len(news)} 条")

    # Step 3 — 六关
    print(f"  📋 Step 3/6：执行六关评分……")
    g1, g1r = gate1_circle_of_competence(data, company)
    print(f"     ├─ 第一关（能力圈）：{star(g1)}  {g1r[:40]}…")
    g2, g2d = gate2_good_business(data)
    print(f"     ├─ 第二关（好生意）：{star(g2)}")
    g3, g3d = gate3_moat(data, company)
    print(f"     ├─ 第三关（护城河）：{star(g3)}")
    g4, g4d = gate4_management(data, company)
    print(f"     ├─ 第四关（管理层）：{star(g4)}")
    g5, g5d = gate5_safety_margin(data)
    print(f"     ├─ 第五关（安全边际）：{star(g5)}")
    g6, g6w = gate6_decision_discipline(data)
    print(f"     └─ 第六关（决策纪律）：{star(g6)}")

    gates: Dict[str, Any] = {
        "gate1_score": g1, "gate1_reason": g1r,
        "gate2_score": g2, "gate2_details": g2d,
        "gate3_score": g3, "gate3_details": g3d,
        "gate4_score": g4, "gate4_details": g4d,
        "gate5_score": g5, "gate5_details": g5d,
        "gate6_score": g6, "gate6_warnings": g6w,
    }

    # Step 4
    print(f"  📋 Step 4/6：快速否决检查……", end=" ")
    vetoes = quick_veto_checklist(data, gates)
    if vetoes:
        print("⚠️  触发否决项：")
        for v in vetoes:
            print(f"     └─ {v}")
    else:
        print("✅  未触发自动否决")

    # Step 5 & 6
    print(f"  📋 Step 5-6/6：生成报告……", end=" ")
    report = generate_report(company, data, gates, grade, desc, vetoes, news)
    print("✅")

    return {
        "company": company,
        "data": data,
        "gates": gates,
        "info_grade": grade,
        "vetoes": vetoes,
        "news": news,
        "report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="巴菲特价值投资买入前 Checklist —— 系统化价值投资分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 investment-checklist.py AAPL\n"
            "  python3 investment-checklist.py 0700.HK\n"
            "  python3 investment-checklist.py =TSLA\n"
            "  python3 investment-checklist.py AAPL MSFT GOOGL\n"
            "  python3 investment-checklist.py --output report.md AAPL\n"
            "  python3 investment-checklist.py --no-save AAPL\n"
        ),
    )
    parser.add_argument("tickers", nargs="+", help="股票代码（如 AAPL、0700.HK、MSFT）")
    parser.add_argument("--output", "-o", help="输出报告到指定文件")
    parser.add_argument("--no-save", action="store_true", help="不保存报告到 ~/巴菲特Checklist/")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║     巴菲特价值投资买入前 Checklist  分析工具            ║")
    print(f"║     分析日期：{CURRENT_DATE}                               ║")
    print("╚══════════════════════════════════════════════════════════╝")

    results: List[Dict[str, Any]] = []
    for raw in args.tickers:
        r = analyze_one(raw.strip().upper())
        if r:
            results.append(r)

    if not results:
        print("\n❌ 所有分析均失败，退出。")
        sys.exit(1)

    # ── 保存报告 ──
    if not args.no_save:
        os.makedirs(REPORT_DIR, exist_ok=True)
        for r in results:
            name = r["company"]["name"]
            ticker = r["company"]["ticker"]
            safe = re.sub(r'[<>:"/\\|?*]', "", f"{name}-{ticker}")
            path = os.path.join(REPORT_DIR, f"巴菲特Checklist-{safe}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(r["report"])
            print(f"\n  💾 报告已保存：{path}")

        if len(results) > 1:
            comp_path = os.path.join(REPORT_DIR, "巴菲特Checklist-多公司对比.md")
            with open(comp_path, "w", encoding="utf-8") as f:
                f.write(generate_comparison_table(results))
            print(f"  💾 对比总览已保存：{comp_path}")

    # ── 多公司对比 ──
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("  📊  多公司对比总览")
        print("=" * 60)
        for line in generate_comparison_table(results).split("\n"):
            if line.startswith("|"):
                print(f"  {line}")
        print()

    # ── 汇总 ──
    print(f"\n  {'=' * 50}")
    print(f"  ✅  分析完成！成功：{len(results)}/{len(args.tickers)}")
    for r in results:
        g = r["gates"]
        t = g["gate1_score"] + g["gate2_score"] + g["gate3_score"] + g["gate4_score"] + g["gate5_score"]
        p = sum(1 for s in [g["gate1_score"], g["gate2_score"], g["gate3_score"], g["gate4_score"], g["gate5_score"]] if s >= 3)
        print(f"     {r['company']['name']}（{r['company']['ticker']}）：{t}/25（{p}/5 关通过）")

    if args.output:
        if len(results) == 1:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(results[0]["report"])
        else:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(generate_comparison_table(results))
        print(f"\n  📄 报告已输出至：{args.output}")


if __name__ == "__main__":
    main()
