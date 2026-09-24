from django.views.generic import TemplateView

from django.db.models import Count

from conversations.models import Conversation, Message

from knowledge_base.models import Document

from system.models import SystemLog, SystemSetting



class DashboardView(TemplateView):

    template_name = 'dashboard/index.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['doc_count'] = Document.objects.count()

        context['recent_conversations'] = Conversation.objects.order_by('-created_at')[:5]

        context['recent_logs'] = SystemLog.objects.order_by('-timestamp')[:5]

        return context



from assistant.ai_engine import get_engine

from django.conf import settings



class AssistantView(TemplateView):

    template_name = 'dashboard/assistant.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        try:

            engine = get_engine()

            context['ai_engine'] = engine.engine_name.upper()

        except Exception:

            context['ai_engine'] = settings.AI_ENGINE.upper()



        context['ai_model'] = getattr(settings, 'OLLAMA_MODEL', 'Unknown') if context['ai_engine'] == 'LOCAL' else 'Mocked'

        context['ai_mode'] = 'Offline'



        # Pass conversation history to the template for the left sidebar

        context['conversations'] = Conversation.objects.order_by('-created_at')[:20]



        return context



class ConversationsView(TemplateView):

    template_name = 'dashboard/conversations.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['conversations'] = (

            Conversation.objects

            .annotate(message_count=Count('messages'))

            .order_by('-created_at')

        )

        return context



class KnowledgeBaseView(TemplateView):

    template_name = 'dashboard/knowledge.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        documents = Document.objects.all().order_by('-uploaded_at')

        context['documents'] = documents

        context['indexed_count'] = documents.filter(status='INDEXED').count()

        context['pending_count'] = documents.filter(status='PENDING').count()

        return context



class DeviceStatusView(TemplateView):

    template_name = 'dashboard/device.html'



class LogsView(TemplateView):

    template_name = 'dashboard/logs.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['logs'] = SystemLog.objects.all().order_by('-timestamp')[:100]

        return context



class SettingsView(TemplateView):

    template_name = 'dashboard/settings.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['settings'] = SystemSetting.objects.all()

        return context



class CompanionStudioView(TemplateView):

    template_name = 'dashboard/studio.html'
