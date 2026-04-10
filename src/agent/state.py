"""
TweetAgentState 定义
LangGraph StateGraph 使用的状态类型
"""
import operator
from datetime import datetime
from typing import Annotated, Any, TypedDict

from typing_extensions import NotRequired

from src.models.news_item import NewsItem


class TweetAgentState(TypedDict):
    # ── MVP 必需字段 ──────────────────────────────────────────────────────
    raw_items: list[NewsItem]                              # Collector: 原始抓取
    scrape_errors: list[str]                               # Collector: 抓取错误
    filtered_items: list[NewsItem]                         # Collector: 去重+过滤后
    generated_tweets: list[dict[str, Any]]                 # Writer: 生成的推文列表
    publish_results: Annotated[list[dict], operator.add]   # Publisher: 发布结果(追加式)
    run_at: datetime                                       # 全局: 运行时间戳
    error_log: Annotated[list[str], operator.add]          # 全局: 错误日志(追加式)

    # ── Phase 4: SourceRouter ─────────────────────────────────────────────
    selected_sources: NotRequired[list[str]]

    # ── Phase 2-3 预留 ────────────────────────────────────────────────────
    should_tweet: NotRequired[bool]
    analysis_reasoning: NotRequired[str]
    content_plan: NotRequired[dict]

    # ── Reviewer 五维评审 ─────────────────────────────────────────────────
    review_score: NotRequired[float]       # 加权总分
    review_passed: NotRequired[bool]
    review_feedback: NotRequired[str]
    revision_count: NotRequired[int]
    # 五维原始得分
    engagement: NotRequired[float]
    accuracy: NotRequired[float]
    clarity: NotRequired[float]
    originality: NotRequired[float]
    length: NotRequired[float]
