from django.views.generic import TemplateView
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings

from django.db.models import Count

from conversations.models import Conversation, Message

from knowledge_base.models import Document

from system.models import SystemLog, SystemSetting
from system.control_plane.provisioning import get_setup_status, setup_mode_available



class DashboardView(TemplateView):

    template_name = 'dashboard/index.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['doc_count'] = Document.objects.count()

        context['recent_conversations'] = Conversation.objects.order_by('-created_at')[:5]

        context['recent_logs'] = SystemLog.objects.order_by('-timestamp')[:5]

        return context



class AssistantView(TemplateView):

    template_name = 'dashboard/assistant.html'



    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        context['chat_request_timeout_ms'] = settings.CHAT_REQUEST_TIMEOUT_SECONDS * 1000



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


@method_decorator(ensure_csrf_cookie, name='dispatch')
class NetworkView(TemplateView):

    template_name = 'dashboard/network.html'



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


@method_decorator(ensure_csrf_cookie, name='dispatch')
class SetupView(TemplateView):
    template_name = 'setup/index.html'

    def dispatch(self, request, *args, **kwargs):
        if not setup_mode_available():
            raise Http404("Wi-Fi setup is not active.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_setup_status())
        return context
