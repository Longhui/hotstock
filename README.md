# Reddit 热门股票 · 巴菲特 Checklist 分析系统

多源股票论坛热议发现 + 巴菲特价值投资买入前 Checklist 自动化分析流水线。

## 系统架构

```
Reddit RSS / Pushshift / PRAW  ──┐
StockTwits API                   ──┤──→ main.py（热门股发现）
SeekingAlpha/Yahoo/GoogleNews    ──┘
                                        ↓
                                  热门股票排行榜（JSON）
                                        ↓
                                  ┌─ investment-checklist.py（六关评分）
                                  │   - 能力圈 / 好生意 / 护城河
                                  │   - 管理层 / 安全边际 / 决策纪律
                                  │   - 近期重大事件
                                  │   - 三情景估值（financial_rigor）
                                  │
                                  └─ run-complete-analysis.py（综合报告）
```

## 快速开始

```bash
# 1. 激活虚拟环境
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt feedparser

# 3. 抓取 Reddit 热门股票 + 生成排行榜
python main.py --short --top 20 --json
```

### 抓取 Reddit 热门股

```bash
# 简短榜单
python main.py --short --top 30

# 完整榜单
python main.py --top 50

# 仅某个来源
python main.py --no-news --no-stocktwits

# 导出 JSON
python main.py --short --top 20 --json

# 调试模式
python main.py --debug
```

> **Reddit API 凭证**（可选，配置后可获得更多数据）：
> ```bash
> export REDDIT_CLIENT_ID=xxx
> export REDDIT_CLIENT_SECRET=xxx
> ```

### 巴菲特 Checklist 单公司分析

```bash
source venv/bin/activate

# 单公司
python investment-checklist.py AAPL

# 多公司对比
python investment-checklist.py AAPL MSFT GOOGL

# 仅输出到终端（不保存文件）
python investment-checklist.py --no-save AAPL

# 输出到指定文件
python investment-checklist.py --output my-report.md AAPL
```

报告自动保存到 `~/巴菲特Checklist/` 目录。

### 完整分析流水线（推荐）

基于已有的 Reddit 热搜数据，对热门股批量执行 Checklist 分析：

```bash
source venv/bin/activate

# 先抓取数据
python main.py --short --top 20 --json

# 再执行分析（使用最新的 JSON 数据）
python run-complete-analysis.py

# 指定分析前 N 只
python run-complete-analysis.py --top 8

# 指定数据源
python run-complete-analysis.py --json output/ticker_report_20260717_123327.json

# 指定输出路径
python run-complete-analysis.py --output my-report.md
```

输出综合报告到 `output/巴菲特Checklist-Reddit综合报告_*.md`。

### RSS 实时抓取版（备选）

Reddit 限流时的轻量版，直接用 RSS 抓取：

```bash
source venv/bin/activate
python reddit-checklist-pipeline.py
python reddit-checklist-pipeline.py --top 5
python reddit-checklist-pipeline.py --subs "ValueInvesting,stocks,investing"
```

## 六关评分体系

| 关卡 | 评分维度 | 评分范围 | 权重 |
|------|---------|:--------:|:----:|
| 第一关：能力圈 | 商业模式可理解性、10年确定性 | ★1-5 | 业务越简单评分越高 |
| 第二关：好生意 | ROE、毛利率、FCF、资本效率、负债 | ★1-5 | 5项达标=5★ |
| 第三关：护城河 | 品牌、转换成本、网络效应、规模、技术 | ★1-5 | 多重护城河+趋势 |
| 第四关：管理层 | 内部人持股、股东回馈、盈利质量、治理 | ★1-5 | 利益一致性 |
| 第五关：安全边际 | PE/PB/FCF Yield + 三情景估值 | ★1-5 | PE越低评分越高 |
| 第六关：决策纪律 | FOMO检查、容错空间 | ★1-5 | 无信号=5★ |

**总体结论**：
- ✅ **通过**（≥4关通过）— 可进入深度研究
- ❓ **灰色地带**（3关通过）— 需投资者自行判断
- ❌ **未通过**（<3关通过）— 多项核心指标不达标

## 数据来源

| 来源 | 方式 | 是否需要凭证 |
|------|------|:----------:|
| Reddit | Pushshift API / RSS / PRAW | 可选（PRAW需要） |
| SeekingAlpha | RSS | 否 |
| Yahoo Finance | RSS + yfinance API | 否 |
| NASDAQ | RSS | 否 |
| InvestorPlace | RSS | 否 |
| Google News | RSS | 否 |
| StockTwits | API | 可选 |

## 精确计算引擎

`tools/financial_rigor.py` 提供金融数据的精确十进制计算：

```bash
# 估值指标验算
python tools/financial_rigor.py verify-valuation \
  --price 333 --eps 8.24 --bvps 45 --fcf-per-share 12

# 三情景估值
python tools/financial_rigor.py three-scenario \
  --price 333 --eps 8.24 --shares 9.1e9 \
  --growth 0.25 0.17 0.05 --pe 25 20 15

# 市值验算
python tools/financial_rigor.py verify-market-cap \
  --price 333 --shares 9.1e9 --reported 4.9e12 --currency USD
```

## 项目结构

```
├── main.py                        # 多源热门股发现系统（入口）
├── run-complete-analysis.py       # ★ 完整分析流水线（推荐）
├── reddit-checklist-pipeline.py   # RSS实时抓取分析版
├── investment-checklist.py        # 巴菲特Checklist分析引擎
├── analyzer.py                    # 跨源评分引擎
├── company_names.py               # 公司名称查询
├── run.sh                         # 快捷运行脚本
├── requirements.txt               # Python依赖
├── sources/
│   ├── __init__.py                # Post/BaseScraper 数据模型
│   ├── reddit_scraper.py          # Reddit抓取器（Pushshift/RSS/PRAW）
│   ├── stocktwits_scraper.py      # StockTwits抓取器
│   └── news_sources.py            # 新闻RSS抓取器
├── tools/
│   └── financial_rigor.py         # 精确十进制计算工具
└── output/                        # 报告输出目录
```

## 输出示例

```
📊 Reddit 热门股 · 巴菲特 Checklist 综合评分
======================================================================
  # Ticker 公司                      总分   结论
----------------------------------------------------------------------
  1    IBM International Busi     16/25 ✅ 通过
  2   NVDA NVIDIA Corporation     16/25 ✅ 通过
  3   NFLX Netflix, Inc.          15/25 ✅ 通过
  4  GOOGL Alphabet Inc.          17/25 ✅ 通过
  5    TSM Taiwan Semiconduct     16/25 ✅ 通过
  6   ASML ASML Holding N.V.      15/25 ❓ 灰色
  7    AMD Advanced Micro Dev     12/25 ❌ 未通过
```

## 免责声明

本系统为自动化分析工具，**不构成投资建议**。投资决策需结合个人研究和判断。

*"投资的第一条规则是不要亏损。第二条规则是不要忘记第一条。" — 沃伦·巴菲特*
