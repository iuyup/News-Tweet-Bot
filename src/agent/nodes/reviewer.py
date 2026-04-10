"""
Reviewer 节点
LLM 评审推文质量，输出 review_passed / score / feedback
未通过时由 graph 路由回 Writer 进行修改（最多 MAX_REVISIONS 次）

五维评审：Engagement / Accuracy / Clarity / Originality / Length
动态权重：Politics 类 Accuracy 权重更高，Tech 类 Engagement 权重更高
"""
import json
import logging

from src.agent._llm_call import call_default_llm
from src.models.news_item import Category
from src.prompts.templates import (
    DEFAULT_WEIGHTS,
    REVIEW_WEIGHTS,
    build_reviewer_prompt,
)

logger = logging.getLogger(__name__)


def _calculate_weighted_score(
    engagement: float,
    accuracy: float,
    clarity: float,
    originality: float,
    length: float,
    category: Category | None = None,
) -> float:
    """
    计算加权总分。

    各维度权重定义 prompt 中的满分值，LLM 返回的原始分已经是相对于满分的贡献分，
    直接求和即得加权总分（满分 = 权重之和 = 10）。
    """
    return engagement + accuracy + clarity + originality + length


def _parse_review(raw: str) -> dict:
    """解析 LLM 返回的 JSON 评审结果"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return json.loads(text)


def _get_category(tweets: list[dict]) -> Category | None:
    """从推文列表中推断新闻类别"""
    if not tweets:
        return None
    first_item = tweets[0].get("news_item")
    if first_item and hasattr(first_item, "category"):
        return first_item.category
    return None


async def reviewer_node(state: dict) -> dict:
    """评审 generated_tweets，输出五维评审结果"""
    tweets = state.get("generated_tweets", [])
    revision_count = state.get("revision_count", 0)

    if not tweets:
        logger.warning("Reviewer: 无推文可评审，默认通过")
        return {
            "review_passed": True,
            "review_score": 0.0,
            "engagement": 0.0,
            "accuracy": 0.0,
            "clarity": 0.0,
            "originality": 0.0,
            "length": 0.0,
            "review_feedback": "",
            "revision_count": revision_count,
        }

    category = _get_category(tweets)
    prompt = build_reviewer_prompt(tweets, category)

    try:
        raw = await call_default_llm(prompt, max_tokens=512)
        data = _parse_review(raw)
        passed = bool(data.get("review_passed", False))
        feedback = str(data.get("feedback", "")).strip()

        # 五维原始得分
        engagement = float(data.get("engagement", 0.0))
        accuracy = float(data.get("accuracy", 0.0))
        clarity = float(data.get("clarity", 0.0))
        originality = float(data.get("originality", 0.0))
        length = float(data.get("length", 0.0))

        # 计算加权总分
        score = _calculate_weighted_score(
            engagement, accuracy, clarity, originality, length, category
        )
    except Exception as e:
        # LLM 失败视为未通过，revision_count +1；图的边界逻辑保证不死循环
        logger.warning("Reviewer LLM 失败: %s，视为未通过", e)
        return {
            "review_passed": False,
            "review_score": 0.0,
            "engagement": 0.0,
            "accuracy": 0.0,
            "clarity": 0.0,
            "originality": 0.0,
            "length": 0.0,
            "review_feedback": f"Reviewer error: {e}",
            "revision_count": revision_count + 1,
        }

    # 未通过时计数 +1
    new_count = revision_count + (0 if passed else 1)

    logger.info(
        "Reviewer: passed=%s score=%.1f (eng=%.1f acc=%.1f clr=%.1f ori=%.1f len=%.1f) revision=%d | %s",
        passed,
        score,
        engagement,
        accuracy,
        clarity,
        originality,
        length,
        new_count,
        feedback[:80] if feedback else "OK",
    )

    return {
        "review_passed": passed,
        "review_score": score,
        "engagement": engagement,
        "accuracy": accuracy,
        "clarity": clarity,
        "originality": originality,
        "length": length,
        "review_feedback": feedback,
        "revision_count": new_count,
    }
