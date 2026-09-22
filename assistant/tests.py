"""
Tests for the Assistant app.

Tests cover:
- Original API contract (preserved from baseline)
- AI engine abstraction (MockAIEngine)
- Engine factory and configuration
- LocalLLMEngine with MOCKED Ollama responses (no real Ollama required)
- Error handling (unavailable, model not found, timeout, malformed)
- AssistantService integration
"""

import io
import json
import urllib.error
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from conversations.models import Conversation, Message
from assistant.ai_engine import get_engine, reset_engine, AIEngineResult, EngineUnavailableError
from assistant.ai_engine.base import AIEngine
from assistant.ai_engine.mock import MockAIEngine
from assistant.ai_engine.local import LocalLLMEngine


# ─── Original API Tests (preserved from baseline) ──────────────────

class AssistantAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/assistant/chat/'

    def test_valid_chat_request(self):
        response = self.client.post(self.url, {'query': 'Hello'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('conversation_id', response.data)
        self.assertIn('response', response.data)
        
        # Verify persistence
        conv_id = response.data['conversation_id']
        conv = Conversation.objects.get(id=conv_id)
        self.assertEqual(conv.messages.count(), 2)

    def test_empty_query(self):
        response = self.client.post(self.url, {'query': '   '}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_conversation_id(self):
        response = self.client.post(self.url, {'query': 'Hello', 'conversation_id': 999}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_continue_conversation(self):
        conv = Conversation.objects.create(title='Test')
        response = self.client.post(self.url, {'query': 'Hello again', 'conversation_id': conv.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['conversation_id'], conv.id)
        self.assertEqual(conv.messages.count(), 2)

    def test_get_conversation_history(self):
        conv = Conversation.objects.create(title='History Test')
        Message.objects.create(conversation=conv, sender='USER', text='Hi')
        Message.objects.create(conversation=conv, sender='AI', text='Hello')
        
        response = self.client.get(f"{self.url}?conversation_id={conv.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['messages']), 2)
        self.assertEqual(response.data['messages'][0]['content'], 'Hi')

    def test_get_invalid_conversation(self):
        response = self.client.get(f"{self.url}?conversation_id=999")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ─── Engine Abstraction Tests ──────────────────────────────────────

class MockAIEngineTests(TestCase):
    def setUp(self):
        self.engine = MockAIEngine()

    def test_engine_name(self):
        self.assertEqual(self.engine.engine_name, "mock")

    def test_returns_valid_result(self):
        result = self.engine.generate("Hello")
        self.assertIsInstance(result, AIEngineResult)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.text)
        self.assertGreater(len(result.text), 0)
        self.assertEqual(result.engine, "mock")
        self.assertEqual(result.mode, "offline")
        self.assertIsNone(result.model)
        self.assertGreater(result.latency_ms, 0)
        self.assertIsNone(result.error)

    def test_keyword_hello(self):
        result = self.engine.generate("hi there")
        self.assertIn("Smart AI Companion", result.text)

    def test_keyword_status(self):
        result = self.engine.generate("system status check")
        self.assertIn("online", result.text.lower())

    def test_keyword_knowledge(self):
        result = self.engine.generate("search my documents")
        self.assertIn("documents", result.text.lower())

    def test_fallback_response(self):
        result = self.engine.generate("arbitrary test input 42 xkcd")
        self.assertIn("arbitrary test input 42 xkcd", result.text)
        self.assertIn("mock", result.text.lower())

    def test_health_check(self):
        self.assertTrue(self.engine.health_check())

    def test_conversation_history_accepted(self):
        """Ensure generate() accepts conversation_history without error."""
        history = [
            {"role": "USER", "content": "Previous question"},
            {"role": "AI", "content": "Previous answer"},
        ]
        result = self.engine.generate("Follow up", conversation_history=history)
        self.assertTrue(result.success)


# ─── Engine Factory Tests ──────────────────────────────────────────

class EngineFactoryTests(TestCase):
    def setUp(self):
        reset_engine()

    def tearDown(self):
        reset_engine()

    def test_get_mock_engine(self):
        engine = get_engine("mock")
        self.assertIsInstance(engine, MockAIEngine)
        self.assertEqual(engine.engine_name, "mock")

    def test_get_local_engine(self):
        engine = get_engine("local")
        self.assertIsInstance(engine, LocalLLMEngine)
        self.assertEqual(engine.engine_name, "local")

    def test_invalid_engine_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_engine("nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("Available engines", str(ctx.exception))

    @override_settings(AI_ENGINE="mock")
    def test_reads_from_settings(self):
        engine = get_engine()
        self.assertIsInstance(engine, MockAIEngine)

    def test_caches_instance(self):
        engine1 = get_engine("mock")
        engine2 = get_engine("mock")
        self.assertIs(engine1, engine2)

    def test_reset_clears_cache(self):
        engine1 = get_engine("mock")
        reset_engine()
        engine2 = get_engine("mock")
        self.assertIsNot(engine1, engine2)


# ─── LocalLLMEngine Tests (no real Ollama required) ───────────────

class LocalLLMEngineTests(TestCase):
    """Tests that don't require a real Ollama server."""

    def setUp(self):
        self.engine = LocalLLMEngine(host="http://localhost:99999")

    def test_engine_name(self):
        self.assertEqual(self.engine.engine_name, "local")

    def test_health_check_fails_when_unavailable(self):
        self.assertFalse(self.engine.health_check())

    def test_generate_raises_when_unavailable(self):
        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test query")
        self.assertIn("Cannot reach Ollama", str(ctx.exception))

    def test_prompt_building(self):
        """Test legacy prompt construction."""
        history = [
            {"role": "USER", "content": "What is edge AI?"},
            {"role": "AI", "content": "Edge AI runs models locally."},
        ]
        prompt = self.engine._build_prompt("Tell me more", history)
        self.assertIn("User: What is edge AI?", prompt)
        self.assertIn("Assistant: Edge AI runs models locally.", prompt)
        self.assertIn("User: Tell me more", prompt)
        self.assertTrue(prompt.endswith("Assistant:"))

    def test_message_building(self):
        """Test /api/chat message array construction."""
        history = [
            {"role": "USER", "content": "What is edge AI?"},
            {"role": "AI", "content": "Edge AI runs models locally."},
        ]
        messages = self.engine._build_messages("Tell me more", history)

        # First message should be system prompt
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Smart AI Companion", messages[0]["content"])

        # History messages
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "What is edge AI?")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Edge AI runs models locally.")

        # Current query
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "Tell me more")

    def test_message_building_no_history(self):
        """Test message construction with no conversation history."""
        messages = self.engine._build_messages("Hello", None)
        self.assertEqual(len(messages), 2)  # system + user
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hello")


class LocalLLMEngineMockedTests(TestCase):
    """Tests with mocked HTTP responses simulating a real Ollama server."""

    def setUp(self):
        self.engine = LocalLLMEngine(
            host="http://localhost:11434",
            model="llama3.2:1b",
            timeout=30,
        )

    def _make_mock_response(self, body_dict, status_code=200):
        """Create a mock urllib response object."""
        body_bytes = json.dumps(body_dict).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.status = status_code
        mock_resp.read.return_value = body_bytes
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_successful_chat_response(self, mock_urlopen):
        """Mocked successful Ollama /api/chat response."""
        # First call: health_check (/api/tags)
        health_resp = self._make_mock_response({"models": []})
        # Second call: /api/chat
        chat_resp = self._make_mock_response({
            "message": {
                "role": "assistant",
                "content": "Hello! I'm your Smart AI Companion."
            },
            "eval_count": 15,
            "eval_duration": 500000000,  # 500ms in nanoseconds
            "total_duration": 800000000,
        })
        mock_urlopen.side_effect = [health_resp, chat_resp]

        result = self.engine.generate("Hello")

        self.assertIsInstance(result, AIEngineResult)
        self.assertTrue(result.success)
        self.assertEqual(result.text, "Hello! I'm your Smart AI Companion.")
        self.assertEqual(result.engine, "local")
        self.assertEqual(result.model, "llama3.2:1b")
        self.assertEqual(result.mode, "offline")
        self.assertGreaterEqual(result.latency_ms, 0)

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_successful_with_conversation_history(self, mock_urlopen):
        """Mocked response with conversation history context."""
        health_resp = self._make_mock_response({"models": []})
        chat_resp = self._make_mock_response({
            "message": {"role": "assistant", "content": "Edge AI processes data locally."},
            "eval_count": 8,
            "eval_duration": 300000000,
        })
        mock_urlopen.side_effect = [health_resp, chat_resp]

        history = [
            {"role": "USER", "content": "What is edge AI?"},
            {"role": "AI", "content": "It runs models on the device."},
        ]
        result = self.engine.generate("Tell me more", conversation_history=history)

        self.assertTrue(result.success)
        self.assertEqual(result.text, "Edge AI processes data locally.")

        # Verify the chat request included history
        call_args = mock_urlopen.call_args_list[1]  # Second call is /api/chat
        request_obj = call_args[0][0]
        sent_payload = json.loads(request_obj.data.decode("utf-8"))
        # system + 2 history msgs + current query = 4 messages
        self.assertEqual(len(sent_payload["messages"]), 4)

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_model_not_found_error(self, mock_urlopen):
        """Ollama returns 404 when model is not pulled."""
        health_resp = self._make_mock_response({"models": []})
        mock_urlopen.side_effect = [
            health_resp,
            urllib.error.HTTPError(
                url="http://localhost:11434/api/chat",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=io.BytesIO(json.dumps({"error": "model 'llama3.2:1b' not found"}).encode()),
            ),
        ]

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        self.assertIn("not found", str(ctx.exception).lower())
        self.assertIn("ollama pull", str(ctx.exception).lower())

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_server_error_response(self, mock_urlopen):
        """Ollama returns 500 server error."""
        health_resp = self._make_mock_response({"models": []})
        mock_urlopen.side_effect = [
            health_resp,
            urllib.error.HTTPError(
                url="http://localhost:11434/api/chat",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=io.BytesIO(json.dumps({"error": "model loading failed"}).encode()),
            ),
        ]

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        self.assertIn("500", str(ctx.exception))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_connection_refused(self, mock_urlopen):
        """Ollama server is not running — connection refused."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        self.assertIn("Cannot reach Ollama", str(ctx.exception))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_malformed_json_response(self, mock_urlopen):
        """Ollama returns non-JSON garbage."""
        health_resp = self._make_mock_response({"models": []})
        # Second call returns invalid JSON
        bad_resp = MagicMock()
        bad_resp.status = 200
        bad_resp.read.return_value = b"not valid json at all"
        bad_resp.__enter__ = MagicMock(return_value=bad_resp)
        bad_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [health_resp, bad_resp]

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        self.assertIn("Invalid JSON", str(ctx.exception))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_empty_response_content(self, mock_urlopen):
        """Ollama returns valid JSON but empty content."""
        health_resp = self._make_mock_response({"models": []})
        chat_resp = self._make_mock_response({
            "message": {"role": "assistant", "content": ""},
        })
        mock_urlopen.side_effect = [health_resp, chat_resp]

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        self.assertIn("empty response", str(ctx.exception).lower())

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        """Ollama request times out."""
        import socket
        health_resp = self._make_mock_response({"models": []})
        mock_urlopen.side_effect = [
            health_resp,
            urllib.error.URLError(socket.timeout("timed out")),
        ]

        with self.assertRaises(EngineUnavailableError) as ctx:
            self.engine.generate("test")
        # Should be caught by the generic Exception handler
        self.assertIsNotNone(str(ctx.exception))


# ─── Integration: AssistantService + Engine ────────────────────────

class AssistantServiceEngineIntegrationTests(TestCase):
    def setUp(self):
        reset_engine()

    def tearDown(self):
        reset_engine()

    @override_settings(AI_ENGINE="mock")
    def test_service_uses_mock_engine(self):
        from assistant.services import AssistantService
        conv, text, metadata, error = AssistantService.process_message("Hello")
        self.assertIsNone(error)
        self.assertIsNotNone(text)
        self.assertIn("Smart AI Companion", text)
        self.assertEqual(metadata["engine"], "mock")
        self.assertEqual(metadata["mode"], "offline")

    @override_settings(AI_ENGINE="mock")
    def test_processing_time_recorded(self):
        from assistant.services import AssistantService
        conv, text, metadata, error = AssistantService.process_message("status check")
        self.assertIsNone(error)
        self.assertGreater(metadata["latency_ms"], 0)

        # Verify processing_time_ms is saved on the AI message
        ai_msg = conv.messages.filter(sender='AI').first()
        self.assertIsNotNone(ai_msg)
        self.assertIsNotNone(ai_msg.processing_time_ms)
        self.assertGreater(ai_msg.processing_time_ms, 0)

    @override_settings(AI_ENGINE="local")
    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_unavailable_engine_returns_error(self, mock_urlopen):
        """When local engine is unavailable, service returns clean error."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        
        from assistant.services import AssistantService
        conv, text, metadata, error = AssistantService.process_message("test")
        self.assertIsNotNone(error)
        self.assertIn("unavailable", error.lower())
        self.assertIsNone(text)
        self.assertIsNone(metadata)
        # User message should still be saved
        self.assertEqual(conv.messages.filter(sender='USER').count(), 1)
        # AI message should NOT be saved on failure
        self.assertEqual(conv.messages.filter(sender='AI').count(), 0)

    @override_settings(AI_ENGINE="local")
    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_service_with_mocked_local_engine(self, mock_urlopen):
        """Full service integration with mocked Ollama."""
        from assistant.services import AssistantService

        # Mock health check + chat response
        health_body = json.dumps({"models": []}).encode()
        health_resp = MagicMock()
        health_resp.status = 200
        health_resp.read.return_value = health_body
        health_resp.__enter__ = MagicMock(return_value=health_resp)
        health_resp.__exit__ = MagicMock(return_value=False)

        chat_body = json.dumps({
            "message": {"role": "assistant", "content": "The Raspberry Pi is a small computer."},
            "eval_count": 12,
            "eval_duration": 400000000,
        }).encode()
        chat_resp = MagicMock()
        chat_resp.status = 200
        chat_resp.read.return_value = chat_body
        chat_resp.__enter__ = MagicMock(return_value=chat_resp)
        chat_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [health_resp, chat_resp]

        conv, text, metadata, error = AssistantService.process_message("What is Raspberry Pi?")

        self.assertIsNone(error)
        self.assertEqual(text, "The Raspberry Pi is a small computer.")
        self.assertEqual(metadata["engine"], "local")
        self.assertEqual(metadata["model"], "llama3.2:1b")
        self.assertEqual(metadata["mode"], "offline")

        # Verify persistence
        self.assertEqual(conv.messages.filter(sender='USER').count(), 1)
        self.assertEqual(conv.messages.filter(sender='AI').count(), 1)
        ai_msg = conv.messages.get(sender='AI')
        self.assertEqual(ai_msg.text, "The Raspberry Pi is a small computer.")


# ─── API Response Metadata Tests ────────────────────────────────

class APIResponseMetadataTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/assistant/chat/'
        reset_engine()

    def tearDown(self):
        reset_engine()

    @override_settings(AI_ENGINE="mock")
    def test_response_includes_engine_metadata(self):
        response = self.client.post(self.url, {'query': 'Hello'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Original contract preserved
        self.assertIn('conversation_id', response.data)
        self.assertIn('response', response.data)
        # New metadata fields
        self.assertEqual(response.data['engine'], 'mock')
        self.assertEqual(response.data['mode'], 'offline')

    @override_settings(AI_ENGINE="mock")
    def test_existing_frontend_contract_preserved(self):
        """The two fields the frontend relies on must always be present."""
        response = self.client.post(self.url, {'query': 'Hello'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data['conversation_id'], int)
        self.assertIsInstance(response.data['response'], str)
        self.assertGreater(len(response.data['response']), 0)

    @override_settings(AI_ENGINE="local")
    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_unavailable_engine_returns_500(self, mock_urlopen):
        """When local engine is not reachable, API returns 500 with error."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        
        response = self.client.post(self.url, {'query': 'Hello'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn('error', response.data)

    @override_settings(AI_ENGINE="local")
    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_local_engine_api_response(self, mock_urlopen):
        """Full API response with mocked local engine."""
        health_body = json.dumps({"models": []}).encode()
        health_resp = MagicMock()
        health_resp.status = 200
        health_resp.read.return_value = health_body
        health_resp.__enter__ = MagicMock(return_value=health_resp)
        health_resp.__exit__ = MagicMock(return_value=False)

        chat_body = json.dumps({
            "message": {"role": "assistant", "content": "2 plus 2 is 4."},
            "eval_count": 8,
            "eval_duration": 200000000,
        }).encode()
        chat_resp = MagicMock()
        chat_resp.status = 200
        chat_resp.read.return_value = chat_body
        chat_resp.__enter__ = MagicMock(return_value=chat_resp)
        chat_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [health_resp, chat_resp]

        response = self.client.post(self.url, {'query': 'What is 2+2?'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['response'], '2 plus 2 is 4.')
        self.assertEqual(response.data['engine'], 'local')
        self.assertEqual(response.data['model'], 'llama3.2:1b')
        self.assertEqual(response.data['mode'], 'offline')
