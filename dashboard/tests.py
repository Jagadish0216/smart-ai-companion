from django.test import TestCase, override_settings


class AssistantChatTimeoutTests(TestCase):
    @override_settings(CHAT_REQUEST_TIMEOUT_SECONDS=45)
    def test_chat_post_timeout_is_configurable(self):
        response = self.client.get('/assistant/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'const CHAT_REQUEST_TIMEOUT_MS = 45000;')
        self.assertContains(response, 'new AbortController()')
        self.assertContains(response, 'signal: controller.signal')
        self.assertContains(response, 'AI request timed out. Please try again.')
