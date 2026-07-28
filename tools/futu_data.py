#!/usr/bin/env python3
"""
Futu API 数据提供器
=====================
通过本地 Futu OpenD 获取美股行情和财务数据。
作为 yfinance 的替代/补充，降低被限流风险。

用法:
    from tools.futu_data import FutuDataProvider
    fp = FutuDataProvider()
    data = fp.get_market_data("HCI")   # 返回 dict
    info = fp.get_company_info("RY")    # 返回 dict
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 尝试导入 Futu SDK
try:
    from futu import (
        OpenQuoteContext, RET_OK,
        Market, SecurityType, KLType,
        FinancialQuarter,
    )
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False
    logger.warning("futu-api 未安装，FutuDataProvider 不可用")


class FutuDataProvider:
    """通过本地 OpenD 获取股票数据的封装。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self._host = host
        self._port = port
        self._ctx: Optional[OpenQuoteContext] = None
        self._connected = False

    # ── 连接管理 ──

    def _ensure_ctx(self) -> Optional[OpenQuoteContext]:
        """确保连接已建立。"""
        if not HAS_FUTU:
            return None
        if self._ctx is not None:
            return self._ctx
        try:
            self._ctx = OpenQuoteContext(host=self._host, port=self._port)
            self._connected = True
            logger.debug(f"Futu OpenD 已连接 {self._host}:{self._port}")
            return self._ctx
        except Exception as e:
            logger.warning(f"Futu OpenD 连接失败: {e}")
            self._connected = False
            return None

    def close(self):
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
        self._ctx = None
        self._connected = False

    def __del__(self):
        self.close()

    # ── 代码格式转换 ──

    @staticmethod
    def to_futu_code(ticker: str) -> str:
        """AAPL → US.AAPL"""
        ticker = ticker.strip().upper()
        if "." in ticker:
            return ticker
        return f"US.{ticker}"

    # ── 核心数据：实时行情 ──

    def get_market_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        获取核心行情数据（替代 yfinance 的 info dict 大部分字段）。
        返回 dict，字段与 investment-checklist.py 的 collect_financial_data 兼容。
        """
        ctx = self._ensure_ctx()
        if ctx is None:
            return None

        code = self.to_futu_code(ticker)
        try:
            ret, data = ctx.get_market_snapshot([code])
            if ret != RET_OK or data is None or data.empty:
                logger.debug(f"Futu snapshot fail for {code}: {data}")
                return None

            row = data.iloc[0]
            result: Dict[str, Any] = {}

            # 价格和估值
            result["price"] = float(row.get("last_price", 0) or 0)
            result["market_cap"] = float(row.get("total_market_val", 0) or 0)
            result["pe_ttm"] = float(row["pe_ttm_ratio"]) if row.get("pe_ttm_ratio") and row["pe_ttm_ratio"] > 0 else None
            result["forward_pe"] = None  # Futu snapshot 不提供
            result["pb"] = float(row["pb_ratio"]) if row.get("pb_ratio") and row["pb_ratio"] > 0 else None

            # 股息
            div_ttm = float(row.get("dividend_ratio_ttm", 0) or 0)
            result["dividend_yield_pct"] = round(div_ttm, 2) if div_ttm > 0 else None

            # 每股数据
            eps = float(row.get("earning_per_share", 0) or 0)
            result["eps_ttm"] = eps if eps > 0 else None
            result["eps_forward"] = None
            bvps = float(row.get("net_asset_per_share", 0) or 0)
            result["bvps"] = bvps if bvps > 0 else None
            result["fcf_per_share"] = None

            # 盈利能力（从 snapshot 计算）
            na = float(row.get("net_asset", 0) or 0)
            np = float(row.get("net_profit", 0) or 0)
            mcap = float(row.get("total_market_val", 0) or 0)

            result["roe_pct"] = round(np / na * 100, 1) if na > 0 and np > 0 else None
            result["net_margin_pct"] = None
            result["gross_margin_pct"] = None
            result["roa_pct"] = None

            # 增长
            result["revenue_growth_pct"] = None
            result["earnings_growth_pct"] = None

            # 财务健康
            result["total_debt"] = None
            result["total_cash"] = None
            result["operating_cf"] = None
            result["free_cf"] = None
            result["debt_to_equity"] = None
            result["net_cash"] = None

            # 股东结构
            result["insider_pct"] = None
            result["institution_pct"] = None

            # 高管
            result["ceo"] = None  # 从 company_profile 获取

            # 行业
            result["sector"] = ""
            result["industry"] = ""

            # 额外字段
            result["issued_shares"] = float(row.get("issued_shares", 0) or 0)
            result["net_asset"] = na
            result["net_profit"] = np
            result["52w_high"] = float(row.get("highest52weeks_price", 0) or 0)
            result["52w_low"] = float(row.get("lowest52weeks_price", 0) or 0)
            result["pe_ratio"] = float(row["pe_ratio"]) if row.get("pe_ratio") and row["pe_ratio"] > 0 else None

            return result

        except Exception as e:
            logger.debug(f"Futu get_market_data({ticker}): {e}")
            return None

    # ── 公司信息 ──

    def get_company_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """获取公司基本信息。"""
        ctx = self._ensure_ctx()
        if ctx is None:
            return None

        code = self.to_futu_code(ticker)
        try:
            ret, data = ctx.get_company_profile(code)
            if ret != RET_OK or data is None or data.empty:
                return None

            # get_company_profile 返回 key-value 格式
            info: Dict[str, Any] = {}
            for _, row in data.iterrows():
                key = row.get("name", "")
                val = row.get("value", "")
                if key and val is not None:
                    info[key] = val

            return {
                "name": info.get("公司名称", info.get("name", ticker)),
                "exchange": info.get("所属市场", ""),
                "sector": "",            # Futu 不直接提供行业分类
                "industry": "",
                "country": info.get("国家", ""),
                "website": info.get("网址", ""),
                "employees": info.get("员工数量", ""),
                "description": str(info.get("公司简介", ""))[:300],
                "ceo": info.get("CEO", ""),
                "listing_date": info.get("上市日期", ""),
                "isin": info.get("ISIN代码", ""),
            }
        except Exception as e:
            logger.debug(f"Futu get_company_info({ticker}): {e}")
            return None

    # ── 历史 K 线 ──

    def get_history_kline(self, ticker: str, days: int = 365) -> Optional[List[Dict]]:
        """
        获取历史 K 线数据。
        返回按时间升序排列的列表，每项含 date, close, volume。
        """
        ctx = self._ensure_ctx()
        if ctx is None:
            return None

        code = self.to_futu_code(ticker)
        end = datetime.now()
        start = end - timedelta(days=days)

        try:
            ret, data = ctx.request_history_kline(
                code, KLType.K_DAY,
                start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
            )
            if ret != RET_OK or data is None or data.empty:
                return None

            klines = []
            for _, row in data.iterrows():
                klines.append({
                    "date": str(row.get("time_key", "")),
                    "close": float(row.get("close", 0)),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "volume": float(row.get("volume", 0)),
                })
            return klines
        except Exception as e:
            logger.debug(f"Futu kline({ticker}): {e}")
            return None

    # ── 财务指标（从财报获取，短超时避免卡死） ──

    def get_financials(self, ticker: str) -> Optional[Dict[str, Any]]:
        """尝试获取财务指标（短超时，获取不到不影响主流程）。"""
        ctx = self._ensure_ctx()
        if ctx is None:
            return None

        code = self.to_futu_code(ticker)
        result: Dict[str, Any] = {}

        try:
            from futu import FinancialQuarter
            import socket
            old_to = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10)
            ret, data = ctx.get_financials_statements(
                code, FinancialQuarter.ANNUAL, None, None, None, 1
            )
            socket.setdefaulttimeout(old_to)

            if ret != 0 or data is None or len(data) == 0:
                return None

            for _, row in data.iterrows():
                for col in data.columns:
                    cl = col.lower()
                    val = row[col]
                    if val is None or str(val) in ('', 'nan', 'N/A'):
                        continue
                    try:
                        fval = float(val)
                    except (ValueError, TypeError):
                        continue
                    if 'gross_profit' in cl and 'ratio' in cl:
                        result['gross_margin_pct'] = round(fval, 1)
                    elif cl == 'net_profit_ratio' or 'net_margin' in cl:
                        result['net_margin_pct'] = round(fval, 1)
                    elif 'free_cash_flow' in cl:
                        result['free_cf'] = fval
                    elif 'operating_cash' in cl or 'nocf' in cl:
                        result['operating_cf'] = fval
                    elif 'revenue' in cl and 'growth' in cl:
                        result['revenue_growth_pct'] = round(fval, 1)
                    elif 'debt_to_assets' in cl or 'debt_ratio' in cl:
                        result['debt_to_equity'] = round(fval, 1)
                    elif 'total_debt' in cl or 'interest_bearing_debt' in cl:
                        result['total_debt'] = fval
                    elif 'cash_equivalents' in cl:
                        result['total_cash'] = fval
            return result if result else None

        except Exception as e:
            logger.debug(f'Futu financials({ticker}): {e}')
            return None    # ── 新闻 ──

    def get_news(self, ticker: str, max_items: int = 8) -> List[Dict[str, str]]:
        """获取公司新闻。"""
        ctx = self._ensure_ctx()
        if ctx is None:
            return []

        code = self.to_futu_code(ticker)
        try:
            ret, data = ctx.get_search_news(code, language_id="en")
            if ret != RET_OK or data is None or data.empty:
                return []

            news = []
            for _, row in data.iterrows():
                news.append({
                    "title": str(row.get("title", "")),
                    "url": str(row.get("url", "")),
                    "date": str(row.get("time", "")),
                    "summary": str(row.get("summary", ""))[:200],
                })
                if len(news) >= max_items:
                    break
            return news
        except Exception as e:
            logger.debug(f"Futu news({ticker}): {e}")
            return []

    # ── 整合：一站式获取 Checklist 所需全部数据 ──

    def get_all_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        一站式获取 Checklist 分析所需的全部数据。
        合并行情 + 公司信息，缺失字段留 None。
        """
        market = self.get_market_data(ticker)
        if market is None:
            return None

        info = self.get_company_info(ticker)
        if info:
            market["sector"] = info.get("sector", "")
            market["industry"] = info.get("industry", "")
            market["company_name"] = info.get("name", ticker)
            market["ceo"] = info.get("ceo", "")
            market["description"] = info.get("description", "")
            market["country"] = info.get("country", "")
            market["website"] = info.get("website", "")
            market["employees"] = info.get("employees", "")
        else:
            market["company_name"] = ticker

        # 尝试拉 K 线（可选，用于计算年涨幅）
        try:
            klines = self.get_history_kline(ticker, 365)
            if klines and len(klines) > 1:
                first = klines[0]["close"]
                last = klines[-1]["close"]
                market["1y_return_pct"] = round((last / first - 1) * 100, 1) if first > 0 else None
                market["price_history"] = klines
            else:
                market["1y_return_pct"] = None
                market["price_history"] = None
        except Exception:
            market["1y_return_pct"] = None
            market["price_history"] = None

        return market

    # ── 健康检查 ──

    def health_check(self) -> bool:
        """检查 Futu OpenD 是否可用。"""
        ctx = self._ensure_ctx()
        if ctx is None:
            return False
        try:
            ret, _ = ctx.get_global_state()
            return ret == RET_OK
        except Exception:
            return False


# ── 简易测试 ──
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fp = FutuDataProvider()

    if not fp.health_check():
        print("❌ Futu OpenD 未连接")
        sys.exit(1)

    for ticker in ["HCI", "RY"]:
        print(f"\n{'=' * 50}")
        print(f"📊 {ticker}")
        data = fp.get_all_data(ticker)
        if data:
            for k, v in sorted(data.items()):
                if v is not None and str(v) != "" and str(v) != "0" and v != 0 and v != 0.0:
                    print(f"  {k}: {v}")
        else:
            print("  ❌ 无数据")
