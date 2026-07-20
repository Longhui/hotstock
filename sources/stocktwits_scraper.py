"""
StockTwits 数据抓取

方案 A：通过公共 sitemap / 网页抓取热门股票（无需凭证）
方案 B：通过 StockTwits API（需注册免费 API key）

StockTwits API 注册：https://api.stocktwits.com/developers
"""

import re
import requests
import logging
from datetime import datetime, timezone, timedelta
from sources import BaseScraper, Post

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}


class StockTwitsScraper(BaseScraper):
    """
    StockTwits 抓取
    优先使用 API（access_token 可配置），否则回退到网页尝试
    """

    def __init__(self, access_token: str = ""):
        self.access_token = access_token
        self.base_url = "https://api.stocktwits.com/api/2"
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @property
    def name(self):
        return "StockTwits"

    def fetch_last_week(self) -> list[Post]:
        all_msgs = []

        if self.access_token:
            # 方案 A：使用 API
            all_msgs = self._fetch_via_api()
        else:
            # 方案 B：尝试网页抓取
            all_msgs = self._fetch_via_web()

        return all_msgs

    def _fetch_via_api(self) -> list[Post]:
        """通过 StockTwits API 获取"""
        posts = []

        # 获取趋势股票列表
        try:
            resp = self.session.get(
                f"{self.base_url}/trending/symbols.json",
                params={"access_token": self.access_token},
                timeout=10,
            )
            if resp.status_code == 200:
                symbols = resp.json().get("symbols", [])
                trending_symbols = [s["symbol"] for s in symbols if s.get("symbol")]
                logger.info(f"  StockTwits 趋势股票: {len(trending_symbols)} 只")
            else:
                logger.warning(f"  StockTwits API 返回 {resp.status_code}")
                return []
        except Exception as e:
            logger.warning(f"  StockTwits API 错误: {e}")
            return []

        # 获取每只趋势股票的消息
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        for sym in trending_symbols[:30]:
            try:
                resp = self.session.get(
                    f"{self.base_url}/streams/symbol/{sym}.json",
                    params={"access_token": self.access_token, "limit": 30, "filter": "top"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                for msg in resp.json().get("messages", []):
                    created = datetime.fromtimestamp(msg["created_at"], tz=timezone.utc)
                    if created < cutoff:
                        continue
                    p = self._convert(msg, sym)
                    if p:
                        posts.append(p)
            except:
                continue

        logger.info(f"  StockTwits API: {len(posts)} 条消息")
        return posts

    def _fetch_via_web(self) -> list[Post]:
        """通过网页抓取获取热点股票"""
        import time
        posts = []

        # 尝试从 discover/trending 页面获取热门股票列表
        trending_symbols = []

        # 方式1：从 sitemaps 获取
        try:
            resp = self.session.get("https://stocktwits.com/sitemap.xml", timeout=10)
            if resp.status_code == 200:
                # 从 sitemap 提取 symbol 页面
                symbols = re.findall(r'/symbol/([A-Z]{1,5})', resp.text)
                trending_symbols = list(set(symbols))[:50]
        except:
            pass

        # 方式2：直接爬取几个知名热门股票页面
        if not trending_symbols:
            known_hot = ["AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "META", "GOOGL",
                         "AMD", "PLTR", "SOFI", "RIVN", "NIO", "AMC", "GME",
                         "SPY", "QQQ", "DXY", "SPX"]
            trending_symbols = known_hot

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        for sym in trending_symbols[:30]:
            try:
                resp = self.session.get(
                    f"https://stocktwits.com/symbol/{sym}",
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue

                # 从 HTML 中提取 JSON 数据
                # StockTwits 可能在 <script> 中嵌入数据
                body = resp.text
                # 提取消息文本
                messages = re.findall(
                    r'class="message-body"[^>]*>(.*?)</div>',
                    body, re.DOTALL
                )

                for msg_html in messages[:20]:
                    msg_text = re.sub(r'<[^>]+>', '', msg_html).strip()
                    if not msg_text:
                        continue
                    posts.append(Post(
                        source="stocktwits",
                        source_sub=sym,
                        title=msg_text[:100],
                        body=msg_text,
                        url=f"https://stocktwits.com/symbol/{sym}",
                        score=0, comments=0,
                        created_utc=None,  # 网页版难以提取时间
                        tickers_mentioned=[sym] + extract_from_text(msg_text),
                    ))

                time.sleep(0.5)

            except:
                continue

        logger.info(f"  StockTwits 网页: {len(posts)} 条消息（{len(trending_symbols)} 只股票）")
        return posts

    def _convert(self, msg: dict, default_sym: str = "") -> Post | None:
        """StockTwits 消息转 Post"""
        try:
            body = msg.get("body", "")
            if not body.strip():
                return None
            symbols = [s["symbol"] for s in msg.get("symbols", []) if s.get("symbol")]
            if not symbols and default_sym:
                symbols = [default_sym]
            return Post(
                source="stocktwits",
                source_sub=symbols[0] if symbols else "general",
                title=body[:120],
                body=body,
                url=msg.get("url", ""),
                score=msg.get("likes", {}).get("total", 0),
                comments=msg.get("comments_count", 0),
                created_utc=datetime.fromtimestamp(msg["created_at"], tz=timezone.utc),
                tickers_mentioned=symbols,
            )
        except:
            return None


TICKER_IN_TEXT = re.compile(r'(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])')
COMMON = {"IT", "TO", "BE", "IS", "IF", "MY", "WE", "US", "GO", "NO",
          "DO", "SO", "UP", "ON", "AT", "BY", "OR", "OF", "AS", "TV"}


def extract_from_text(text: str) -> list[str]:
    """辅助提取"""
    tickers = set()
    for t in TICKER_IN_TEXT.findall(text):
        if t not in COMMON and len(t) >= 2:
            tickers.add(t)
    return sorted(tickers)
