"""Server-side hardware document scope. Process restart/new conversation never inherits stale intent."""
from dataclasses import dataclass
from datetime import timedelta
import re
import uuid
from django.conf import settings
from django.utils import timezone
from assistant import latency_trace as latency
from knowledge_base.document_names import DOCUMENT, REFERENCE, CLEAR, document_request, match_documents, topic_query

_PROCESS_TOKEN = uuid.uuid4().hex

@dataclass
class VoiceScope:
    state: object
    document: object = None
    message: str | None = None
    retrieval_query: str = ''
    overview: bool = False
    reset_conversation: bool = False

    def bind(self, conversation):
        self.state.conversation = conversation
        self.state.save()


@latency.span('document_resolve')
def resolve(query, conversation_id, session_key):
    from conversations.models import HardwareDocumentScope
    from knowledge_base.models import Document
    now=timezone.localtime(timezone.now())
    state,_=HardwareDocumentScope.objects.get_or_create(session_key=session_key, defaults={'process_token':_PROCESS_TOKEN,'expires_at':now})
    had_scope=bool(state.document_id or state.document_indexed_at or state.pending_document_ids)
    expired=state.process_token!=_PROCESS_TOKEN or state.expires_at<=now or (conversation_id is not None and state.conversation_id!=conversation_id)
    if expired:
        state.document=None;state.document_indexed_at=None;state.conversation=None;state.pending_document_ids=[];state.chunk_cursor=-1
    state.process_token=_PROCESS_TOKEN
    state.expires_at=now+timedelta(seconds=max(60,min(3600,int(getattr(settings,'PHYSICAL_DOCUMENT_SCOPE_SECONDS',1800)))))
    request=VoiceScope(state)
    if CLEAR.search(query):
        state.document=None;state.document_indexed_at=None;state.pending_document_ids=[];state.chunk_cursor=-1
        request.reset_conversation=bool(re.search(r'\bnew (?:chat|conversation)\b',query,re.I))
        request.message='Document selection cleared. What would you like to ask?'
        return request
    # A pending ambiguity accepts only an ordinal or a newly explicit name.
    ordinal=re.fullmatch(r'(?:the )?(first|second|third|fourth|fifth)(?: one| file| document| pdf)?[.!? ]*',query,re.I)
    if ordinal and state.pending_document_ids:
        index=['first','second','third','fourth','fifth'].index(ordinal.group(1).lower())
        candidates=list(Document.objects.filter(pk__in=state.pending_document_ids,source_type='MANUAL'))
        by_id={d.pk:d for d in candidates}
        selected=by_id.get(state.pending_document_ids[index]) if index<len(state.pending_document_ids) else None
        if selected:
            request.document=selected;request.overview=True
        else:
            request.message='That choice is unavailable. Please say the filename again.'
            state.pending_document_ids=[]
    elif state.pending_document_ids and not document_request(query):
        candidates,overflow=match_documents('Summarize '+query,now=now)
        candidates=[doc for doc in candidates if doc.pk in state.pending_document_ids]
        if len(candidates)==1 and not overflow:
            request.document=candidates[0];request.overview=True
        else:
            request.message='Please say the exact filename or the number of one of those documents.'
    elif re.search(r'\b(?:same|that|this)\b',query,re.I) and DOCUMENT.search(query) and not state.document_id:
        request.message='Which uploaded document do you mean? Please say its filename.'
    elif document_request(query) and not (re.search(r'\b(?:same|that|this)\b',query,re.I) and state.document_id):
        candidates,overflow=match_documents(query,now=now)
        if overflow or len(candidates)>1:
            state.pending_document_ids=[doc.pk for doc in candidates[:3]];state.document=None;state.document_indexed_at=None
            labels='; '.join(f'{index+1}: {doc.filename[:60]}' for index,doc in enumerate(candidates[:3]))
            request.message=('Several documents could match. Which filename do you mean?' if overflow else 'Which document do you mean? '+labels)
        elif not candidates:
            state.document=None;state.document_indexed_at=None;state.pending_document_ids=[]
            request.message="I couldn't find an uploaded document matching that name. Please say its filename."
        else:
            request.document=candidates[0]
    elif state.document_id and (conversation_id==state.conversation_id or REFERENCE.search(query)):
        # Obvious new subjects/current questions leave the document scope.
        change=bool(re.search(r'\b(?:weather|latest news|current price|stock price|time is it)\b',query,re.I))
        if not change:
            request.document=Document.objects.filter(pk=state.document_id).first()
        else:
            state.document=None;state.document_indexed_at=None;state.pending_document_ids=[]
    elif had_scope and not state.document_id and REFERENCE.search(query):
        request.message='The previous document scope is no longer active. Please say the filename again.'
    doc=request.document
    if doc:
        if doc.status!='INDEXED' or not doc.chunks.exists():
            request.message='That document is not ready for questions. Please finish indexing it in Memory.'
            request.document=None
            state.document=None;state.document_indexed_at=None;state.pending_document_ids=[];state.chunk_cursor=-1
        else:
            if state.document_id!=doc.pk or state.document_indexed_at!=doc.indexed_at: state.chunk_cursor=-1
            state.document_indexed_at=doc.indexed_at
            state.document=doc;state.pending_document_ids=[]
            request.retrieval_query=topic_query(query,doc)
            request.overview=request.overview or not request.retrieval_query or bool(re.search(r'\b(?:summari[sz]e|continue|read|overview|select|open|use)\b',query,re.I))
            if re.search(r'\b(?:summari[sz]e|overview)\b',query,re.I): state.chunk_cursor=-1
    elif state.document_id and not request.message:
        state.document=None
        request.message='That document is no longer available. Please name another uploaded file.'
    return request
