#!/usr/bin/env python3
"""
多源股票论坛热议发现系统 v2
从 Reddit、StockTwits、SeekingAlpha、Yahoo Finance 等多个来源
抓取过去一周的热议股票，聚合评分后输出排行榜

用法:
    python main.py --short          # 简短榜单
    python main.py --json           # 同时导出 JSON
    python main.py --top 50         # 前 50 只
    python main.py --no-reddit      # 跳过某来源
    python main.py --debug          # 调试日志

Reddit API 凭证（可选，配置后可获得更多数据）:
    export REDDIT_CLIENT_ID=xxx
    export REDDIT_CLIENT_SECRET=xxx

StockTwits API 凭证（可选，注册 https://api.stocktwits.com/developers）:
    export STOCKTWITS_TOKEN=xxx
"""

import sys
import os
import json
import logging
from datetime import datetime
from pathlib import Path

import os
# 配置第三方库
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# 检测 feedparser
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

from sources.reddit_scraper import RedditScraper, PrawScraper
from sources.stocktwits_scraper import StockTwitsScraper
from sources.news_sources import RssNewsScraper
from analyzer import compute_ticker_scores, print_report, print_short_list, attach_company_names

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="多源股票论坛热议发现系统")
    parser.add_argument("--top", type=int, default=30, help="显示前 N 只股票 (默认 30)")
    parser.add_argument("--short", action="store_true", help="仅输出简短潜力股列表")
    parser.add_argument("--json", action="store_true", help="导出 JSON 报告")
    parser.add_argument("--no-reddit", action="store_true", help="跳过 Reddit")
    parser.add_argument("--no-stocktwits", action="store_true", help="跳过 StockTwits")
    parser.add_argument("--no-news", action="store_true", help="跳过新闻源")
    parser.add_argument("--no-google", action="store_true", help="跳过 Google News")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    all_posts = []
    sources_used = []

    # ========== 1. Reddit ==========
    if not args.no_reddit:
        logger.info("🌐 [1/3] 正在抓取 Reddit...")
        # 优先使用 PRAW（需配置凭证）
        client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        try:
            if client_id and client_secret:
                scraper = PrawScraper(
                    client_id=client_id,
                    client_secret=client_secret,
                    max_posts_per_sub=100,
                )
            else:
                scraper = RedditScraper(max_posts_per_sub=50)
            posts = scraper.fetch_last_week()
            logger.info(f"  ✅ Reddit: {len(posts)} 条帖子")
            all_posts.extend(posts)
            sources_used.append("Reddit")
        except Exception as e:
            logger.error(f"  ❌ Reddit 抓取失败: {e}")

    # ========== 2. StockTwits ==========
    if not args.no_stocktwits:
        logger.info("🌐 [2/3] 正在抓取 StockTwits...")
        try:
            token = os.environ.get("STOCKTWITS_TOKEN", "")
            scraper = StockTwitsScraper(access_token=token)
            posts = scraper.fetch_last_week()
            if posts:
                logger.info(f"  ✅ StockTwits: {len(posts)} 条消息")
                all_posts.extend(posts)
                sources_used.append("StockTwits")
            else:
                logger.info("  ℹ️  StockTwits 无数据（可配置 API Token 获取更多）")
        except Exception as e:
            logger.warning(f"  ⚠️ StockTwits: {e}")

    # ========== 3. 新闻 / RSS 源 ==========
    if not args.no_news:
        logger.info("🌐 [3/3] 正在抓取新闻源（SeekingAlpha/Yahoo/InvestorPlace等）...")
        try:
            if not HAS_FEEDPARSER:
                logger.warning("  ⚠️ feedparser 未安装，跳过新闻源")
            else:
                scraper = RssNewsScraper()
                posts = scraper.fetch_last_week()
                logger.info(f"  ✅ 新闻源: {len(posts)} 条文章")
                all_posts.extend(posts)
                sources_used.append("News")
        except Exception as e:
            logger.error(f"  ❌ 新闻源抓取失败: {e}")

    # ========== 统计概览 ==========
    if not all_posts:
        print("\n⚠️  所有来源均未获取到数据。")
        print("   请检查网络连接或尝试: python main.py --debug")
        return

    posts_with_tickers = [p for p in all_posts if p.tickers_mentioned]
    total_ticker_mentions = sum(len(p.tickers_mentioned) for p in posts_with_tickers)

    print(f"\n{'='*60}")
    print(f"📊 获取概况")
    print(f"{'='*60}")
    print(f"  来源: {', '.join(sources_used)}")
    print(f"  总帖/文: {len(all_posts)}")
    print(f"  含股票代码: {len(posts_with_tickers)}")
    print(f"  总提及次数: {total_ticker_mentions}")

    # ========== 综合评分 ==========
    scores = compute_ticker_scores(all_posts)

    # ========== 查询公司名称（只查前 top_n 名） ==========
    query_n = min(args.top, 15) if args.short else args.top
    logger.info(f"🔍 正在查询前 {query_n} 名公司名称...")
    scores = attach_company_names(scores, top_n=query_n)

    # ========== 输出 ==========
    print()
    if args.short:
        report = print_short_list(scores, top_n=min(args.top, 15))
    else:
        report = print_report(scores, top_n=args.top)
    print(report)

    # ========== JSON 导出 ==========
    if args.json:
        output_dir = BASE_DIR / "output"
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_dir / f"ticker_report_{timestamp}.json"
        with open(json_path, "w") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "sources_used": sources_used,
                "total_posts": len(all_posts),
                "posts_with_tickers": len(posts_with_tickers),
                "total_ticker_mentions": total_ticker_mentions,
                "top_tickers": scores[:args.top],
            }, f, indent=2, ensure_ascii=False)
        logger.info(f"📁 JSON 报告已导出: {json_path}")


if __name__ == "__main__":
    main()
