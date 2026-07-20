"""
新闻/文章源抓取
组合多个 RSS 和可爬取的新闻源提取热门股票
"""

import re
import feedparser
import requests
import logging
from datetime import datetime, timezone, timedelta
from sources import BaseScraper, Post
from sources.reddit_scraper import extract_tickers

logger = logging.getLogger(__name__)

# ========== RSS 源配置 ==========

RSS_FEEDS = {
    "SeekingAlpha": "https://seekingalpha.com/feed.xml",
    "InvestorPlace": "https://investorplace.com/feed/",
    "NASDAQ": "https://www.nasdaq.com/feed/rssoutbound",
}

# Yahoo Finance 通过 RSS 搜索多个关键词
YAHOO_RSS_QUERIES = [
    "markets", "stocks", "earnings",
    "ipo", "stock-market", "investing",
]


class RssNewsScraper(BaseScraper):
    """聚合 RSS 源抓取"""

    def __init__(self):
        self.seen_urls = set()

    @property
    def name(self):
        return "News"

    def fetch_last_week(self) -> list[Post]:
        all_posts = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        for source_name, feed_url in RSS_FEEDS.items():
            try:
                posts = self._parse_feed(source_name, feed_url, cutoff)
                if posts:
                    logger.info(f"  {source_name}: {len(posts)} 条")
                all_posts.extend(posts)
            except Exception as e:
                logger.warning(f"{source_name}: {e}")

        # Yahoo Finance
        for q in YAHOO_RSS_QUERIES:
            try:
                url = f"https://finance.yahoo.com/news/rss/{q}"
                posts = self._parse_feed("YahooFinance", url, cutoff)
                if posts:
                    logger.info(f"  YahooFinance/{q}: {len(posts)} 条")
                all_posts.extend(posts)
            except Exception as e:
                logger.warning(f"YahooFinance/{q}: {e}")

        # Google News 股票相关
        try:
            gnp = self._fetch_google_news(cutoff)
            if gnp:
                logger.info(f"  GoogleNews: {len(gnp)} 条")
            all_posts.extend(gnp)
        except Exception as e:
            logger.warning(f"GoogleNews: {e}")

        return all_posts

    def _parse_feed(self, source: str, feed_url: str, cutoff: datetime) -> list[Post]:
        """解析 RSS feed"""
        posts = []
        resp = requests.get(feed_url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []

        feed = feedparser.parse(resp.content)

        for entry in feed.entries:
            url = entry.get("link", "")
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)

            # 发布时间
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            created = None
            if published:
                try:
                    created = datetime(*published[:6], tzinfo=timezone.utc)
                except:
                    pass
            if created and created < cutoff:
                continue

            title = entry.get("title", "")
            summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
            summary = re.sub(r'<[^>]+>', '', summary_raw)

            body = f"{title} {summary}"
            tickers = extract_tickers(body)

            # 提取来源子分类
            cats = entry.get("tags", [])
            source_sub = cats[0].get("term", "news") if cats else "news"

            posts.append(Post(
                source=source,
                source_sub=source_sub,
                title=title,
                body=summary,
                url=url,
                score=0, comments=0,
                created_utc=created,
                tickers_mentioned=tickers,
            ))
        return posts

    def _fetch_google_news(self, cutoff: datetime) -> list[Post]:
        """Google News RSS"""
        posts = []
        queries = [
            "stock market today",
            "hot stocks",
            "value stocks",
            "undervalued stocks",
            "stock picks",
            "best stocks to buy",
        ]
        for q in queries:
            params = {
                "q": f"{q} stock OR shares OR ticker",
                "hl": "en-US", "gl": "US",
                "ceid": "US:en", "num": 30,
            }
            try:
                resp = requests.get(
                    "https://news.google.com/rss/search",
                    params=params, timeout=15,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code != 200:
                    continue
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    url = entry.get("link", "")
                    if url in self.seen_urls:
                        continue
                    self.seen_urls.add(url)

                    published = entry.get("published_parsed")
                    created = None
                    if published:
                        try:
                            created = datetime(*published[:6], tzinfo=timezone.utc)
                        except:
                            pass
                    if created and created < cutoff:
                        continue

                    title = entry.get("title", "")
                    summary_raw = entry.get("summary", "") or ""
                    summary = re.sub(r'<[^>]+>', '', summary_raw)
                    body = f"{title} {summary}"
                    tickers = extract_tickers(body)

                    posts.append(Post(
                        source="GoogleNews",
                        source_sub=q,
                        title=title,
                        body=summary,
                        url=url,
                        score=0, comments=0,
                        created_utc=created,
                        tickers_mentioned=tickers,
                    ))
            except:
                continue
        return posts
