import re
import urllib.parse

RESERVED_ESCAPE = re.compile(r'(%25|%2F|%3F|%23)', re.I)

TRACKING_PARAMS = re.compile(r'^(utm_|openstat|gclid|yclid|fbclid|from$|ref$)')


def normalize_url(url, base=None):
    if base:
        url = urllib.parse.urljoin(base, url)

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        return None

    netloc = parts.netloc.lower()
    if netloc.endswith(':80') and parts.scheme == 'http':
        netloc = netloc[:-3]
    if netloc.endswith(':443') and parts.scheme == 'https':
        netloc = netloc[:-4]

    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not TRACKING_PARAMS.match(k)]
    query.sort()

    path = ''.join(piece.upper() if RESERVED_ESCAPE.fullmatch(piece)
                   else urllib.parse.unquote(piece)
                   for piece in RESERVED_ESCAPE.split(parts.path))
    path = path or '/'

    return urllib.parse.urlunsplit((
        parts.scheme.lower(), netloc, path,
        urllib.parse.urlencode(query), ''))


def host(url):
    return urllib.parse.urlsplit(url).netloc
