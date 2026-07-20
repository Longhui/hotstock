"""基础抓取器"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Post:
    """统一帖子数据模型"""
    source: str          # 来源: reddit, stocktwits, seekingalpha, ...
    source_sub: str      # 子版块/频道/分类
    title: str
    body: str            # 帖文正文或评论拼接
    url: str
    score: int = 0       # 点赞/热度分
    comments: int = 0    # 评论数
    created_utc: Optional[datetime] = None
    tickers_mentioned: list = field(default_factory=list)


class BaseScraper(ABC):
    """每个数据源继承此类"""

    @abstractmethod
    def fetch_last_week(self) -> list[Post]:
        """获取过去一周的热议帖子"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称"""
        ...
