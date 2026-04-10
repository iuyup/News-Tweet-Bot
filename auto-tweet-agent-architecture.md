# Auto-Tweet Agent 系统架构设计文档

## 项目定位

将现有的"爬取→改写→发推"线性 pipeline 升级为基于 LangGraph 的自主决策 Agent 系统。系统能够自主完成信息采集、内容决策、质量把控和定时发布的完整闭环，具备多步推理、工具调用、自我反思和动态路由能力。

---

## 一、整体架构概览

### 状态图（State Graph）

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                           ▼
                ┌──────────────────┐
                │  1. SourceRouter │  (决定从哪些渠道采集)
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │  2. Collector    │  (多源并发采集)
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │  3. Analyst      │  (筛选+去重+判断价值)
                └────────┬─────────┘
                         │
                    ┌────┴────┐
                    │ 有价值?  │
                    └────┬────┘
                   No/   │  \Yes
                  ┌──┘   │   └──┐
                  ▼      │      ▼
             ┌────────┐  │  ┌──────────────────┐
             │  END   │  │  │  4. ContentPlanner│ (决定发什么角度/风格)
             └────────┘  │  └────────┬─────────┘
                         │           │
                         │           ▼
                         │  ┌──────────────────┐
                         │  │  5. Writer        │ (撰写推文)
                         │  └────────┬─────────┘
                         │           │
                         │           ▼
                         │  ┌──────────────────┐
                         │  │  6. Reviewer      │ (自我审查+评分)
                         │  └────────┬─────────┘
                         │           │
                         │      ┌────┴────┐
                         │      │ 通过?   │
                         │      └────┬────┘
                         │     No/   │  \Yes
                         │    ┌──┘   │   └──┐
                         │    ▼      │      ▼
                         │ (回到     │  ┌──────────────────┐
                         │  Writer,  │  │  7. Publisher     │ (调用X API发布)
                         │  最多3次) │  └────────┬─────────┘
                         │           │           │
                         │           │           ▼
                         │           │      ┌────────┐
                         │           │      │  END   │
                         │           │      └────────┘
                         │           │
                         └───────────┘
```

### 节点流程总览

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
|
│                          should_tweet=False             │
│                                    │ ──────────► END    │
│                                    │                    │
│                           ContentPlanner                │
│                            (规则式配比)                  │
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

### 核心设计原则

1. **每个节点是一个独立的 Agent 函数**，接收 State，返回更新后的 State
2. **条件边（conditional edges）实现动态路由**，不是硬编码的线性流程
3. **State 是唯一的数据传递方式**，所有节点通过读写 State 通信
4. **Reflection Loop** 实现自我修正，Writer 和 Reviewer 之间最多循环 3 次

---

## 二、State 定义

这是整个系统的核心数据结构，所有节点共享：

```python
from typing import TypedDict, Literal
from dataclasses import dataclass, field

class TweetAgentState(TypedDict):
    # ===== 采集阶段 =====
    selected_sources: list[str]              # SourceRouter 选择的信息源
    raw_articles: list[dict]                 # Collector 采集的原始文章
    # 每篇文章结构: {
    #   "title": str,
    #   "summary": str,
    #   "url": str,
    #   "source": str,          # 来源渠道名
    #   "published_at": str,    # 发布时间
    #   "raw_content": str      # 原文摘要或全文
    # }

    # ===== 分析阶段 =====
    filtered_articles: list[dict]            # Analyst 筛选后的高价值文章
    should_tweet: bool                       # Analyst 判断：今天是否有值得发的内容
    analysis_reasoning: str                  # Analyst 的判断理由

    # ===== 内容规划阶段 =====
    content_plan: dict                       # ContentPlanner 输出的内容计划
    # content_plan 结构: {
    #   "topic": str,           # 选定的话题
    #   "angle": str,           # 切入角度
    #   "style": str,           # 风格: "informative" | "opinion" | "thread" | "hot_take"
    #   "target_articles": list[dict],  # 参考的文章
    #   "key_points": list[str] # 要涵盖的要点
    # }

    # ===== 写作阶段 =====
    draft_tweet: str                         # Writer 生成的推文草稿
    tweet_type: Literal["single", "thread"]  # 单条推文或 thread

    # ===== 审查阶段 =====
    review_score: float                      # Reviewer 评分 (0-10)
    review_feedback: str                     # Reviewer 的修改建议
    review_passed: bool                      # 是否通过审查
    revision_count: int                      # 当前修改轮次 (最多3次)

    # ===== 发布阶段 =====
    published: bool                          # 是否已发布
    tweet_id: str                            # 发布后的推文 ID
    published_at: str                        # 发布时间

    # ===== 全局 =====
    account_context: dict                    # 账号定位信息（从配置加载）
    # account_context 结构: {
    #   "niche": str,           # 账号领域定位，如 "AI/Tech"
    #   "tone": str,            # 语调风格，如 "专业但不枯燥"
    #   "language": str,        # 语言，如 "en" 或 "zh"
    #   "avoid_topics": list[str],  # 避免的话题
    #   "recent_tweets": list[str]  # 最近发过的推文（用于去重）
    # }
    error_log: list[str]                     # 错误日志
```

---

## 三、各节点详细设计

### Node 1: SourceRouter（信息源路由）

**职责**：根据当前时间、账号定位和近期发推记录，自主决定从哪些渠道采集信息。

**输入**：`account_context`
**输出**：`selected_sources`

**实现逻辑**：

```python
async def source_router(state: TweetAgentState) -> dict:
    """
    LLM 决策节点：根据账号定位选择信息源。

    Prompt 核心逻辑:
    - 输入: 账号定位(niche)、当前时间、最近发过的话题
    - 输出: 选择 2-4 个信息源，并说明理由

    可用信息源 (tools):
    - "hackernews": Hacker News 热帖 (科技/创业)
    - "arxiv": arXiv 最新论文 (AI/ML)
    - "twitter_trending": X 平台趋势话题
    - "rss_tech": 科技博客 RSS 聚合 (TechCrunch, The Verge 等)
    - "reddit_ai": Reddit r/artificial 等子版
    - "producthunt": Product Hunt 新产品

    决策依据:
    - 如果最近发了太多论文解读 → 降低 arxiv 权重
    - 如果是工作日早上 → 偏向新闻类
    - 如果是周末 → 可以偏向深度内容或有趣的项目
    """
    # 调用 LLM 进行决策
    # 返回 {"selected_sources": ["hackernews", "arxiv", "twitter_trending"]}
```

**关键点**：这个节点体现了 Agent 的"自主决策"能力——不是固定爬同样的源，而是根据上下文动态选择。

---

### Node 2: Collector（多源采集器）

**职责**：根据 SourceRouter 的选择，并发调用对应的采集工具，获取原始内容。

**输入**：`selected_sources`
**输出**：`raw_articles`

**实现逻辑**：

```python
async def collector(state: TweetAgentState) -> dict:
    """
    工具调用节点：并发执行多个采集工具。

    这个节点不需要 LLM，是纯工具调用:
    - 根据 selected_sources 列表，并发调用对应的采集函数
    - 每个采集函数是一个独立的 Tool
    - 统一输出格式为 raw_articles 列表

    采集工具实现要点:
    - hackernews_tool: 调用 HN API (https://hacker-news.firebaseio.com/v0/)
      获取 top stories，取前 20 条，提取标题和摘要
    - arxiv_tool: 调用 arxiv API，按账号领域关键词搜索最近 24h 论文
    - twitter_trending_tool: 调用 X API v2 的 trends 端点
    - rss_tool: 用 feedparser 解析预配置的 RSS 源列表
    - reddit_tool: 调用 Reddit API，获取指定 subreddit 的 hot posts
    - producthunt_tool: 调用 PH API 获取当日新品

    错误处理:
    - 单个源采集失败不影响其他源
    - 失败信息记入 error_log
    - 至少一个源成功即可继续流程
    """
    # 并发采集
    # 返回 {"raw_articles": [...], "error_log": [...]}
```

**关键点**：这里体现了 Agent 的 Tool Use 能力，每个采集源是一个独立的 Tool。

---

### Node 3: Analyst（内容分析师）

**职责**：对采集到的原始内容进行筛选、去重、价值判断，决定是否值得发推。

**输入**：`raw_articles`, `account_context`
**输出**：`filtered_articles`, `should_tweet`, `analysis_reasoning`

**实现逻辑**：

```python
async def analyst(state: TweetAgentState) -> dict:
    """
    LLM 决策节点：内容价值评估和筛选。

    Prompt 核心逻辑:
    - 输入: raw_articles 列表 + 账号定位 + 最近发过的推文
    - 任务:
      1. 去重: 与 recent_tweets 对比，排除已经发过的相似话题
      2. 相关性过滤: 只保留与账号定位相关的内容
      3. 价值评分: 对每篇文章打分 (时效性 × 话题热度 × 独特性)
      4. 最终决策: 是否有值得发的内容

    Tool 调用 (可选):
    - search_recent_tweets_tool: 搜索自己账号最近 48h 的推文，用于去重
    - check_trending_tool: 检查某个话题在 X 上的当前热度

    输出结构:
    - filtered_articles: 筛选后的 top 3-5 篇文章
    - should_tweet: bool (如果全部内容都是低价值的，返回 False)
    - analysis_reasoning: 解释为什么选了这些 / 为什么不发
    """
    # LLM 分析 + 可选工具调用
    # 返回 {"filtered_articles": [...], "should_tweet": True/False, "analysis_reasoning": "..."}
```

**条件边：Analyst → ContentPlanner 或 END**

```python
def should_continue_after_analysis(state: TweetAgentState) -> str:
    """条件路由: 如果没有值得发的内容，直接结束"""
    if state["should_tweet"]:
        return "content_planner"
    else:
        return "end"
```

---

### Node 4: ContentPlanner（内容策划）

**职责**：基于筛选后的文章，决定发推的话题、角度和风格。

**输入**：`filtered_articles`, `account_context`
**输出**：`content_plan`

**实现逻辑**：

```python
async def content_planner(state: TweetAgentState) -> dict:
    """
    LLM 决策节点：内容策划。

    Prompt 核心逻辑:
    - 输入: filtered_articles + 账号定位 + 最近推文
    - 任务:
      1. 从 filtered_articles 中选择 1 个最佳话题
      2. 决定切入角度 (是转述新闻、发表观点、还是做深度解读)
      3. 决定推文风格 (informative / opinion / hot_take / thread)
      4. 列出关键要点

    决策依据:
    - 如果最近发了太多 informative 类型 → 偏向 opinion 或 hot_take
    - 如果话题足够有深度 → 考虑做 thread
    - 如果是突发新闻 → 快速 informative + 简短观点
    - 始终避免与最近推文角度重复

    输出:
    - content_plan dict (见 State 定义中的结构)
    """
```

---

### Node 5: Writer（推文撰写）

**职责**：根据 content_plan 撰写推文。如果是修改轮次，还会参考 Reviewer 的反馈。

**输入**：`content_plan`, `review_feedback` (如果是修改轮次), `revision_count`
**输出**：`draft_tweet`, `tweet_type`

**实现逻辑**：

```python
async def writer(state: TweetAgentState) -> dict:
    """
    LLM 生成节点：撰写推文。

    首次撰写 Prompt:
    - 输入: content_plan
    - 约束:
      - 单条推文: 不超过 280 字符 (英文) 或 140 字 (中文)
      - Thread: 每条不超过 280 字符，总共 3-7 条
      - 包含相关 hashtag (1-3个)
      - 语调符合 account_context.tone
      - 如果引用了文章，加上来源 URL

    修改轮次 Prompt (revision_count > 0):
    - 输入: content_plan + 上一版 draft_tweet + review_feedback
    - 任务: 根据 feedback 具体修改，不要完全重写

    Tool 调用 (可选):
    - fact_check_tool: 用搜索引擎验证关键事实声明
      (比如 "OpenAI 发布了 GPT-5" → 搜索确认是否属实)
    """
    # 生成推文
    # 返回 {"draft_tweet": "...", "tweet_type": "single"}
```

---

### Node 6: Reviewer（质量审查）

**职责**：对 Writer 生成的推文进行多维度评估，决定是否通过。

**输入**：`draft_tweet`, `content_plan`, `account_context`
**输出**：`review_score`, `review_feedback`, `review_passed`, `revision_count`

**实现逻辑**：

```python
async def reviewer(state: TweetAgentState) -> dict:
    """
    LLM 评估节点：推文质量审查 (Reflection)。

    Prompt 核心逻辑:
    - 输入: draft_tweet + content_plan + account_context
    - 评估维度 (每项 0-10 分):
      1. 准确性: 是否有事实错误或误导性表述
      2. 吸引力: 是否有足够的 hook，能吸引人读完
      3. 品牌一致性: 是否符合账号定位和语调
      4. 字数合规: 是否在字符限制内
      5. 争议风险: 是否可能引发不必要的争议
    - 综合评分: 加权平均

    通过标准:
    - 综合评分 >= 7.0 → 通过
    - 综合评分 < 7.0 → 不通过，给出具体修改建议
    - 如果 revision_count >= 3 → 强制通过或丢弃 (避免死循环)

    输出:
    - review_score: float
    - review_feedback: 具体修改建议 (如果不通过)
    - review_passed: bool
    - revision_count: +1
    """
```

**条件边：Reviewer → Publisher 或 Writer 或 END**

```python
def should_continue_after_review(state: TweetAgentState) -> str:
    """条件路由: 通过→发布，未通过→修改，超次数→丢弃"""
    if state["review_passed"]:
        return "publisher"
    elif state["revision_count"] >= 3:
        # 修改 3 次还不行，丢弃这条
        return "end"
    else:
        return "writer"  # 回到 Writer 修改
```

---

### Node 7: Publisher（发布器）

**职责**：调用 X API 发布推文。

**输入**：`draft_tweet`, `tweet_type`
**输出**：`published`, `tweet_id`, `published_at`

**实现逻辑**：

```python
async def publisher(state: TweetAgentState) -> dict:
    """
    工具调用节点：调用 X API 发布推文。

    实现要点:
    - 单条推文: 调用 POST /2/tweets
    - Thread: 按顺序发布，每条 reply 上一条 (用 reply.in_reply_to_tweet_id)
    - 发布成功后记录 tweet_id 和时间
    - 发布失败则记入 error_log

    Tool:
    - post_tweet_tool: 封装 X API v2 的推文发布接口
    - post_thread_tool: 封装 thread 发布逻辑 (连续 reply)
    """
    # 调用 X API
    # 返回 {"published": True, "tweet_id": "...", "published_at": "..."}
```

---

## 四、LangGraph 组装代码

```python
from langgraph.graph import StateGraph, END

# 创建状态图
workflow = StateGraph(TweetAgentState)

# 添加节点
workflow.add_node("source_router", source_router)
workflow.add_node("collector", collector)
workflow.add_node("analyst", analyst)
workflow.add_node("content_planner", content_planner)
workflow.add_node("writer", writer)
workflow.add_node("reviewer", reviewer)
workflow.add_node("publisher", publisher)

# 设置入口
workflow.set_entry_point("source_router")

# 添加边
workflow.add_edge("source_router", "collector")       # 路由 → 采集
workflow.add_edge("collector", "analyst")              # 采集 → 分析
workflow.add_conditional_edges(                         # 分析 → 策划 or 结束
    "analyst",
    should_continue_after_analysis,
    {"content_planner": "content_planner", "end": END}
)
workflow.add_edge("content_planner", "writer")         # 策划 → 写作
workflow.add_edge("writer", "reviewer")                # 写作 → 审查
workflow.add_conditional_edges(                         # 审查 → 发布 or 修改 or 结束
    "reviewer",
    should_continue_after_review,
    {"publisher": "publisher", "writer": "writer", "end": END}
)
workflow.add_edge("publisher", END)                    # 发布 → 结束

# 编译
app = workflow.compile()
```

---

## 五、工具 (Tools) 清单

以下是系统需要实现的所有工具，每个工具应封装为 LangGraph 可调用的 Tool：

| 工具名 | 类型 | 用途 | 调用方 |
|--------|------|------|--------|
| `hackernews_tool` | 采集 | 获取 HN 热帖 | Collector |
| `arxiv_tool` | 采集 | 搜索最新论文 | Collector |
| `twitter_trending_tool` | 采集 | 获取 X 趋势话题 | Collector |
| `rss_tool` | 采集 | 解析 RSS 源 | Collector |
| `reddit_tool` | 采集 | 获取 Reddit 热帖 | Collector |
| `producthunt_tool` | 采集 | 获取 PH 新品 | Collector |
| `search_recent_tweets_tool` | 查询 | 搜索自己最近的推文 | Analyst |
| `check_trending_tool` | 查询 | 检查话题热度 | Analyst |
| `fact_check_tool` | 验证 | 搜索引擎验证事实 | Writer |
| `post_tweet_tool` | 发布 | 发布单条推文 | Publisher |
| `post_thread_tool` | 发布 | 发布 thread | Publisher |

---

## 六、项目文件结构

```
auto-tweet-agent/
├── README.md
├── pyproject.toml                  # 项目依赖管理
├── .env.example                    # 环境变量模板
│
├── config/
│   ├── account_config.yaml         # 账号定位配置
│   └── sources_config.yaml         # 信息源配置
│
├── src/
│   ├── __init__.py
│   ├── state.py                    # TweetAgentState 定义
│   ├── graph.py                    # LangGraph 组装 (上面第四节的代码)
│   │
│   ├── nodes/                      # 各节点实现
│   │   ├── __init__.py
│   │   ├── source_router.py
│   │   ├── collector.py
│   │   ├── analyst.py
│   │   ├── content_planner.py
│   │   ├── writer.py
│   │   ├── reviewer.py
│   │   └── publisher.py
│   │
│   ├── tools/                      # 工具实现
│   │   ├── __init__.py
│   │   ├── collectors/             # 采集工具
│   │   │   ├── hackernews.py
│   │   │   ├── arxiv.py
│   │   │   ├── twitter_trending.py
│   │   │   ├── rss_feeds.py
│   │   │   ├── reddit.py
│   │   │   └── producthunt.py
│   │   ├── twitter_api.py          # X API 封装 (查询+发布)
│   │   └── fact_checker.py         # 事实验证工具
│   │
│   └── prompts/                    # Prompt 模板 (与代码分离)
│       ├── source_router.txt
│       ├── analyst.txt
│       ├── content_planner.txt
│       ├── writer.txt
│       ├── writer_revision.txt     # 修改轮次专用 prompt
│       └── reviewer.txt
│
├── scheduler.py                    # 定时任务入口 (APScheduler / cron)
└── run_once.py                     # 单次运行入口 (调试用)
```


## 七、实现优先级建议

建议按以下顺序开发，每步都能跑通一个最小闭环：

### Phase 1: 最小可运行版本 (MVP)
1. 定义 State
2. 实现 Collector (先只做 1 个源，比如 HackerNews)
3. 实现 Writer (直接写，不经过 Analyst 和 ContentPlanner)
4. 实现 Publisher
5. 用 LangGraph 串起来: Collector → Writer → Publisher

### Phase 2: 加入决策能力
6. 实现 Analyst (筛选+判断)
7. 实现 ContentPlanner (内容策划)
8. 加入条件边: Analyst 可以决定不发

### Phase 3: 加入反思循环
9. 实现 Reviewer
10. 加入 Writer ↔ Reviewer 的 reflection loop
11. 加入 revision_count 控制

### Phase 4: 扩展信息源和工具
12. 增加更多 Collector 工具
13. 实现 SourceRouter (动态选源)
14. 实现 fact_check_tool

### Phase 5: 生产化
15. 加入 APScheduler 定时任务
16. 加入日志和监控
17. 加入发推历史持久化 (SQLite / JSON)
18. 部署到服务器

---

## 八、技术选型

| 组件 | 推荐 | 理由 |
|------|------|------|
| Agent 框架 | LangGraph | 状态图模式，适合有条件分支和循环的 Agent |
| LLM | DeepSeek / GPT-4o-mini | 成本可控，DeepSeek 中文能力强 |
| 采集 | httpx + feedparser | 异步 HTTP + RSS 解析 |
| X API | tweepy v2 | 成熟的 Python X API 客户端 |
| 定时任务 | APScheduler | 轻量级，支持 cron 表达式 |
| 持久化 | SQLite | 单机够用，记录发推历史 |
| 配置管理 | pydantic-settings + YAML | 类型安全的配置 |




