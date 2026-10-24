"""Network-free regression tests for online safety and resource-aware latency."""
import json
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.online.base import OnlineRetrievalProviderError, OnlineRetrievalResponseError
from assistant.online.grounding import build_online_grounding_instruction, select_current_evidence
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache, _CACHE
from assistant.policy import AssistantResponsePolicy
from assistant.routing import QueryRouter
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from system.control_plane.resource_manager import evaluate_resource_policy
from system.tests_resource_manager import healthy_snapshot


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, results=None, body=None, url=None, **extra):
        self.body = body if body is not None else json.dumps({"results": results or [], **extra}).encode()
        self.url = url or "http://127.0.0.1:8888/search"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.body[:size]

    def geturl(self):
        return self.url


def raw_result(**changes):
    return dict(title="Acme CEO appointment", url="https://example.org/acme", content="Acme CEO is Alice Smith as of 2026-10-24.", publishedDate=NOW.isoformat(), **changes)


class IntentTests(SimpleTestCase):
    def test_dynamic_facts_without_freshness_adjectives(self):
        for query in ("Who is Acme's CEO?", "Who is India's president?", "Bitcoin price?", "Python releases?", "AI news?", "Weather in Delhi?", "IPL scores?", "Team rankings?", "Train schedule?", "Flight status?", "Is Python 4 released?", "Is the museum open?", "Is a Pi 5 available?", "Who won the match?", "What happened this week?"):
            with self.subTest(query=query):
                self.assertTrue(QueryRouter.requires_online(query))

    def test_local_explanations_and_personal_knowledge(self):
        for query in ("Hello", "What is electrical current?", "What does latest mean?", "How does weather forecasting work?", "Explain stock prices.", "What is price elasticity?", "Who wrote Hamlet?", "Who was president in 1990?", "According to my notes, what is the latest release?", "What is our project schedule?", "What's my current price list?", "Turn on the light"):
            with self.subTest(query=query):
                self.assertFalse(QueryRouter.requires_online(query))


class ProviderResilienceTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache()
        enrichment = patch("assistant.online.enrichment.enrich_results", side_effect=lambda results, *args: results)
        enrichment.start()
        self.addCleanup(enrichment.stop)
        self.retriever = SearXNGOnlineRetriever(base_url="http://127.0.0.1:8888", timeout_seconds=4, max_results=4)

    @patch("assistant.online.searxng.request.urlopen")
    def test_retry_transient_then_cache_across_retriever_instances(self, http):
        http.side_effect = [socket.timeout(), Response([raw_result()])]
        first = self.retriever.retrieve("Acme CEO")
        second = SearXNGOnlineRetriever(base_url=self.retriever.base_url, timeout_seconds=4, max_results=4).retrieve("Acme CEO")
        self.assertEqual(first, second)
        self.assertEqual(http.call_count, 2)
        self.assertTrue(all(0 < c.kwargs["timeout"] <= 3 for c in http.call_args_list))

    @patch("assistant.online.searxng.request.urlopen")
    def test_no_internet_cooldown_and_bounded_retry(self, http):
        http.side_effect = URLError("private connection detail")
        for query in ("Acme CEO", "other news"):
            with self.assertRaises(OnlineRetrievalProviderError):
                self.retriever.retrieve(query)
        self.assertEqual(http.call_count, 2)

    @patch("assistant.online.searxng.request.urlopen")
    def test_captive_portal_html_and_redirect_never_retry(self, http):
        for response in (Response(body=b"<html>Login required</html>"), Response([raw_result()], url="http://college.invalid/login")):
            clear_online_cache()
            http.reset_mock()
            http.return_value = response
            with self.assertRaises(OnlineRetrievalResponseError):
                self.retriever.retrieve("Acme CEO")
            self.assertEqual(http.call_count, 1)

    @patch("assistant.online.searxng.request.urlopen")
    def test_auth_and_rate_limits_are_not_retried(self, http):
        for code in (401, 403, 429):
            clear_online_cache()
            http.reset_mock()
            http.side_effect = HTTPError("http://provider", code, "denied", {}, None)
            with self.assertRaises(OnlineRetrievalProviderError):
                self.retriever.retrieve("Acme CEO")
            self.assertEqual(http.call_count, 1)

    @patch("assistant.online.searxng.request.urlopen")
    def test_empty_local_endpoint_is_not_external_connectivity(self, http):
        http.return_value = Response()
        with self.assertRaises(OnlineRetrievalProviderError):
            self.retriever.retrieve("Acme CEO")
        self.assertEqual(http.call_count, 2)  # query + one real canary

    @patch("assistant.online.searxng.request.urlopen")
    def test_empty_results_with_healthy_external_search_and_health_cache(self, http):
        canary = Response([{"title": "IANA", "url": "https://www.iana.org/help/example-domains", "content": "IANA reserves example domains for documentation."}])
        http.side_effect = [Response(), canary, Response()]
        self.assertEqual(self.retriever.retrieve("Acme CEO"), [])
        self.assertEqual(self.retriever.retrieve("Other CEO"), [])
        self.assertEqual(http.call_count, 3)

    @patch("assistant.online.searxng.request.urlopen")
    def test_one_alternative_query_only(self, http):
        http.side_effect = [Response(), Response([raw_result()])]
        self.assertEqual(len(self.retriever.retrieve("What is Acme CEO?")), 1)
        self.assertEqual(http.call_count, 2)
        self.assertIn("q=Acme+CEO", http.call_args.args[0].full_url)

    @patch("assistant.online.searxng.request.urlopen")
    def test_defensive_parsing_dedup_and_script_sanitization(self, http):
        result = raw_result()
        http.return_value = Response([None, "bad", result, result, {**result, "url": "https://example.net", "content": result["content"]}, {**result, "url": "http://127.0.0.1/private"}, {**result, "url": "https://example.net/real", "content": "<script>secret</script>Different useful Acme information."}])
        results = self.retriever.retrieve("Acme CEO")
        self.assertEqual(len(results), 2)
        self.assertNotIn("secret", results[1].content)

    @patch("assistant.online.searxng.request.urlopen")
    def test_relevant_results_rank_before_generic_first_hits(self, http):
        http.return_value = Response([
            {"title": "Generic page", "url": "https://example.net", "content": "Browse the information on this site."},
            raw_result(),
        ])
        self.retriever.max_results = 1
        self.assertEqual(self.retriever.retrieve("Acme CEO")[0].title, "Acme CEO appointment")

    @patch("assistant.online.searxng.request.urlopen")
    def test_malformed_types_and_bounded_response(self, http):
        for body in (b"[]", b"null", b'{"results": {}}', b"x" * 1_000_001):
            clear_online_cache()
            http.return_value = Response(body=body)
            with self.assertRaises(OnlineRetrievalResponseError):
                self.retriever.retrieve("Acme CEO")

    @patch("assistant.online.searxng.time.monotonic")
    @patch("assistant.online.searxng.request.urlopen")
    def test_total_budget_prevents_a_retry_after_slow_response(self, http, clock):
        clock.return_value = 100
        def slow(*args, **kwargs):
            clock.return_value = 105
            return Response([raw_result()])
        http.side_effect = slow
        from assistant.online.base import OnlineRetrievalTimeoutError
        with self.assertRaises(OnlineRetrievalTimeoutError):
            self.retriever.retrieve("Acme CEO")
        self.assertEqual(http.call_count, 1)

    @patch("assistant.online.searxng.time.monotonic")
    @patch("assistant.online.searxng.request.urlopen")
    def test_negative_health_expiry_allows_recovery(self, http, clock):
        clock.return_value = 100
        http.side_effect = URLError("offline")
        with self.assertRaises(OnlineRetrievalProviderError):
            self.retriever.retrieve("Acme CEO")
        clock.return_value = 111
        http.side_effect = None
        http.return_value = Response([raw_result()])
        self.assertEqual(len(self.retriever.retrieve("Acme CEO")), 1)

    @patch("assistant.online.searxng.time.monotonic")
    @patch("assistant.online.searxng.request.urlopen")
    def test_freshness_ttl_and_cache_bound(self, http, clock):
        clock.return_value = 100
        http.return_value = Response([raw_result()])
        self.retriever.retrieve("Acme price")
        clock.return_value = 114
        self.retriever.retrieve("Acme price")
        self.assertEqual(http.call_count, 1)
        clock.return_value = 116
        self.retriever.retrieve("Acme price")
        self.assertEqual(http.call_count, 2)
        for i in range(70):
            self.retriever.retrieve(f"Acme release {i}")
        self.assertEqual(len(_CACHE), 64)


@override_settings(AI_ENGINE="local", ONLINE_RETRIEVAL_ENABLED=True, ONLINE_PROVIDER="searxng", ONLINE_TIMEOUT_SECONDS=4, ONLINE_MAX_RESULTS=4, ONLINE_MAX_CONTEXT_CHARS=3600, SEARXNG_BASE_URL="http://127.0.0.1:8888", ONLINE_KNOWLEDGE_AUTO_PERSIST=False, RAG_ENABLED=False)
class EndToEndTests(TestCase):
    def setUp(self):
        clear_online_cache()
        self.report = install_resource_report(self, online_allowed=True)

    @patch("assistant.services.django_timezone.now", return_value=NOW)
    @patch("assistant.services.get_engine")
    @patch("assistant.online.searxng.request.urlopen")
    def test_transport_and_voice_matrix(self, http, engine_factory, _now):
        result = AIEngineResult(text="Acme's CEO is Alice Smith, as reported on October 24.", engine="local", latency_ms=12, ttft_ms=2)
        engine = MagicMock()
        engine.generate.return_value = result
        engine.generate_stream.side_effect = lambda *a, **k: iter([AIEngineStreamEvent(type="delta", text=result.text), AIEngineStreamEvent(type="done", result=result)])
        engine_factory.return_value = engine
        for state in ("FULL", "UNKNOWN"):
            snapshot = healthy_snapshot()
            snapshot["network"]["internet_state"] = state
            self.report.return_value = evaluate_resource_policy(snapshot)
            for stream in (False, True):
                for voice in (False, True):
                    for scenario in ("connected", "portal", "offline", "timeout", "empty", "stale", "malformed", "no_relevant", "contradictory"):
                        with self.subTest(state=state, stream=stream, voice=voice, scenario=scenario):
                            clear_online_cache()
                            engine.reset_mock()
                            http.side_effect = None
                            http.return_value = Response([raw_result()])
                            if scenario == "portal":
                                http.return_value = Response(body=b"<html>Sign in</html>")
                            elif scenario == "offline":
                                http.side_effect = URLError("no network")
                            elif scenario == "timeout":
                                http.side_effect = socket.timeout()
                            elif scenario == "empty":
                                http.return_value = Response()
                            elif scenario == "stale":
                                http.return_value = Response([{**raw_result(), "publishedDate": "2020-01-01T00:00:00Z"}])
                            elif scenario == "malformed":
                                http.return_value = Response(body=b'{"results": null}')
                            elif scenario == "no_relevant":
                                http.return_value = Response([{**raw_result(), "title": "Unrelated weather", "content": "Delhi weather is sunny today."}])
                            elif scenario == "contradictory":
                                http.return_value = Response([raw_result(), {**raw_result(), "url": "https://example.net/acme", "content": "Acme CEO is Bob Jones as of 2026-10-24."}])
                            query = "Who is Acme CEO?"
                            kwargs = {"response_plan": AssistantResponsePolicy.plan_voice_response(query)} if voice else {}
                            if stream:
                                events = list(AssistantService.process_message_stream(query, **kwargs))
                                self.assertEqual(events[-1]["type"], "done")
                                metadata = events[-1]
                            else:
                                _, _, metadata, error = AssistantService.process_message(query, **kwargs)
                                self.assertIsNone(error)
                            self.assertEqual(metadata["route"], "online")
                            self.assertTrue(metadata["online_attempted"])
                            self.assertEqual(metadata["online_used"], scenario == "connected")
                            self.assertIn("execution_ms", metadata)
                            self.assertIn("generation_ms", metadata)
                            if scenario != "connected":
                                engine.generate.assert_not_called()
                                engine.generate_stream.assert_not_called()
                                self.assertTrue(metadata["fallback_reason"])
                            if scenario == "contradictory":
                                self.assertEqual(metadata["fallback_reason"], "conflicting_evidence")
                            if scenario in ("stale", "no_relevant"):
                                self.assertEqual(metadata["fallback_reason"], "no_relevant_current_results")

    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_engine")
    def test_personal_and_timeless_requests_never_search_both_transports(self, engine_factory, online):
        result = AIEngineResult(text="Local answer.", engine="local")
        engine_factory.return_value.generate.return_value = result
        engine_factory.return_value.generate_stream.side_effect = lambda *a, **k: iter([AIEngineStreamEvent(type="delta", text=result.text), AIEngineStreamEvent(type="done", result=result)])
        for query in ("What is recursion?", "What's my current schedule?", "According to our notes, what is the latest release?"):
            AssistantService.process_message(query)
            list(AssistantService.process_message_stream(query, response_plan=AssistantResponsePolicy.plan_voice_response(query)))
        online.assert_not_called()

    @patch("assistant.services.get_online_retriever")
    def test_private_query_and_exception_text_never_enter_diagnostics(self, online):
        online.side_effect = RuntimeError("private-password-and-conversation")
        with self.assertLogs("assistant.services", level="INFO") as captured:
            _, _, metadata, error = AssistantService.process_message("Search the web for private-query-marker")
        self.assertIsNone(error)
        self.assertTrue(metadata["online_attempted"])
        self.assertNotIn("private-query-marker", str(captured.output))
        self.assertNotIn("private-password", str(captured.output))

    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_engine")
    def test_explicit_offline_current_request_is_labeled_and_never_guessed(self, engine, online):
        for stream in (False, True):
            query = "Answer offline: who is Acme CEO?"
            if stream:
                metadata = list(AssistantService.process_message_stream(query))[-1]
                text = metadata["response"]
            else:
                _, text, metadata, _ = AssistantService.process_message(query)
            self.assertIn("may be outdated", text)
            self.assertEqual(metadata["fallback_reason"], "offline_requested")
            self.assertFalse(metadata["online_attempted"])
        online.assert_not_called()
        engine.assert_not_called()


class EvidenceTests(SimpleTestCase):
    def test_relevance_dates_and_conflicting_source_instructions(self):
        from assistant.online.base import OnlineRetrievalResult
        result = OnlineRetrievalResult("Acme CEO", "https://example.org", "Acme CEO is Alice Smith.", "searxng", NOW, NOW)
        rejected = [replace(result, published_at=NOW-timedelta(days=10)), replace(result, published_at=None), replace(result, published_at=NOW+timedelta(days=1)), replace(result, title="Unrelated release", content="A Python release is available.")]
        self.assertEqual(select_current_evidence(rejected, "Who is Acme CEO?", NOW), [])
        accepted = select_current_evidence([result], "Who is Acme CEO?", NOW)
        self.assertEqual(accepted, [result])
        instruction, _ = build_online_grounding_instruction(accepted, max_context_chars=2000, query="Who is Acme CEO?", request_time=NOW)
        self.assertIn("If sources disagree", instruction)
        self.assertIn("not a cloud LLM", instruction)
        self.assertIn("Published:", instruction)
