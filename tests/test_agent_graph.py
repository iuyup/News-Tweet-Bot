"""
Agent StateGraph 集成测试 (Phase 4)
覆盖：SourceRouter / Collector / Analyst / ContentPlanner / Writer / Reviewer / Publisher / 图集成
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from src.models.news_item import Category, NewsItem


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _make_items(n: int = 4) -> list[NewsItem]:
    return [
        NewsItem(
            title=f"Test headline number {i} with enough length",
            url=f"https://example.com/{i}",
            source="reddit",
            category=Category.TECH if i % 2 == 0 else Category.POLITICS,
            score=1000 - i * 100,
            fetched_at=datetime.utcnow(),
        )
        for i in range(n)
    ]


def _make_tweets(items: list[NewsItem]) -> list[dict]:
    return [
        {
            "tweet": f"Breaking: {item.title[:50]} #Test #News",
            "news_item": item,
            "input_tokens": 100,
            "output_tokens": 50,
        }
        for item in items[:2]
    ]


def _initial_state() -> dict:
    return {
        "raw_items": [],
        "scrape_errors": [],
        "filtered_items": [],
        "generated_tweets": [],
        "publish_results": [],
        "run_at": datetime.now(timezone.utc),
        "error_log": [],
    }


def _run_config() -> dict:
    import uuid
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ── SourceRouter ──────────────────────────────────────────────────────────────

class TestSourceRouterNode:
    @pytest.mark.asyncio
    async def test_source_router_success(self):
        resp = '{"selected_sources": ["reddit", "hackernews"], "reasoning": "Weekday, prefer news."}'
        with patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.source_router import source_router_node
            result = await source_router_node({"run_at": datetime.now(timezone.utc)})

        assert "reddit" in result["selected_sources"]
        assert "hackernews" in result["selected_sources"]

    @pytest.mark.asyncio
    async def test_source_router_llm_failure_fallback(self):
        """LLM 失败时使用默认信息源"""
        with patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, side_effect=Exception("err")):
            from src.agent.nodes.source_router import source_router_node
            result = await source_router_node({"run_at": datetime.now(timezone.utc)})

        assert len(result["selected_sources"]) > 0

    @pytest.mark.asyncio
    async def test_source_router_invalid_source_filtered(self):
        """未知信息源应被过滤"""
        resp = '{"selected_sources": ["reddit", "invalid_source"], "reasoning": "test"}'
        with patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.source_router import source_router_node
            result = await source_router_node({"run_at": datetime.now(timezone.utc)})

        assert "invalid_source" not in result["selected_sources"]
        assert "reddit" in result["selected_sources"]


# ── Collector ─────────────────────────────────────────────────────────────────

class TestCollectorNode:
    @pytest.mark.asyncio
    async def test_collector_single_source(self):
        items = _make_items()
        with patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items):
            from src.agent.nodes.collector import collector_node
            result = await collector_node({"selected_sources": ["reddit"], "run_at": datetime.now(timezone.utc)})

        assert len(result["raw_items"]) == len(items)
        assert result["scrape_errors"] == []
        assert "filtered_items" not in result  # Phase 2+: Analyst 负责

    @pytest.mark.asyncio
    async def test_collector_multi_source(self):
        """多源并发抓取，结果应合并"""
        reddit_items = _make_items(3)
        hn_items = _make_items(2)
        with (
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=reddit_items),
            patch("src.agent.nodes.collector.fetch_hackernews_top", new_callable=AsyncMock, return_value=hn_items),
        ):
            from src.agent.nodes.collector import collector_node
            result = await collector_node({"selected_sources": ["reddit", "hackernews"]})

        # 去重后数量应 ≤ reddit + hn 的总数
        assert len(result["raw_items"]) <= len(reddit_items) + len(hn_items)
        assert len(result["raw_items"]) > 0

    @pytest.mark.asyncio
    async def test_collector_failure(self):
        with patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, side_effect=Exception("err")):
            from src.agent.nodes.collector import collector_node
            result = await collector_node({"selected_sources": ["reddit"], "run_at": datetime.now(timezone.utc)})

        assert result["raw_items"] == []
        assert len(result["error_log"]) > 0


# ── Analyst ───────────────────────────────────────────────────────────────────

class TestAnalystNode:
    @pytest.mark.asyncio
    async def test_analyst_success(self):
        items = _make_items()
        resp = '{"should_tweet": true, "reasoning": "Good mix of news.", "selected_indices": [1, 2, 3]}'
        with patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.analyst import analyst_node
            result = await analyst_node({"raw_items": items})

        assert result["should_tweet"] is True
        assert len(result["filtered_items"]) == 3

    @pytest.mark.asyncio
    async def test_analyst_no_tweet(self):
        items = _make_items()
        resp = '{"should_tweet": false, "reasoning": "Nothing notable.", "selected_indices": []}'
        with patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.analyst import analyst_node
            result = await analyst_node({"raw_items": items})

        assert result["should_tweet"] is False
        assert result["filtered_items"] == []

    @pytest.mark.asyncio
    async def test_analyst_empty_input(self):
        from src.agent.nodes.analyst import analyst_node
        result = await analyst_node({"raw_items": []})
        assert result["should_tweet"] is False
        assert len(result["error_log"]) > 0

    @pytest.mark.asyncio
    async def test_analyst_llm_fallback(self):
        """LLM 失败时降级为规则过滤"""
        items = _make_items()
        with patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, side_effect=Exception("err")):
            from src.agent.nodes.analyst import analyst_node
            result = await analyst_node({"raw_items": items})

        assert result["should_tweet"] is True
        assert len(result["filtered_items"]) > 0


# ── ContentPlanner ────────────────────────────────────────────────────────────

class TestContentPlannerNode:
    def test_planner_mixed(self):
        items = _make_items(4)
        from src.agent.nodes.content_planner import content_planner_node
        result = content_planner_node({"filtered_items": items, "analysis_reasoning": ""})
        plan = result["content_plan"]
        assert plan["total"] > 0
        assert plan["politics_count"] + plan["tech_count"] == plan["total"]

    def test_planner_only_tech(self):
        items = [i for i in _make_items(4) if i.category == Category.TECH]
        from src.agent.nodes.content_planner import content_planner_node
        result = content_planner_node({"filtered_items": items, "analysis_reasoning": ""})
        assert result["content_plan"]["politics_count"] == 0

    def test_planner_empty(self):
        from src.agent.nodes.content_planner import content_planner_node
        result = content_planner_node({"filtered_items": [], "analysis_reasoning": ""})
        assert result["content_plan"]["total"] == 0


# ── Writer ────────────────────────────────────────────────────────────────────

class TestWriterNode:
    @pytest.mark.asyncio
    async def test_writer_normal_mode(self):
        items = _make_items()
        # mock 每次调用返回 1 条推文（模拟 count=1 语义）
        async def _single_tweet(items_arg, count=1):
            return [{"tweet": f"Tweet #{len(items_arg)} #Test", "news_item": items_arg[0],
                     "input_tokens": 100, "output_tokens": 50}]

        with patch("src.agent.nodes.writer.generate_tweets", side_effect=_single_tweet) as mock_gen:
            from src.agent.nodes.writer import writer_node
            result = await writer_node({
                "filtered_items": items,
                "content_plan": {"total": 3, "politics_count": 1, "tech_count": 2},
                "revision_count": 0,
                "review_feedback": "",
                "generated_tweets": [],
            })
        # 1 次 politics + 2 次 tech = 共 3 次调用，每次 count=1
        assert mock_gen.call_count == 3
        assert all(call.kwargs.get("count") == 1 or call.args[1:] == (1,) or
                   call.args[1] == 1 if len(call.args) > 1 else call.kwargs.get("count") == 1
                   for call in mock_gen.call_args_list)
        assert len(result["generated_tweets"]) == 3

    @pytest.mark.asyncio
    async def test_writer_revision_mode(self):
        """revision_count > 0 时走修改模式"""
        items = _make_items()
        original_tweets = _make_tweets(items)
        revised_resp = '{"revised": [{"tweet": "Revised tweet 1 #AI #Tech", "index": 1}, {"tweet": "Revised tweet 2 #Politics", "index": 2}]}'

        with patch("src.agent.nodes.writer.call_default_llm", new_callable=AsyncMock, return_value=revised_resp):
            from src.agent.nodes.writer import writer_node
            result = await writer_node({
                "filtered_items": items,
                "revision_count": 1,
                "review_feedback": "Hooks too weak, add specific stats",
                "generated_tweets": original_tweets,
            })

        assert len(result["generated_tweets"]) == 2
        assert "Revised" in result["generated_tweets"][0]["tweet"]

    @pytest.mark.asyncio
    async def test_writer_empty_input(self):
        from src.agent.nodes.writer import writer_node
        result = await writer_node({"filtered_items": [], "revision_count": 0, "review_feedback": ""})
        assert result["generated_tweets"] == []


# ── Reviewer ──────────────────────────────────────────────────────────────────

class TestReviewerNode:
    @pytest.mark.asyncio
    async def test_reviewer_pass(self):
        tweets = _make_tweets(_make_items())
        # 五维得分：engagement=3.5, accuracy=2.0, clarity=0.9, originality=0.8, length=1.3 → 加权总分=8.5
        resp = '{"review_passed": true, "engagement": 3.5, "accuracy": 2.0, "clarity": 0.9, "originality": 0.8, "length": 1.3, "feedback": ""}'
        with patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.reviewer import reviewer_node
            result = await reviewer_node({"generated_tweets": tweets, "revision_count": 0})

        assert result["review_passed"] is True
        assert result["review_score"] == 8.5
        assert result["engagement"] == 3.5
        assert result["accuracy"] == 2.0
        assert result["clarity"] == 0.9
        assert result["originality"] == 0.8
        assert result["length"] == 1.3
        assert result["revision_count"] == 0  # 通过时不递增

    @pytest.mark.asyncio
    async def test_reviewer_fail_increments_count(self):
        tweets = _make_tweets(_make_items())
        # 五维得分：engagement=2.0, accuracy=1.5, clarity=0.7, originality=0.6, length=0.7 → 加权总分=5.5
        resp = '{"review_passed": false, "engagement": 2.0, "accuracy": 1.5, "clarity": 0.7, "originality": 0.6, "length": 0.7, "feedback": "Hook too weak, add data."}'
        with patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, return_value=resp):
            from src.agent.nodes.reviewer import reviewer_node
            result = await reviewer_node({"generated_tweets": tweets, "revision_count": 0})

        assert result["review_passed"] is False
        assert result["review_score"] == 5.5
        assert result["revision_count"] == 1
        assert "Hook" in result["review_feedback"]

    @pytest.mark.asyncio
    async def test_reviewer_llm_failure_increments_count(self):
        """LLM 失败视为未通过，revision_count +1，由图边界逻辑保证终止"""
        tweets = _make_tweets(_make_items())
        with patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, side_effect=Exception("err")):
            from src.agent.nodes.reviewer import reviewer_node
            result = await reviewer_node({"generated_tweets": tweets, "revision_count": 0})

        assert result["review_passed"] is False
        assert result["revision_count"] == 1

    @pytest.mark.asyncio
    async def test_reviewer_no_tweets(self):
        from src.agent.nodes.reviewer import reviewer_node
        result = await reviewer_node({"generated_tweets": [], "revision_count": 0})
        assert result["review_passed"] is True


# ── Publisher ─────────────────────────────────────────────────────────────────

class TestPublisherNode:
    @pytest.mark.asyncio
    async def test_publisher_empty(self):
        from src.agent.nodes.publisher import publisher_node
        result = await publisher_node({
            "generated_tweets": [],
            "run_at": datetime.now(timezone.utc),
            "filtered_items": [],
        })
        assert result["publish_results"] == []


# ── Graph Integration ─────────────────────────────────────────────────────────

_SOURCE_ROUTER_RESP = '{"selected_sources": ["reddit"], "reasoning": "test"}'


class TestGraphIntegration:
    @pytest.mark.asyncio
    async def test_full_graph_first_pass(self, tmp_path):
        """推文第一次就通过评审"""
        items = _make_items()
        analyst_resp = '{"should_tweet": true, "reasoning": "Great.", "selected_indices": [1,2,3,4]}'
        review_resp = '{"review_passed": true, "score": 8.5, "feedback": ""}'

        # 每次调用返回 1 条推文，模拟 count=1 语义
        async def _one_tweet(items_arg, count=1):
            return [{"tweet": f"Tweet #Test", "news_item": items_arg[0],
                     "input_tokens": 100, "output_tokens": 50}]

        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=_SOURCE_ROUTER_RESP),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=analyst_resp),
            patch("src.agent.nodes.writer.generate_tweets", side_effect=_one_tweet),
            patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, return_value=review_resp),
            patch("src.agent.nodes.publisher.publish_tweet", new_callable=AsyncMock, return_value="mock-id"),
            patch("src.agent.nodes.publisher.generate_daily_summary", new_callable=AsyncMock, return_value="Summary"),
            patch("src.agent.nodes.publisher.save_tweet"),
            patch("src.agent.nodes.publisher.mark_published"),
            patch("src.agent.nodes.publisher.write_daily_md"),
            patch("src.agent.nodes.publisher.update_daily_md_incremental"),
            patch("src.agent.nodes.publisher.get_daily_md_path", return_value=tmp_path / "test.md"),
            patch("src.agent.nodes.publisher.sync_to_target"),
            patch("src.agent.nodes.publisher._write_log"),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        assert result.get("review_passed") is True
        assert result.get("revision_count", 0) == 0
        assert len(result["publish_results"]) == 2

    @pytest.mark.asyncio
    async def test_graph_review_loop_then_pass(self, tmp_path):
        """第一次评审失败 → 修改 → 第二次通过"""
        items = _make_items()
        tweets = _make_tweets(items)
        analyst_resp = '{"should_tweet": true, "reasoning": "OK.", "selected_indices": [1,2]}'
        review_fail = '{"review_passed": false, "score": 5.0, "feedback": "Hook too weak."}'
        review_pass = '{"review_passed": true, "score": 8.0, "feedback": ""}'
        revision_resp = '{"revised": [{"tweet": "Better tweet 1 #AI", "index": 1}, {"tweet": "Better tweet 2 #Politics", "index": 2}]}'

        review_call_count = 0

        async def mock_reviewer_llm(prompt, max_tokens=512):
            nonlocal review_call_count
            review_call_count += 1
            return review_fail if review_call_count == 1 else review_pass

        async def mock_writer_llm(prompt, max_tokens=1024):
            return revision_resp

        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=_SOURCE_ROUTER_RESP),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=analyst_resp),
            patch("src.agent.nodes.writer.generate_tweets", new_callable=AsyncMock, return_value=tweets),
            patch("src.agent.nodes.writer.call_default_llm", side_effect=mock_writer_llm),
            patch("src.agent.nodes.reviewer.call_default_llm", side_effect=mock_reviewer_llm),
            patch("src.agent.nodes.publisher.publish_tweet", new_callable=AsyncMock, return_value="mock-id"),
            patch("src.agent.nodes.publisher.generate_daily_summary", new_callable=AsyncMock, return_value="Summary"),
            patch("src.agent.nodes.publisher.save_tweet"),
            patch("src.agent.nodes.publisher.mark_published"),
            patch("src.agent.nodes.publisher.write_daily_md"),
            patch("src.agent.nodes.publisher.update_daily_md_incremental"),
            patch("src.agent.nodes.publisher.get_daily_md_path", return_value=tmp_path / "test.md"),
            patch("src.agent.nodes.publisher.sync_to_target"),
            patch("src.agent.nodes.publisher._write_log"),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        assert result.get("review_passed") is True
        assert result.get("revision_count") == 1  # 修改了 1 次
        assert review_call_count == 2

    @pytest.mark.asyncio
    async def test_graph_force_publish_after_max_revisions(self, tmp_path):
        """超过最大修改次数后强制发布"""
        from src.agent.graph import MAX_REVISIONS
        items = _make_items()
        tweets = _make_tweets(items)
        analyst_resp = '{"should_tweet": true, "reasoning": "OK.", "selected_indices": [1,2]}'
        review_fail = '{"review_passed": false, "score": 4.0, "feedback": "Still bad."}'
        revision_resp = '{"revised": [{"tweet": "Still revised #AI", "index": 1}, {"tweet": "Still revised #Pol", "index": 2}]}'

        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=_SOURCE_ROUTER_RESP),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=analyst_resp),
            patch("src.agent.nodes.writer.generate_tweets", new_callable=AsyncMock, return_value=tweets),
            patch("src.agent.nodes.writer.call_default_llm", new_callable=AsyncMock, return_value=revision_resp),
            patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, return_value=review_fail),
            patch("src.agent.nodes.publisher.publish_tweet", new_callable=AsyncMock, return_value="mock-id"),
            patch("src.agent.nodes.publisher.generate_daily_summary", new_callable=AsyncMock, return_value="Summary"),
            patch("src.agent.nodes.publisher.save_tweet"),
            patch("src.agent.nodes.publisher.mark_published"),
            patch("src.agent.nodes.publisher.write_daily_md"),
            patch("src.agent.nodes.publisher.update_daily_md_incremental"),
            patch("src.agent.nodes.publisher.get_daily_md_path", return_value=tmp_path / "test.md"),
            patch("src.agent.nodes.publisher.sync_to_target"),
            patch("src.agent.nodes.publisher._write_log"),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        # 最终 revision_count 应 >= MAX_REVISIONS，仍能发布
        assert result.get("revision_count", 0) >= MAX_REVISIONS
        assert len(result["publish_results"]) > 0

    @pytest.mark.asyncio
    async def test_graph_early_exit_no_news(self):
        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=_SOURCE_ROUTER_RESP),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=[]),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        assert result["raw_items"] == []
        assert result["publish_results"] == []

    @pytest.mark.asyncio
    async def test_graph_early_exit_analyst_no_tweet(self):
        items = _make_items()
        analyst_resp = '{"should_tweet": false, "reasoning": "Nothing notable.", "selected_indices": []}'
        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=_SOURCE_ROUTER_RESP),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=analyst_resp),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        assert result.get("should_tweet") is False
        assert result["publish_results"] == []

    @pytest.mark.asyncio
    async def test_graph_source_router_selects_multi_source(self, tmp_path):
        """SourceRouter 选择多个信息源，Collector 并发抓取"""
        items = _make_items(3)
        tweets = _make_tweets(items)
        router_resp = '{"selected_sources": ["reddit", "hackernews"], "reasoning": "Multi-source test."}'
        analyst_resp = '{"should_tweet": true, "reasoning": "Good.", "selected_indices": [1,2,3]}'
        review_resp = '{"review_passed": true, "score": 8.0, "feedback": ""}'

        with (
            patch("src.agent.nodes.source_router.call_default_llm", new_callable=AsyncMock, return_value=router_resp),
            patch("src.agent.nodes.collector.fetch_reddit_hot", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.collector.fetch_hackernews_top", new_callable=AsyncMock, return_value=items),
            patch("src.agent.nodes.analyst._call_llm", new_callable=AsyncMock, return_value=analyst_resp),
            patch("src.agent.nodes.writer.generate_tweets", new_callable=AsyncMock, return_value=tweets),
            patch("src.agent.nodes.reviewer.call_default_llm", new_callable=AsyncMock, return_value=review_resp),
            patch("src.agent.nodes.publisher.publish_tweet", new_callable=AsyncMock, return_value="mock-id"),
            patch("src.agent.nodes.publisher.generate_daily_summary", new_callable=AsyncMock, return_value="Summary"),
            patch("src.agent.nodes.publisher.save_tweet"),
            patch("src.agent.nodes.publisher.mark_published"),
            patch("src.agent.nodes.publisher.write_daily_md"),
            patch("src.agent.nodes.publisher.update_daily_md_incremental"),
            patch("src.agent.nodes.publisher.get_daily_md_path", return_value=tmp_path / "test.md"),
            patch("src.agent.nodes.publisher.sync_to_target"),
            patch("src.agent.nodes.publisher._write_log"),
        ):
            from src.agent.graph import build_checkpointed_graph
            app = build_checkpointed_graph()
            result = await app.ainvoke(_initial_state(), config=_run_config())

        assert result.get("selected_sources") == ["reddit", "hackernews"]
        assert len(result["publish_results"]) > 0
