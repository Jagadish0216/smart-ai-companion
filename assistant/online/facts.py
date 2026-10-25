"""Small, attributed date facts; all broader questions retain grounded generation."""
import re
from urllib.parse import urlsplit

from .dates import parse_publication_date

_DATE = r'(?:20\d{2}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+ 20\d{2}|[A-Za-z]+ \d{1,2},? 20\d{2})'
_CLAIM = re.compile(
    r'^(?P<subject>[A-Z][\w ’\':.-]{1,100}?)\s+'
    r'(?P<relation>is scheduled to release|will release|will not release|was released|'
    r'is scheduled to launch|will launch|will not launch|was launched|was announced|was not announced)'
    r'\s+on\s+(?P<date>'+_DATE+r')[.!]?$', re.I)
_QUESTION = re.compile(
    r'^When (?:is (?P<scheduled>.+?) scheduled to (?P<action>release|launch)|'
    r'will (?P<future>.+?) (?P<future_action>release|launch)|'
    r'was (?P<past>.+?) (?P<past_action>released|launched|announced))[?.! ]*$', re.I)


def factual_question(query):
    return _QUESTION.fullmatch(query.strip())


def subject_key(subject):
    subject = subject.casefold().strip()
    return re.sub(r'^(?:(?:the|telugu|tamil|malayalam|hindi|films?|movies?|cinema)[: ]+)+', '', subject)


def dated_claims(text):
    """Only explicit date relations; never derive dates from event metadata."""
    for piece in re.split(r'\n+|(?<=[.!?])\s+', text):
        match = _CLAIM.fullmatch(piece.strip())
        if match and (stamp := parse_publication_date(match['date'])):
            relation = match['relation'].lower()
            event = 'announcement' if 'announced' in relation else 'release' if 'release' in relation else 'launch'
            yield subject_key(match['subject']), event, stamp, 'not' in relation.split(), piece.strip()


def source_supported_answer(query, results, request_time):
    """Return an exact source-attributed claim only for one explicit date question."""
    from .grounding import select_current_evidence, has_conflicting_claims
    from .searxng import _safe_public_url
    question = factual_question(query)
    if not question or not results:
        return None
    accepted = select_current_evidence(results, query, request_time)
    if len(accepted) != len(results) or has_conflicting_claims(accepted, query):
        return None
    subject = question['scheduled'] or question['future'] or question['past']
    action = question['action'] or question['future_action'] or question['past_action']
    event = 'announcement' if action == 'announced' else 'release' if action in {'release', 'released'} else 'launch'
    claims = []
    for result in accepted:
        if (result.partial_response or result.accessibility != 'available' or result.evidence_kind not in {'fetched_report', 'fetched_reference'}
                or result.publication_source != 'article_metadata' or not result.published_at
                or not result.verified_at or not 0 <= (request_time-result.verified_at).total_seconds() <= 300
                or urlsplit(result.verified_url).scheme != 'https'
                or not _safe_public_url(result.verified_url)
                or any(label in (result.claim_status+','+result.entertainment_status)
                       for label in ('rumour', 'unconfirmed', 'denied', 'outdated'))
                or re.search(r'\b(?:rumou?rs?|reportedly|unconfirmed|speculation|expected|may(?!\s+\d)|might|could|not|denied)\b', result.content, re.I)):
            return None
        extracted = list(dated_claims(result.content))
        if len(extracted) != 1 or len(result.content) > 350:
            return None
        name, kind, stamp, denied, sentence = extracted[0]
        if name != subject_key(subject) or event != kind or denied:
            return None
        scheduled = bool(question['scheduled'] or question['future'])
        if scheduled != bool(re.search(r'\b(?:scheduled|will)\b', sentence, re.I)):
            return None
        if (scheduled and stamp.date() < request_time.date()) or (not scheduled and stamp.date() > request_time.date()):
            return None
        claims.append((stamp.date(), result, sentence))
    if len({date for date, _, _ in claims}) != 1:
        return None
    _, source, sentence = claims[0]
    # The attribution qualifies this as a dated source claim, not model knowledge
    # or independent confirmation. Exact text avoids paraphrase/tense inference.
    return (f'Source report (published {source.published_at.date().isoformat()}; '
            f'last verified {source.verified_at.isoformat()}): {sentence}')
