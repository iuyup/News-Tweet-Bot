# Auto-Tweet Agent

基于 **LangGraph** 的自主推文发布 Agent。每日多时段从 Reddit、HackerNews、arXiv、RSS 抓取热点，由 LLM 自主决策选题、撰写、审查，最终通过 Twitter/X API v2 自动发布英文推文。

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                      StateGraph                         │
│                                                         │
│   SourceRouter ──► Collector ──► Analyst                │
│       │                            │                    │
│    LLM选源                      LLM选题                  │
│    失败fallback                 失败降级                 │
│  [reddit,hn]                   (top6规则)               │
│                                    │                    │
│                          should_tweet=False             │
│                                    │ ──────────► END    │
│                                    │                    │
│                           ContentPlanner                │
│                            (内容策划)                    │
│                                    │                    │
│                    ┌──────────── Writer ◄──────┐        │
│                    │             │ LLM生成/修改 │        │
│                    │             ▼             │        │
│                    │          Reviewer         │        │
│                    │        LLM 5维评审         │       |
│                    │     score≥7.0 → 通过      │        │
│                    │     score<7.0 且          │        │
│                    │     revision<2 ───────────┘        │
│                    │     revision≥2 → 强制通过           │
│                    │             │                      │
│                    └──────────── ▼                      │
│                            Publisher                    │
│                   发布 → SQLite → Markdown → JSONL      │
└─────────────────────────────────────────────────────────┘
```

| 节点 | 职责 | LLM |
|------|------|-----|
| SourceRouter | 根据时间和账号定位动态选择信息源 | ✅ |
| Collector | 并发抓取多源，合并去重 | — |
| Analyst | 从原始新闻选出4-6条精华，决定是否发推（参考近7天历史去重） | ✅ |
| ContentPlanner | 按分类制定发推计划（politics/tech各几条） | — |
| Writer | 生成推文；修改模式下根据 Reviewer 反馈修改 | ✅ |
| Reviewer | 5维评审（≥7.0通过），未通过回流 Writer，最多2次 | ✅ |
| Publisher | 发布推文 + 写入 SQLite + 增量更新 Markdown + JSONL日志 | ✅（每日总结）|

## 技术栈

- **语言**：Python 3.10+，全面 `async/await`
- **Agent 框架**：LangGraph（StateGraph + MemorySaver checkpointing）
- **LLM**：DeepSeek（默认）、Anthropic Claude、MiniMax
- **信息源**：Reddit、HackerNews、arXiv、RSS（TechCrunch / The Verge）
- **发布**：Twitter/X API v2（tweepy，OAuth 1.0a）
- **持久化**：SQLite（推文历史 + 去重指纹）+ JSONL（运行日志）
- **调度**：APScheduler（支持多时间点 cron）
- **配置**：pydantic-settings + `.env`

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 `.env`

```env
# LLM（三选一或多选，默认 DeepSeek）
DEEPSEEK_API_KEY=your_key
ANTHROPIC_API_KEY=your_key

# Twitter/X API v2（必填）
TWITTER_API_KEY=your_key
TWITTER_API_SECRET=your_key
TWITTER_ACCESS_TOKEN=your_token
TWITTER_ACCESS_SECRET=your_token_secret

# 可选开关
USE_AGENT=true           # 启用 LangGraph Agent（推荐）
DRY_RUN=false            # true=不实际发推，用于测试
DEFAULT_LLM_PROVIDER=deepseek
SCHEDULE_HOURS=9,11,13,15,17,19,21,23   # 北京时间，每天执行时间点
SYNC_TARGET_DIR=D:/Documents/ObsidianVault/News  # Obsidian 同步目录（可选）
```

### 3. 运行

```bash
# 手动执行一次（立即发布）
python -m src.agent

# 干跑测试（不实际发推）
DRY_RUN=true python -m src.agent

# 启动自动调度器（按 SCHEDULE_HOURS 定时执行，进程常驻）
python -m src.scheduler.cron

# 服务器后台运行
nohup python -m src.scheduler.cron > data/logs/scheduler.log 2>&1 &
```

### 4. 运行测试

```bash
pytest tests/ -v        # 全部 63 个测试
```

## 信息源

| 源 | 内容 | 认证 |
|----|------|------|
| Reddit | r/worldnews, r/geopolitics, r/politics, r/technology, r/artificial, r/MachineLearning, r/singularity | 无需 |
| HackerNews | Firebase API Top Stories | 无需 |
| arXiv | cs.AI / cs.LG / cs.CL 最新论文 | 无需 |
| RSS | TechCrunch, The Verge | 无需 |

## 项目结构

```
src/
├── config.py               # 统一配置（pydantic-settings）
├── models/
│   └── news_item.py        # NewsItem, Category 数据模型
├── agent/                  # LangGraph Agent（主流程）
│   ├── __init__.py         # run_agent() 入口
│   ├── state.py            # TweetAgentState TypedDict
│   ├── graph.py            # StateGraph 组装
│   ├── _llm_call.py        # 共享 LLM 调用工具
│   └── nodes/              # 7 个节点实现
│       ├── source_router.py
│       ├── collector.py
│       ├── analyst.py
│       ├── content_planner.py
│       ├── writer.py
│       ├── reviewer.py
│       └── publisher.py
├── scrapers/               # 采集工具
│   ├── reddit_scraper.py
│   ├── hackernews_scraper.py
│   ├── arxiv_scraper.py
│   └── rss_scraper.py
├── processors/
│   └── filter.py           # 去重、过滤、分类
├── prompts/
│   └── templates.py        # 所有节点的 Prompt 模板
├── generator/
│   └── llm.py              # LLM 推文生成（旧 pipeline 复用）
├── publisher/
│   └── twitter.py          # Twitter/X API 封装
├── storage/
│   ├── db.py               # SQLite 持久化（推文历史 + 去重）
│   ├── daily_md.py         # 每日 Markdown 增量更新
│   └── summarizer.py       # LLM 每日总结生成
├── scheduler/
│   ├── workflow.py         # 旧 pipeline（保留，USE_AGENT=false 时使用）
│   └── cron.py             # APScheduler 定时调度
└── cli/
    ├── status.py           # 状态监控面板
    ├── backfill.py         # JSONL 历史数据回填到 SQLite
    └── save_daily.py       # 手动重建 Markdown

tests/
data/
├── cache/tweet_history.db  # SQLite 发布历史
├── logs/*.jsonl            # 每日运行日志
└── daily/*.md              # 每日推文 Markdown
```

## CLI 工具

```bash
# 状态监控（今日/累计发布数、token 统计、最近推文列表）
python -m src.cli.status
python -m src.cli.status --days 3

# JSONL 历史数据回填到 SQLite（首次部署时运行一次）
python -m src.cli.backfill
python -m src.cli.backfill --dry   # 只统计，不写入

# 从 JSONL 日志重建指定日期 Markdown
python -m src.cli.save_daily 2026-03-14
```

## 配置参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `USE_AGENT` | false | true = LangGraph Agent，false = 旧 pipeline |
| `DRY_RUN` | false | true = 不实际发推 |
| `DEFAULT_LLM_PROVIDER` | deepseek | claude / minimax / deepseek |
| `SCHEDULE_HOURS` | 9 | 北京时间执行时间点，逗号分隔（如 "9,11,13"） |
| `TWEETS_PER_RUN` | 2 | 每次运行生成推文数 |
| `ENABLED_SOURCES` | reddit,hackernews,arxiv,rss | SourceRouter 可选信息源 |
| `HACKERNEWS_LIMIT` | 20 | HN 抓取条数 |
| `ARXIV_QUERY` | cat:cs.AI OR cat:cs.LG OR cat:cs.CL | arXiv 搜索条件 |
| `SYNC_TARGET_DIR` | (空) | Obsidian Vault 同步目录 |

## 推文规范

- 严格 ≤ 280 字符（含 hashtag）
- 每条附 2-3 个相关 hashtag
- 时政类：客观呈现，不表达极端立场
- 科技类：突出技术影响或行业意义
- 结构化 JSON 输出，不做 regex 解析

## License

MIT
