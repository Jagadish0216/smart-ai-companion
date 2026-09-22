from django.core.management.base import BaseCommand
from system.models import SystemLog, SystemSetting
from conversations.models import Conversation, Message
from knowledge_base.models import Document

class Command(BaseCommand):
    help = 'Sets up initial demo data for the Smart AI Companion'

    def handle(self, *args, **kwargs):
        self.stdout.write('Clearing old demo data...')
        SystemLog.objects.all().delete()
        Conversation.objects.all().delete()
        Document.objects.all().delete()
        SystemSetting.objects.all().delete()

        self.stdout.write('Creating System Settings...')
        SystemSetting.objects.create(key='assistant_name', value='Smart AI Companion')
        SystemSetting.objects.create(key='ai_mode', value='LOCAL')
        SystemSetting.objects.create(key='voice_enabled', value='True')

        self.stdout.write('Creating System Logs...')
        logs = [
            {'level': 'INFO', 'component': 'SYSTEM', 'message': 'Boot sequence initiated.'},
            {'level': 'INFO', 'component': 'NETWORK', 'message': 'Connected to local edge network.'},
            {'level': 'INFO', 'component': 'RAG_ENGINE', 'message': 'Knowledge base synchronized. 12 documents indexed.'},
            {'level': 'INFO', 'component': 'CORE_LLM', 'message': 'Simulated local model loaded.'},
            {'level': 'WARN', 'component': 'HARDWARE', 'message': 'CPU temperature slightly above nominal (62°C).'},
        ]
        for log in logs:
            SystemLog.objects.create(**log)

        self.stdout.write('Creating Conversations...')
        conv = Conversation.objects.create(title='Edge deployment strategies')
        Message.objects.create(conversation=conv, sender='USER', text='How do I deploy a model to a Raspberry Pi?')
        Message.objects.create(conversation=conv, sender='AI', text='Deploying to a Raspberry Pi typically involves using a quantized model (like GGUF) and a lightweight runner such as llama.cpp to optimize RAM usage.')

        self.stdout.write('Creating Knowledge Documents (Metadata)...')
        Document.objects.create(filename='rpi_optimization_guide.pdf', file_type='application/pdf', status='INDEXED')
        Document.objects.create(filename='edge_ai_frameworks.md', file_type='text/markdown', status='INDEXED')

        self.stdout.write(self.style.SUCCESS('Successfully setup demo data!'))
