#!/usr/bin/env python3
"""
Financial Modeling Prep 数据源
=============================
作为 yfinance 的备选，获取毛利率/FCF/负债率等财务细节。
注册免费 API Key: https://site.financialmodelingprep.com/register
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# FMP API 配置
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_KEY = os.environ.get("FMP_API_KEY", "zspuCBaKeLVJA1rV6oDsytuQunx64FLf")

# 本地缓存（避免重复请求）
_cache: Dict[str, Dict] = {}
_CACHE_TTL = 8 * 3600  # 8 小时
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".fmp_cache")


def _request(path: str) -> Optional[dict]:
    """调用 FMP API。"""
    sep = "&" if "?" in path else "?"
    url = f"{FMP_BASE}/{path}{sep}apikey={FMP_KEY}"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        resp = urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            return data[0] if data else None
        return data
    except HTTPError as e:
        if e.code == 429:
            logger.debug("FMP: 429 rate limited")
        else:
            logger.debug(f"FMP: HTTP {e.code} for {path}")
        return None
    except Exception as e:
        logger.debug(f"FMP: {e} for {path}")
        return None


def get_financials(ticker: str) -> Dict[str, Any]:
    """获取核心财务指标（同 collect_financial_data 格式）。"""
    ticker = ticker.strip().upper()
    result: Dict[str, Any] = {}

    # ── profile 获取基本信息 ──
    profile = _request(f"profile?symbol={ticker}")
    if profile:
        result["price"] = profile.get("price")
        result["market_cap"] = profile.get("marketCap")
        result["pe_ttm"] = profile.get("pe")
        result["pb"] = profile.get("pb")
        result["eps_ttm"] = profile.get("eps")
        result["sector"] = profile.get("sector", "")
        result["industry"] = profile.get("industry", "")
        result["company_name"] = profile.get("companyName", ticker)
        result["employees"] = profile.get("fullTimeEmployees")
        result["country"] = profile.get("country", "")
        result["website"] = profile.get("website", "")
        result["description"] = (profile.get("description") or "")[:300]
        result["exchange"] = profile.get("exchange", "")
        # 股息率 = lastDividend / price
        div = profile.get("lastDividend")
        pr = result.get("price")
        result["dividend_yield_pct"] = round(div / pr * 100, 2) if div and pr else None

    # ── key-metrics-ttm 获取财务比率 ──
    metrics = _request(f"key-metrics-ttm?symbol={ticker}&limit=1")
    if metrics:
        # ROE（0.33 → 33%）
        roe = metrics.get("returnOnEquityTTM")
        result["roe_pct"] = round(roe * 100, 1) if roe else None
        # ROA
        roa = metrics.get("returnOnAssetsTTM")
        result["roa_pct"] = round(roa * 100, 1) if roa else None
        # FCF Yield
        fy = metrics.get("freeCashFlowYieldTTM")
        result["fcf_yield_pct"] = round(fy * 100, 1) if fy else None
        # FCF（绝对值）
        fcf = metrics.get("freeCashFlowToEquityTTM")
        result["free_cf"] = fcf

    # ── income-statement 计算利润率 ──
    income = _request(f"income-statement?symbol={ticker}&limit=1")
    if income:
        rev = income.get("revenue")
        gp = income.get("grossProfit")
        ni = income.get("netIncome")
        eps = income.get("epsDiluted") or income.get("eps")
        if rev and rev > 0:
            if gp is not None:
                result["gross_margin_pct"] = round(gp / rev * 100, 1)
            if ni is not None:
                result["net_margin_pct"] = round(ni / rev * 100, 1)
        if not result.get("eps_ttm") and eps:
            result["eps_ttm"] = eps

    # ── balance-sheet 计算负债率 ──
    bs = _request(f"balance-sheet-statement?symbol={ticker}&limit=1")
    if bs:
        debt = bs.get("totalDebt")
        eq = bs.get("totalEquity")
        cash = bs.get("cashAndCashEquivalents")
        if debt is not None and eq and eq > 0:
            result["debt_to_equity"] = round(debt / eq * 100, 1)
        if cash is not None:
            result["total_cash"] = cash
        if debt is not None:
            result["total_debt"] = debt

    return result


def health_check() -> bool:
    """检查 FMP API 是否可用。"""
    result = _request("profile?symbol=AAPL")
    return result is not None and result.get("symbol") == "AAPL"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if health_check():
        print("✅ FMP API 可用")
        for t in ["HCI", "RY"]:
            print(f"\n📊 {t}")
            data = get_financials(t)
            for k, v in sorted(data.items()):
                if v is not None and str(v) not in ("", "0", "None"):
                    print(f"  {k}: {v}")
    else:
        print("❌ FMP API 不可用")
