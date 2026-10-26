"""Deterministic, bounded matching of spoken names against persistent uploaded metadata."""
import re
import unicodedata


def normalize_title(value):
    value = re.sub(r'\.(?:pdf|txt|md|markdown)$', '', str(value), flags=re.I)
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    value = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', value)
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))[:255]


DOCUMENT = re.compile(r'\b(?:pdf|documents?|files?|reports?|notes|uploaded|attached)\b|\.(?:txt|md|markdown)\b', re.I)
REFERENCE = re.compile(r'\b(?:same|that|this|its|it|second|third|next|continue|objectives?|conclusions?|sections?)\b', re.I)
CLEAR = re.compile(r'\b(?:clear|forget|deselect|stop using)\b.*\b(?:document|pdf|file|selection|report)\b|\b(?:new conversation|new chat|change (?:the )?topic)\b', re.I)
GENERIC = {'pdf','document','documents','file','files','report','reports','notes','uploaded','attached'}


def requested_name(query):
    patterns = [
        r'what (?:does|do) (?:my |the )?(.+?)\s+say\b',
        r'\b(?:from|according to|in)\s+(?:my |the )?(.+?)(?:\s+(?:i uploaded|explain|say|what|tell)|[,?]|$)',
        r'(?:summari[sz]e|read|explain|select|open|use)\s+(?:my |the )?(.+?)(?:\s+i uploaded|[,.?]|$)',
        r'(?:my|the|uploaded)\s+(.+?\b(?:pdf|document|file|report|notes)\b)',
    ]
    for pattern in patterns:
        found = re.search(pattern, query, re.I)
        if found:
            name = normalize_title(found.group(1))
            name = re.sub(r'\b(?:my|the|that|this|same|i|uploaded|last week|yesterday)\b', ' ', name)
            name = re.sub(r'\b(?:pdf|document|file)\b$', '', name).strip()
            return ' '.join(name.split())
    if (re.search(r'\.(?:pdf|txt|md|markdown)$',query.strip(),re.I) or
            DOCUMENT.search(query) and len(query.split())<=8 and not re.match(r'^(?:what|how|who|where|when|why|take|make|write)\b',query,re.I)):
        return normalize_title(query)
    return ''


def document_request(query):
    if not DOCUMENT.search(query):
        return False
    from assistant.routing import QueryRouter
    private=bool(re.search(r'\b(?:my|our|uploaded|attached|same|that|this)\b',query,re.I))
    selecting=bool(re.match(r'^(?:select|open|use)\b',query,re.I))
    if QueryRouter.requires_online(query,respect_offline=False) and not private and not selecting:
        return False
    if re.match(r'^(?:what (?:is|are) (?:a |an )?|define )(?:pdf|document|report|notes)\b',query,re.I):
        return False
    return bool(requested_name(query) or re.search(r'\b(?:my|our|uploaded|attached|same|that|this)\b',query,re.I))


def match_documents(query, *, now):
    """Never rank by document contents. Return candidates, not a guessed winner."""
    from datetime import timedelta
    from difflib import SequenceMatcher
    from .models import Document
    phrase = requested_name(query)
    tokens = phrase.split()
    base = Document.objects.filter(source_type='MANUAL').exclude(source_identifier__startswith='reference:')
    if 'last week' in query.casefold():
        monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        base = base.filter(uploaded_at__gte=monday-timedelta(days=7), uploaded_at__lt=monday)
    elif 'yesterday' in query.casefold():
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        base = base.filter(uploaded_at__gte=today-timedelta(days=1), uploaded_at__lt=today)
    elif 'today' in query.casefold():
        base = base.filter(uploaded_at__gte=now.replace(hour=0, minute=0, second=0, microsecond=0))
    if re.search(r'\bpdf\b',query,re.I):
        base=base.filter(file_type='application/pdf')
    if 'report' in tokens: base=base.filter(normalized_title__contains='report')
    if 'notes' in tokens: base=base.filter(normalized_title__contains='notes')
    suffix=re.search(r'\.(txt|md|markdown)$',query.strip(),re.I)
    if suffix: base=base.filter(file_type='text/plain' if suffix[1].lower()=='txt' else 'text/markdown')
    distinct = [t for t in tokens if t not in GENERIC]
    # Exact/prefix matches use the persisted type/title index. Fetch one extra
    # candidate to detect overflow instead of silently selecting from a subset.
    exact=[]
    if phrase:
        exact=list(base.filter(normalized_title=phrase).order_by('-uploaded_at')[:9])
        prefix=phrase+' '
        exact+=list(base.filter(normalized_title__gte=prefix,normalized_title__lt=prefix+'\uffff').order_by('normalized_title')[:9])
    if exact:
        return exact[:8], len(exact)>8
    if not distinct:
        if 'report' in tokens: base=base.filter(normalized_title__contains='report')
        if 'notes' in tokens: base=base.filter(normalized_title__contains='notes')
        values = list(base.order_by('-uploaded_at')[:9])
        return values[:8], len(values)>8
    # SQLite cannot index edit distance. A hard metadata-only cap bounds fuzzy
    # work and memory. On overflow, clarify; never pick an unrelated recent file.
    candidates = list(base.only('id','filename','normalized_title','status','uploaded_at','file_type').order_by('-uploaded_at')[:129])
    if len(candidates)>128:
        return [], True
    scored=[]
    for doc in candidates:
        words=(doc.normalized_title or normalize_title(doc.filename)).split()
        coverage=sum(max((SequenceMatcher(None,t,w).ratio() for w in words), default=0) for t in distinct)/len(distinct)
        # Every meaningful name token must be well supported. Generic names
        # cannot win simply because 'report' occurs in unrelated filenames.
        if coverage < .83 or any(max((SequenceMatcher(None,t,w).ratio() for w in words),default=0)<.66 for t in distinct):
            continue
        scored.append((coverage,doc))
    scored.sort(key=lambda item:(-item[0],item[1].pk))
    if not scored:
        return [], False
    top=scored[0][0]
    return [doc for score,doc in scored if top-score<.16][:8], False


def topic_query(query, document):
    """Remove only known naming/scaffolding, retaining the requested topic."""
    value=normalize_title(query)
    reference=requested_name(query)
    if reference: value=value.replace(reference,' ',1)
    names=set()
    stop={'explain','summarize','summarise','according','from','what','does','do','is','are','was','were','my','the','a','an','in','that','same','this','pdf','file','document','report','notes','uploaded','i','say','says','about','please','last','week','yesterday','today','first','second','third','next','it','its','of','and','to','continue'}
    words=[{'objectives':'objective','conclusions':'conclusion'}.get(w,w) for w in value.split() if w not in names and w not in stop]
    return ' '.join(words)
