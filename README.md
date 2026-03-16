# News Tweet Bot

自动化推文生成机器人，每日抓取时政与科技热点，由 LLM 撰写英文推文并发布到 Twitter/X。

## 功能

- **数据抓取**：从 Reddit 热帖（7 个目标 subreddit）、Nitter 热搜（7 个备用实例）获取热点
- **内容过滤**：去重、分类（时政/科技）、质量筛选
- **AI 生成**：调用 LLM（默认 DeepSeek）生成符合规范的英文推文（≤280字符，含 hashtag）
- **自动发布**：通过 Twitter API v2 自动发布推文
- **定时调度**：每日北京时间 9:00 自动执行（可配置多个时间点）
- **Markdown 生成**：每日自动生成 Markdown 文件，支持增量更新
- **Obsidian 同步**：支持同步到 Obsidian Vault

## 技术栈

- Python 3.10+ (async/await)
- LLM: DeepSeek（默认）、Anthropic Claude、MiniMax
- Twitter/X API v2 (tweepy OAuth 1.0a)
- APScheduler
- httpx / pydantic / beautifulsoup4

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd news-tweet-bot
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 并填入密钥：

```
ANTHROPIC_API_KEY=your_anthropic_api_key
TWITTER_API_KEY=your_twitter_api_key
TWITTER_API_SECRET=your_twitter_api_secret
TWITTER_ACCESS_TOKEN=your_twitter_access_token
TWITTER_ACCESS_SECRET=your_twitter_access_secret
```

### 4. 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定模块
pytest tests/test_scrapers.py -v
pytest tests/test_filters.py -v
```

### 5. 手动运行工作流

```bash
python -c "import asyncio; from src.scheduler.workflow import run_workflow; asyncio.run(run_workflow())"
```

### 6. 启动调度器

```bash
python -m src.scheduler.cron
```

## 工作流设计

每日自动化流程（`scheduler/workflow.py`）：

1. **抓取**：并发抓取 Reddit（7 个目标 subreddit）和 Nitter（7 备用实例）
2. **去重 + 过滤**：去除已发布和批次内重复，过滤 UNKNOWN 分类，按热度排序
3. **生成**：调用 LLM（默认 DeepSeek）按分类（POLITICS/TECH）生成英文推文
4. **发布**：逐条发布推文，**每条推文发送成功后立即增量更新 Markdown**
5. **总结**：LLM 生成每日新闻摘要，追加到 Markdown
6. **日志**：写入 JSONL 日志（含 token 用量）

## 抓取源

### Reddit 目标子版

| Subreddit | 分类 |
|-----------|------|
| r/worldnews | 时政 |
| r/geopolitics | 时政 |
| r/politics | 时政 |
| r/technology | 科技 |
| r/artificial | 科技 |
| r/MachineLearning | 科技 |
| r/singularity | 科技 |

### Nitter 备用实例

共 7 个备用实例轮询：
- nitter.privacydev.net
- nitter.poast.org
- nitter.lucahammer.com
- nitter.rawbit.ch
- nitter.kyoko.jp
- nitter.bus-hit.me
- nitter.esmailelbob.xyz

## 推文生成规范

- 长度严格 ≤ 280 字符（含 hashtag）
- 风格：简洁、客观、吸引眼球，适合英文受众
- 每条推文附 2~3 个相关 hashtag
- 时政类：中立客观，避免极端立场
- 科技类：突出技术亮点或行业影响

## 项目结构

```
src/
├── config.py           # 统一配置（pydantic-settings）
├── models/
│   └── news_item.py   # 数据模型（NewsItem, Category）
├── scrapers/
│   ├── nitter_scraper.py   # Nitter 热搜抓取
│   └── reddit_scraper.py   # Reddit 热帖抓取
├── processors/
│   └── filter.py      # 内容过滤、去重、缓存标记
├── prompts/
│   └── templates.py   # 推文生成 Prompt 模板
├── generator/
│   └── llm.py         # LLM 推文生成（支持 Claude/DeepSeek/MiniMax）
├── publisher/
│   └── twitter.py     # Twitter/X API 发布
├── scheduler/
│   ├── workflow.py    # 每日工作流核心
│   └── cron.py        # APScheduler 定时调度
├── storage/
│   ├── daily_md.py    # Markdown 文件生成（增量更新）
│   └── summarizer.py  # LLM 每日总结生成
└── cli/
    └── save_daily.py  # 手动重建 Markdown 工具

tests/                  # 单元测试
data/
├── cache/             # 已发布新闻指纹缓存
├── logs/              # 每日 JSONL 运行日志
└── daily/             # 每日 Markdown 文件
```

## 配置选项

在 `.env` 中可自定义：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_LLM_PROVIDER` | deepseek | LLM 提供商 (claude/minimax/deepseek) |
| `DEEPSEEK_API_KEY` | (可选) | DeepSeek API 密钥 |
| `DEEPSEEK_MODEL` | deepseek-chat | DeepSeek 模型 |
| `ANTHROPIC_API_KEY` | (必填) | Anthropic API 密钥 |
| `CLAUDE_MODEL` | claude-sonnet-4-6 | Claude 模型 |
| `MINIMAX_API_KEY` | (可选) | MiniMax API 密钥 |
| `MINIMAX_MODEL` | MiniMax-M2.5 | MiniMax 模型 |
| `TWITTER_API_KEY` | (必填) | Twitter API Key |
| `TWITTER_API_SECRET` | (必填) | Twitter API Secret |
| `TWITTER_ACCESS_TOKEN` | (必填) | Twitter Access Token |
| `TWITTER_ACCESS_SECRET` | (必填) | Twitter Access Secret |
| `REDDIT_LIMIT_PER_SUB` | 10 | 每个 Reddit 子版抓取数量 |
| `TWEETS_PER_RUN` | 2 | 每次运行生成推文数 |
| `SCHEDULE_HOURS` | 9 | 调度时间（北京时间，可多个如 "9,11,13"） |
| `SCHEDULE_MINUTE` | 0 | 调度时间（分钟） |
| `DRY_RUN` | false | 测试模式（不实际发布） |
| `NITTER_INSTANCES` | 7 个实例 | Nitter 备用实例列表 |
| `SYNC_TARGET_DIR` | (可选) | Obsidian Vault 同步目录 |

## CLI 工具

### 手动重建 Markdown

```bash
# 从 JSONL 日志重建指定日期的 Markdown 文件
python -m src.cli.save_daily 2026-03-14

# 默认今天
python -m src.cli.save_daily
```

## 存储与日志

- **JSONL 日志**：记录每条推文的完整信息（来源、token 用量、发布状态）
- **Markdown 增量更新**：每条推文发布成功后立即更新当天文件
- **缓存指纹**：已发布新闻标题 SHA1 指纹，防止重复发布

## 测试

```bash
pytest tests/ -v
```

## License

MIT
