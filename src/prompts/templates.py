"""
推文撰写 Prompt 模板
"""
from src.models.news_item import Category, NewsItem


def build_analyst_prompt(items: list[NewsItem], recent_tweets: list[dict] | None = None) -> str:
    """
    构建 Analyst 提示词：让 LLM 分析新闻价值，选出最值得发推的条目。
    recent_tweets: 最近 7 天已发布推文列表（供去重参考）。
    返回 JSON: {"should_tweet": bool, "reasoning": str, "selected_indices": [1,2,...]}
    """
    headlines = "\n".join(
        f"{i+1}. [{item.category.value.upper()}] {item.title} (score: {item.score})"
        for i, item in enumerate(items)
    )

    recent_section = ""
    if recent_tweets:
        recent_lines = "\n".join(
            f"- [{r.get('category', '?')[:3].upper()}] {r.get('tweet', '')[:100]}"
            for r in recent_tweets[:10]
        )
        recent_section = f"""
Recently published tweets (past 7 days) — avoid similar topics or angles:
{recent_lines}
"""

    return f"""You are a news analyst deciding which stories deserve a tweet today.

{len(items)} trending items:

{headlines}
{recent_section}
Select the BEST 4-6 items for Twitter. Prefer: breaking news, controversy, tech breakthroughs, mix of politics and tech. Avoid: niche, tabloid, already over-covered, or topics already tweeted recently.

Respond ONLY with this JSON:
{{
  "should_tweet": true,
  "reasoning": "<2-3 sentences on today's news landscape and your selection rationale>",
  "selected_indices": [<1-based indices>]
}}

If there are truly no worthy stories, set should_tweet to false and selected_indices to []."""


# 动态权重配置
REVIEW_WEIGHTS = {
    Category.POLITICS: {
        "engagement": 3,
        "accuracy": 3,
        "clarity": 1.5,
        "originality": 1,
        "length": 1.5,
    },
    Category.TECH: {
        "engagement": 4,
        "accuracy": 2,
        "clarity": 1,
        "originality": 1,
        "length": 2,
    },
}

DEFAULT_WEIGHTS = {
    "engagement": 3.5,
    "accuracy": 2.5,
    "clarity": 1.0,
    "originality": 1.0,
    "length": 2.0,
}


def build_reviewer_prompt(tweets: list[dict], category: Category | None = None) -> str:
    """
    构建 Reviewer 提示词：LLM 评审推文质量（五维评分）。

    动态权重策略：
    - Politics类: engagement=3, accuracy=3, clarity=1.5, originality=1, length=1.5
    - Tech类:    engagement=4, accuracy=2, clarity=1, originality=1, length=2
    - 其他/默认: engagement=3.5, accuracy=2.5, clarity=1, originality=1, length=2

    返回 JSON: {
        "review_passed": bool,
        "engagement": float,
        "accuracy": float,
        "clarity": float,
        "originality": float,
        "length": float,
        "feedback": str
    }
    注意：LLM 只返回各维度原始得分，加权总分由 reviewer_node 计算
    """
    entries = ""
    for i, entry in enumerate(tweets, 1):
        tweet = entry["tweet"]
        item = entry["news_item"]
        entries += (
            f"\nTweet {i} [{item.category.value.upper()}] ({len(tweet)} chars):\n"
            f"  Text: {tweet}\n"
            f"  Source: {item.title[:120]}\n"
        )

    weights = (
        REVIEW_WEIGHTS.get(category, DEFAULT_WEIGHTS)
        if category else DEFAULT_WEIGHTS
    )

    return f"""You are a senior social media editor reviewing tweets before publication.
{entries}
Score based on the WEAKEST tweet in the set.

Scoring (10 pts total, weighted):
- Engagement ({weights["engagement"]} pts): Strong hook, unique angle, sparks debate or response
- Accuracy ({weights["accuracy"]} pts): Aligned with source headline, not sensationalist
- Clarity ({weights["clarity"]} pts): Clear language, easy to understand at a glance
- Originality ({weights["originality"]} pts): Fresh angle, not just repeating headlines
- Length ({weights["length"]} pts): ≤280 chars, 2-3 relevant hashtags, no URL

Pass threshold: 7.0 (weighted total)

Respond ONLY with this JSON:
{{
  "review_passed": true,
  "engagement": 3.5,
  "accuracy": 2.5,
  "clarity": 0.9,
  "originality": 0.8,
  "length": 1.8,
  "feedback": ""
}}

If not passed, feedback must be specific and actionable for each tweet that needs improvement."""


def build_revision_prompt(current_tweets: list[dict], feedback: str) -> str:
    """
    构建推文修改 Prompt，包含 Reviewer 的具体反馈。
    返回 JSON: {"revised": [{"tweet": str, "index": int}]}
    """
    entries = ""
    for i, entry in enumerate(current_tweets, 1):
        tweet = entry["tweet"]
        item = entry["news_item"]
        entries += (
            f"\nTweet {i} [{item.category.value.upper()}] ({len(tweet)} chars):\n"
            f"  {tweet}\n"
            f"  Source: {item.title[:120]}\n"
        )

    return f"""Rewrite the following tweets based on editor feedback.

Current tweets:{entries}

Editor feedback: {feedback}

Rules:
- Each tweet MUST be ≤280 characters (including hashtags)
- Include 2-3 relevant hashtags at the end
- No URLs
- Address ALL feedback points

Respond ONLY with this JSON:
{{
  "revised": [
    {{"tweet": "<revised tweet 1>", "index": 1}},
    {{"tweet": "<revised tweet 2>", "index": 2}}
  ]
}}"""


def build_source_router_prompt(available_sources: list[str], now) -> str:
    """
    构建 SourceRouter 提示词：LLM 决定今天使用哪些信息源。
    返回 JSON: {"selected_sources": [...], "reasoning": str}
    """
    from datetime import datetime

    if isinstance(now, datetime):
        weekday = now.strftime("%A")
        hour = now.hour
    else:
        weekday, hour = "Unknown", 12

    source_desc = {
        "reddit": "Reddit hot posts (broad coverage: politics + tech, high engagement)",
        "hackernews": "Hacker News top stories (tech/startup focused, developer audience)",
        "arxiv": "arXiv latest papers (AI/ML research, highly technical)",
        "rss": "Tech blog RSS feeds (TechCrunch, The Verge — product/industry news)",
    }
    descriptions = "\n".join(
        f"- {s}: {source_desc.get(s, s)}" for s in available_sources
    )

    return f"""You are selecting news sources to scrape today for a tech/AI Twitter account.

Today is {weekday}, current hour: {hour:02d}:00 UTC.

Available sources:
{descriptions}

Select 2-3 sources for best coverage and diversity.
- Always include at least one broad source (reddit or rss)
- Weekdays: prefer timely news (reddit, hackernews, rss)
- Weekends: can include research content (arxiv)
- Avoid over-indexing on arxiv alone (too technical for general audience)

Respond ONLY with this JSON:
{{
  "selected_sources": ["reddit", "hackernews"],
  "reasoning": "<1 sentence>"
}}"""


def build_tweet_prompt(items: list[NewsItem], category: Category) -> str:
    """
    根据新闻条目列表构建推文生成 Prompt。
    返回完整的 user message 字符串。
    """
    category_desc = {
        Category.POLITICS: "geopolitics and world affairs",
        Category.TECH: "technology and AI",
    }.get(category, "current events")

    headlines = "\n".join(
        f"{i+1}. [{item.source}] {item.title} (score: {item.score})"
        for i, item in enumerate(items)
    )

    return f"""You are a sharp, concise social media writer covering {category_desc}.

Below are today's top trending headlines:

{headlines}

Select the SINGLE most newsworthy headline and write one engaging English tweet about it.

RULES:
- Strict maximum 280 characters (including hashtags)
- Include 2-3 relevant hashtags at the end
- Do NOT include a URL

ENGAGEMENT STRATEGY (Select at least ONE):
1. Ask a provocative question at the end - make readers want to answer or debate
2. Create "who wins / who loses" tension - spark disagreement
3. Make a bold prediction others might disagree with
4. Use a striking data point that challenges assumptions
5. Leave a strategic gap/ambiguity - invite "what do you mean?"

Structure:
- Hook: Lead with the most surprising or counterintuitive fact
- Insight: Your unique take (NOT just repeating the news!)
- CTA or Question: End with something that invites response

Tone: Opinionated but factual. Avoid generic corporate speak.

Respond with ONLY a JSON object in this exact format:
{{
  "tweet": "<tweet text with hashtags>",
  "source_index": <1-based index of the headline you chose>,
  "char_count": <character count of tweet>
}}"""
