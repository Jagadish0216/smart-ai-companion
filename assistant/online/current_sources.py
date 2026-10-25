"""Small, conservative current-state rules over fetched text, never search titles."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit


def historical_query(query):
    if re.search(r'\b(?:who was|who were|history of|historical|biography)\b', query, re.I):
        return True
    years = re.findall(r'\bin\s+((?:19|20)\d{2})\b', query, re.I)
    return bool(years and all(int(year) < datetime.now(timezone.utc).year for year in years))


def current_topic(query):
    text = query.casefold()
    if re.fullmatch(r'what is (?:a |the )?(?:chief minister|prime minister|president|governor|mayor)[?.! ]*', text.strip()):
        return ''
    if historical_query(text):
        return ''
    if re.search(r'\b(?:was|were)\b', text) and not re.search(r'\b(?:just|recently|latest|now|announced|this week)\b', text):
        return ''
    if re.search(r'\b(?:chief minister|prime minister|president|governor|mayor)\b', text):
        if not re.search(r'\b(?:define|meaning|duties|role of|what is a|what is the role)\b', text):
            return 'officeholder'
    if (re.search(r'\b(?:actor|actress|films?|movies?|cinema|ott|entertainment|tollywood|kollywood|mollywood|bollywood)\b', text)
            and re.search(r'\b(?:now|current|currently|latest|new|upcoming|next|working|projects?|releases?|released|announced|updates?|recent|recently|today|this week)\b', text)):
        return 'entertainment'
    if re.search(r'\b(?:telugu|hindi|tamil|malayalam|tollywood|kollywood|mollywood|bollywood)\b.*\b(?:releases?|releasing)\b', text):
        return 'entertainment'
    return ''


def known_office_profile(query):
    """Choose only an already scoped current-office adapter; never infer an office."""
    if current_topic(query) != 'officeholder':
        return ''
    for url in ('https://www.telangana.gov.in/government/chief-minister/',
                'https://www.pmindia.gov.in/en/pms-profile/'):
        if official_office_page(url, query):
            return url
    return ''


def official_office_page(url, query):
    """Explicit source/path scope; a government news archive is not a roster."""
    p = urlsplit(url)
    host, path = (p.hostname or '').removeprefix('www.'), p.path.rstrip('/').lower()
    if p.scheme != 'https':
        return False
    if re.search(r'\bchief minister\b', query, re.I) and re.search(r'\btelangana\b', query, re.I):
        return host == 'telangana.gov.in' and path in {'/government/chief-minister', '/government/chiefminister', '/chief-minister'}
    if re.search(r'\bprime minister\b', query, re.I) and re.search(r'\bindia\b', query, re.I):
        return host == 'pmindia.gov.in' and path in {'/en/pms-profile', '/en/prime-ministers-office'}
    return False


def report_status(piece):
    """Describe what a source says; an announcement is not independent confirmation."""
    if re.search(r'\b(?:not|never|no)\b.{0,40}\b(?:confirmed|announced|released|filming|scheduled|release)\b|\b(?:denied|cancelled|canceled)\b', piece, re.I):
        return 'unconfirmed_or_denied'
    if re.search(r'\b(?:rumou?rs?|reportedly|unconfirmed|speculation|in talks|expected|may(?!\s+\d)|might|could)\b', piece, re.I):
        return 'rumour_or_unconfirmed'
    if re.search(r'\b(?:was released|has been released|(?:is|are) now streaming|released on|premiered on)\b', piece, re.I):
        return 'reported_released'
    if re.search(r'\b(?:will release|scheduled to release|will premiere|releases on|slated for|set to release|will hit (?:screens|cinemas))\b|\b(?:confirmed|officially announced)\b.{0,60}\b(?:release|premiere)\b|\brelease date\b.{0,30}\b(?:confirmed|officially announced)\b', piece, re.I):
        return 'reported_confirmed_or_scheduled'
    if re.search(r'\b(?:confirmed|announced|will star|stars in|is filming|is shooting|began filming|begun shooting|working on|upcoming|production began)\b', piece, re.I):
        return 'reported_project'
    return 'reported_claim'


def classify_claims(text):
    """Precise general statuses alongside the backward-compatible film labels."""
    from .facts import dated_claims
    labels = set()
    for piece in re.split(r'\n+|(?<=[.!?])\s+', text):
        status = report_status(piece)
        if status not in {'rumour_or_unconfirmed', 'unconfirmed_or_denied'}:
            if re.search(r'\b(?:was released|has been released|was launched|now streaming|premiered on)\b', piece, re.I):
                status = 'reported_released'
            elif re.search(r'\b(?:will release|will launch|scheduled|will premiere|releases on|slated for|set to release|will hit)\b', piece, re.I):
                status = 'reported_scheduled'
                if any(stamp.date() < datetime.now(timezone.utc).date() for _, _, stamp, _, _ in dated_claims(piece)):
                    status = 'outdated_schedule'
            elif re.search(r'\bconfirmed\b', piece, re.I):
                status = 'reported_confirmation'
            elif re.search(r'\b(?:filming|shooting|production began)\b', piece, re.I):
                status = 'reported_production'
            elif re.search(r'\bannounced\b', piece, re.I):
                status = 'reported_announcement'
            elif status == 'reported_project':
                status = 'reported_claim'
        if piece.strip():
            labels.add(status)
    return ','.join(sorted(labels))


def page_evidence(text, url, query):
    """Extract short claim-bearing sentences. Script/navigation text is excluded upstream."""
    from .searxng import query_terms
    from .intent import normalized_words
    topic = current_topic(query)
    if topic == 'officeholder' and official_office_page(url, query):
        # Scoped present-office profiles can express a roster through adjacent
        # name/role blocks, or a current PM biography's opening oath sentence.
        # These rules never apply to government news archives or search snippets.
        if 'chief minister' in query.casefold():
            names = re.findall(r'(?im)^((?:SRI|SHRI|SMT|DR)[ .]+[A-Z][A-Za-z .\'-]{2,70})\s*\n(?:The\s+)?Hon[^\w\s]?ble\s+Chief Minister\s*(?:\n|$)', text)
            names = {' '.join(name.split()) for name in names}
            if len(names) == 1:
                return 'Chief Minister of Telangana: '+next(iter(names))+'.', ''
            if len(names) > 1:
                return '', ''
        elif urlsplit(url).path.rstrip('/').lower() == '/en/pms-profile' and re.search(r'(?im)^Know the PM$', text):
            intro = re.search(r'(?im)^((?:Shri|Sri) [A-Z][A-Za-z .\'-]{2,65}) was sworn[- ]in as India[\W_]?s Prime Minister\b', text)
            if intro:
                return 'Prime Minister of India: '+intro[1]+'. The fetched current official PM profile identifies this officeholder.', ''
    terms = query_terms(query) - {'actor', 'actress', 'movie', 'movies', 'film', 'films', 'working', 'projects', 'project', 'releases', 'upcoming', 'next'}
    named = set().union(*(normalized_words(token) for token in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:['’]s)?\b", query))) & terms
    pieces = re.split(r'\n+|(?<!\b[A-Z]\.)(?<=[.!?])\s+', text)
    selected, statuses = [], set()
    region = next((word for word in ('telugu', 'tamil', 'malayalam', 'hindi') if word in terms), '')
    regional_heading = ''
    for piece in pieces:
        piece = ' '.join(piece.split())
        if re.search(r'^(?:sign in|log in|subscribe|accept cookies|all rights reserved|read more|click here|join our newsletter)\b', piece, re.I):
            continue
        if topic == 'entertainment' and len(piece) <= 100 and re.fullmatch(r'(?:Telugu|Tamil|Malayalam|Hindi) (?:cinema|films?|movies?)(?: releases?)?[:.! ]*', piece, re.I):
            regional_heading = piece
            continue
        if not 20 <= len(piece) <= 700 or (topic == 'officeholder' and re.search(r'\b(?:former|previous)\b', piece, re.I)):
            continue
        words = normalized_words(piece)
        contextual_region = region and region in normalized_words(regional_heading) and not (words & {'telugu', 'tamil', 'malayalam', 'hindi'})
        anchors = terms - {region} if contextual_region else terms
        if not topic and not named <= words:
            continue
        if anchors and (not anchors <= words if topic in {'officeholder', 'entertainment'} else len(anchors & words) < min(2, len(anchors))):
            continue
        if topic == 'officeholder':
            if re.search(r'\b(?:was|served|in (?:19|20)\d{2}|as of (?:19|20)\d{2})\b', piece, re.I):
                continue
            role = 'chief minister' if re.search(r'chief minister', query, re.I) else 'prime minister'
            # Require explicit present-tense relation or an official roster label.
            if official_office_page(url, query) and re.search(
                    r'\b' + role + r'\s*(?:of\s+(?:Telangana|India)\s*)?(?:is\s+|:\s*)[A-Z][A-Za-z .\'-]{2,65}'
                    r'|\b[A-Z][A-Za-z .\'-]{2,65}\s+is\s+(?:the\s+)?(?:current\s+)?' + role + r'\b', piece, re.I):
                if sum(map(len, selected)) + len(piece) + len(selected) <= 1200:
                    selected.append(piece)
        elif topic == 'entertainment':
            status = report_status(piece)
            from .facts import dated_claims
            if status == 'reported_confirmed_or_scheduled' and any(
                    stamp.date() < datetime.now(timezone.utc).date()
                    for _, _, stamp, _, _ in dated_claims(piece)):
                status = 'outdated_schedule'
            if status != 'reported_claim':
                if contextual_region:
                    piece = regional_heading.rstrip(': .!')+': '+piece
                if sum(map(len, selected)) + len(piece) + len(selected) <= 1200:
                    selected.append(piece)
                    statuses.add(status)
        elif not topic and len(piece.split()) >= 6:
            if not terms and not re.search(r'\b(?:announced|reported|released|launched|approved|voted|elected|discovered|published|signed|opened|closed|confirmed|scheduled|said|found|reached|measured|won)\b', piece, re.I):
                continue
            if sum(map(len, selected)) + len(piece) + len(selected) <= 1200:
                selected.append(piece)
                statuses.add(report_status(piece))
        if sum(map(len, selected)) >= 1000:
            break
    if not selected:
        return '', ''
    return ' '.join(selected), ','.join(sorted(statuses))
