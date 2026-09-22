from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import CompanionState

class SystemAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.control_url = '/api/system/companion/control/'
        self.state_url = '/api/system/companion/state/'
        self.metrics_url = '/api/system/metrics/'

    def test_valid_companion_command(self):
        response = self.client.post(self.control_url, {'action': 'set_state', 'value': 'THINKING'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'THINKING') # verify returning full state
        
        # Verify state changed in DB
        state_response = self.client.get(self.state_url)
        self.assertEqual(state_response.data['state'], 'THINKING')

    def test_other_companion_commands_persist(self):
        self.client.post(self.control_url, {'action': 'set_expression', 'value': 'HAPPY'}, format='json')
        self.client.post(self.control_url, {'action': 'set_eye_style', 'value': 'SLIT'}, format='json')
        self.client.post(self.control_url, {'action': 'set_animation', 'value': 'SCAN'}, format='json')
        self.client.post(self.control_url, {'action': 'set_brightness', 'value': 45}, format='json')
        self.client.post(self.control_url, {'action': 'set_display_text', 'value': 'Test Text'}, format='json')

        state_response = self.client.get(self.state_url)
        data = state_response.data
        self.assertEqual(data['expression'], 'HAPPY')
        self.assertEqual(data['eye_style'], 'SLIT')
        self.assertEqual(data['animation'], 'SCAN')
        self.assertEqual(data['brightness'], 45)
        self.assertEqual(data['display_text'], 'Test Text')

    def test_invalid_companion_command(self):
        response = self.client.post(self.control_url, {'action': 'invalid_action', 'value': 'blah'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_device_metrics_format(self):
        response = self.client.get(self.metrics_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        expected_keys = [
            'device_name', 'status', 'cpu_percent', 'ram_percent', 
            'ram_used_gb', 'ram_total_gb', 'storage_percent', 
            'temperature_c', 'network', 'audio_status', 'display_status'
        ]
        for key in expected_keys:
            self.assertIn(key, response.data)
