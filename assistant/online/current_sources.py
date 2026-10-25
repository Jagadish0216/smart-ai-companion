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
    if (re.search(r'\b(?:actor|actress|films?|movies?|cinema|ott|entertainment)\b', text)
            and re.search(r'\b(?:now|current|currently|latest|new|upcoming|next|working|projects?|releases?|released|announced|updates?|recent|recently|today|this week)\b', text)):
        return 'entertainment'
    if re.search(r'\b(?:telugu|hindi|tamil|malayalam)\b.*\breleases?\b', text):
        return 'entertainment'
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


def page_evidence(text, url, query):
    """Extract short claim-bearing sentences. Script/navigation text is excluded upstream."""
    from .searxng import query_terms
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
    pieces = re.split(r'\n+|(?<!\b[A-Z]\.)(?<=[.!?])\s+', text)
    selected, statuses = [], set()
    for piece in pieces:
        piece = ' '.join(piece.split())
        if not 20 <= len(piece) <= 700 or re.search(r'\b(?:former|previous|rumours? denied|not confirmed)\b', piece, re.I):
            continue
        words = set(re.findall(r'[a-z0-9]+', piece.lower()))
        if terms and not terms <= words:
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
            status = ''
            if re.search(r'\b(?:rumou?rs?|reportedly|unconfirmed|speculation|in talks)\b', piece, re.I):
                status = 'rumour_or_unconfirmed'
            elif re.search(r'\b(?:not|never|no)\b.{0,40}\b(?:confirmed|announced|released|filming|scheduled)\b', piece, re.I):
                status = 'unconfirmed_or_denied'
            elif re.search(r'\b(?:was released|has been released|(?:is|are) now streaming|released on)\b', piece, re.I):
                status = 'reported_released'
            elif re.search(r'\b(?:will release|scheduled to release|will premiere)\b|\b(?:confirmed|officially announced)\b.{0,60}\b(?:release|premiere)\b|\brelease date\b.{0,30}\b(?:confirmed|officially announced)\b', piece, re.I):
                status = 'reported_confirmed_or_scheduled'
            elif re.search(r'\b(?:confirmed|announced|will star|is filming|is shooting|working on|upcoming)\b', piece, re.I):
                status = 'reported_project'
            if status:
                if sum(map(len, selected)) + len(piece) + len(selected) <= 1200:
                    selected.append(piece)
                    statuses.add(status)
        elif not topic and len(piece.split()) >= 6:
            if sum(map(len, selected)) + len(piece) + len(selected) <= 1200:
                selected.append(piece)
        if sum(map(len, selected)) >= 1000:
            break
    if not selected:
        return '', ''
    return ' '.join(selected), ','.join(sorted(statuses))
