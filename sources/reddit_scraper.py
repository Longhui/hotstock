"""
Reddit 数据抓取
- 方案 A：Pushshift API（免费、无需凭证，需控制频率）
- 方案 B：Reddit RSS（免费、无需凭证，轻量）
- 方案 C：PRAW（需 Reddit App 凭证，质量最高）

环境变量配置 PRAW（可选）：
  export REDDIT_CLIENT_ID=xxx
  export REDDIT_CLIENT_SECRET=xxx
"""

import re
import time
import requests
import feedparser
import logging
from datetime import datetime, timedelta, timezone
from sources import BaseScraper, Post

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}

# ========== Ticker 提取（严格版 v2） ==========

DOLLAR_TICKER = re.compile(r'\$([A-Z]{1,5})')
TICKER_PATTERN = re.compile(r'(?<![A-Za-z$])[A-Z]{2,5}(?![A-Za-z])')

# 常见英文单词（2-5个大写字母，容易误认为 ticker）
COMMON_WORDS = {
    "IT", "TO", "BE", "BY", "OR", "OF", "AS", "IS", "ME", "MY", "WE",
    "US", "GO", "NO", "SO", "UP", "DO", "IF", "AM", "AN", "HE", "HI",
    "TV", "ALL", "ARE", "FOR", "HAS", "HAD", "NOT", "NOW", "OUT", "SEE",
    "THE", "WAS", "WON", "YOU", "BIG", "NEW", "GET", "LOT", "TOP", "MAN",
    "CUT", "SET", "RUN", "LED", "AGE", "KEY", "FUN", "FAR", "HOT", "RED",
    "BET", "GOT", "PAY", "LOW", "PUT", "WAY", "DUE", "ANY", "OWN", "OLD",
    "MOM", "DAD", "BRO", "HIS", "HER", "OUR", "DAY", "YEAR", "MONTH",
    "OPEN", "CLOSE", "HIGH", "LONG", "SHORT", "EARN", "SALE", "CASH",
    "DEBT", "RISK", "BANK", "LUCK", "FREE", "PASS", "FAIL", "WORK",
    "LIFE", "MORE", "LESS", "OVER", "UNDER", "BULL", "BEAR", "NEXT",
    "LAST", "FIRST", "BACK", "DOWN", "MOVE", "TRUE", "REAL", "GOOD",
    "BEST", "SAFE", "HUGE", "WIDE", "DARK", "SOON", "LATE", "FAST",
    "HARD", "SOFT", "KEEP", "HOLD", "SHOW", "TURN", "CALL", "GIVE",
    "TAKE", "KNOW", "HELP", "FIND", "NEED", "STAY", "WANT", "LIKE",
    "LOVE", "HATE", "EVEN", "STILL", "ALSO", "JUST", "ONLY", "VERY",
    "THAN", "THAT", "THIS", "WITH", "FROM", "BEEN", "HAVE", "WERE",
    "SAYS", "SOME", "EACH", "BOTH", "MADE", "DOES", "DONE", "USED",
    "SURE", "MAIN", "WELL", "ELSE", "EDIT", "IMO", "TLDR",
    # 行业黑话
    "WEEK", "YTD", "ATH", "CAD", "USD", "EUR", "GBP", "JPY",
    "BUY", "SELL", "DIV", "YIELD", "BOND", "YOY",
    # 语境词：新闻中几乎总指"人工智能"而非 C3.ai
    "AI",
}

# 金融/机构/指数名称（不是可交易股票，或不是我们要关注的）
FINANCIAL_NON_TICKER = {
    "NY", "NYSE", "NASDAQ", "AMEX", "OTC", "OTCQB", "OTCMKTS", "NYSEARCA",
    "SEC", "FDA", "CPI", "PPI", "AUM", "GDP", "IPO",
    "PE", "ROE", "ROI", "EPS", "MACD", "RSI", "ROA", "ROCE",
    "CEO", "CFO", "CTO", "COO", "EV", "EBITDA", "EBIT",
    "CNBC", "FTSE", "SPX", "VIX", "DXY",
    "BTC", "ETH", "USDT", "USDC", "SOL", "XRP",
    "USA", "UK", "EU", "FOMC", "TTM", "PEG", "SMA", "EMA", "FCF",
    "ADR", "DCF", "OPEC", "NAND", "P/E", "PEG", "YOY",
    "OTC", "NYSE", "IPO", "ETF", "REIT", "M&A",
    "DRAM", "WSB", "GPU", "COVID", "WATCH", "IRA", "SK", "APY",
}

def extract_tickers(text: str) -> list[str]:
    """从文本中提取可能的股票代码"""
    if not text:
        return []
    tickers = set()

    # 1. $TICKER 格式 — 最高可信度，保留所有（除非明确是金融术语）
    for t in DOLLAR_TICKER.findall(text):
        if t not in FINANCIAL_NON_TICKER:
            tickers.add(t)

    # 2. 非 $ 前缀的 2-5个大写字母 — 严格过滤
    for t in TICKER_PATTERN.findall(text):
        if t in COMMON_WORDS:
            continue
        if t in FINANCIAL_NON_TICKER:
            continue
        tickers.add(t)

    return sorted(tickers)


# Reddit 子版块
SUBREDDITS = [
    "ValueInvesting", "SecurityAnalysis",
    "investing", "stocks", "StockMarket",
    "dividends", "REITs", "biotech_stocks",
    "wallstreetbets", "pennystocks", "smallstreetbets",
    "thetagang", "SPACs",
]


# ========== 方案 A：Pushshift API ==========

class RedditScraper(BaseScraper):
    """
    Pushshift API 抓取
    免费、无需凭证，但需控制请求频率
    """

    def __init__(self, max_posts_per_sub=30):
        self.max_posts_per_sub = max_posts_per_sub
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @property
    def name(self):
        return "Reddit"

    def fetch_last_week(self) -> list[Post]:
        since = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
        all_posts = []

        for sub in SUBREDDITS:
            try:
                posts = self._fetch_sub(sub, since)
                if posts:
                    logger.info(f"  r/{sub}: {len(posts)} 条")
                all_posts.extend(posts)
                time.sleep(1.2)  # Pushshift 限流
            except Exception as e:
                logger.warning(f"r/{sub}: {e}")

        # 如果 Pushshift 没拿到数据，尝试 RSS
        if not all_posts:
            logger.info("Pushshift 无数据，尝试 RSS...")
            return RssFallback(max_posts_per_sub=self.max_posts_per_sub).fetch_last_week()

        return all_posts

    def _fetch_sub(self, sub: str, since: int) -> list[Post]:
        posts = []
        after = since

        for page in range(2):  # 最多2页
            try:
                resp = self.session.get(
                    "https://api.pullpush.io/reddit/submission/search/",
                    params={
                        "subreddit": sub,
                        "after": after,
                        "size": 100,
                        "sort": "desc",
                        "sort_type": "score",
                        "fields": "id,title,selftext,url,subreddit,score,num_comments,created_utc",
                    },
                    timeout=25,
                )
                if resp.status_code == 429:
                    logger.debug(f"  Pushshift 429, 等待 5s...")
                    time.sleep(5)
                    continue
                if resp.status_code != 200:
                    break

                data = resp.json().get("data", [])
                if not data:
                    break

                for item in data:
                    body = (item.get("title") or "") + " " + (item.get("selftext") or "")
                    posts.append(Post(
                        source="reddit", source_sub=sub,
                        title=item.get("title", ""),
                        body=item.get("selftext") or "",
                        url=f"https://reddit.com/r/{sub}/comments/{item['id']}",
                        score=item.get("score", 0),
                        comments=item.get("num_comments", 0),
                        created_utc=datetime.fromtimestamp(item["created_utc"], tz=timezone.utc),
                        tickers_mentioned=extract_tickers(body),
                    ))

                after = data[-1]["created_utc"]
                time.sleep(1.5)

                if len(posts) >= self.max_posts_per_sub:
                    break

            except Exception as e:
                logger.debug(f"  Pushshift page error: {e}")
                break

        return posts


# ========== 方案 B：Reddit RSS ==========

class RssFallback(BaseScraper):
    """Reddit RSS 抓取（备选）"""

    def __init__(self, max_posts_per_sub=20):
        self.max_posts_per_sub = max_posts_per_sub

    @property
    def name(self):
        return "Reddit"

    def fetch_last_week(self) -> list[Post]:
        all_posts = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        for sub in SUBREDDITS:
            try:
                posts = self._fetch_rss(sub, cutoff)
                if posts:
                    logger.info(f"  RSS/r/{sub}: {len(posts)} 条")
                all_posts.extend(posts)
            except Exception as e:
                logger.debug(f"RSS/r/{sub}: {e}")

        return all_posts

    def _fetch_rss(self, sub: str, cutoff: datetime) -> list[Post]:
        posts = []
        url = f"https://www.reddit.com/r/{sub}/.rss"
        try:
            resp = requests.get(url, timeout=15, headers=HEADERS)
            if resp.status_code != 200:
                return []
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
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
                link = entry.get("link", "")
                id_part = link.split("/comments/")[-1].split("/")[0] if "/comments/" in link else ""

                posts.append(Post(
                    source="reddit", source_sub=sub,
                    title=title, body=summary,
                    url=link or f"https://reddit.com/r/{sub}/.rss",
                    score=0, comments=0,
                    created_utc=created,
                    tickers_mentioned=extract_tickers(body),
                ))
                if len(posts) >= self.max_posts_per_sub:
                    break
        except Exception as e:
            logger.debug(f"RSS error r/{sub}: {e}")
        return posts


# ========== 方案 C：PRAW（推荐） ==========

class PrawScraper(BaseScraper):
    """PRAW（需 Reddit API 凭证）"""

    def __init__(self, client_id="", client_secret="", user_agent="stock_scanner/1.0",
                 max_posts_per_sub=100):
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.max_posts_per_sub = max_posts_per_sub
        self._reddit = None

    @property
    def name(self):
        return "Reddit"

    def _init(self) -> bool:
        if self._reddit is not None:
            return True
        try:
            import praw
            self._reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent,
            )
            self._reddit.user.me()
            return True
        except Exception as e:
            logger.warning(f"PRAW 初始化失败: {e}")
            return False

    def fetch_last_week(self) -> list[Post]:
        if not self._init():
            return RedditScraper(max_posts_per_sub=self.max_posts_per_sub).fetch_last_week()

        all_posts = []
        for sub_name in SUBREDDITS:
            try:
                sub = self._reddit.subreddit(sub_name)
                count = 0
                for post in sub.top(time_filter="week", limit=self.max_posts_per_sub):
                    body = (post.title or "") + " " + (post.selftext or "")
                    all_posts.append(Post(
                        source="reddit", source_sub=sub_name,
                        title=post.title or "",
                        body=post.selftext or "",
                        url=f"https://reddit.com/r/{sub_name}/comments/{post.id}",
                        score=post.score or 0, comments=post.num_comments or 0,
                        created_utc=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                        tickers_mentioned=extract_tickers(body),
                    ))
                    count += 1
                logger.info(f"  r/{sub_name}: {count} 条")
            except Exception as e:
                logger.warning(f"r/{sub_name}: {e}")
        return all_posts
