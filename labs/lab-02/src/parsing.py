import hashlib
import html as htmllib
import re

from urls import normalize_url


META_CHARSET = re.compile(rb'''<meta[^>]+charset=["']?\s*([\w-]+)''', re.I)


def decode(content, content_type):
    if content_type:
        m = re.search(r'charset=([\w-]+)', content_type, re.I)
        if m:
            try:
                return content.decode(m.group(1), errors='replace'), m.group(1).lower()
            except LookupError:
                pass

    m = META_CHARSET.search(content[:4096])
    if m:
        name = m.group(1).decode('ascii', errors='ignore')
        try:
            return content.decode(name, errors='replace'), name.lower()
        except LookupError:
            pass

    return content.decode('utf-8', errors='replace'), 'utf-8'


LINK = re.compile(r'''<a\s[^>]*href\s*=\s*["']([^"'>]+)["']''', re.I)
TITLE = re.compile(r'(?is)<title[^>]*>(.*?)</title>')
DROP_TAGS = re.compile(r'(?is)<(script|style|table|noscript)\b.*?</\1>')
TAG = re.compile(r'(?s)<[^>]+>')


def extract_links(html, base_url):
    out = []
    seen = set()
    for href in LINK.findall(html):
        url = normalize_url(htmllib.unescape(href), base_url)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_title(html):
    m = TITLE.search(html)
    return re.sub(r'\s+', ' ', htmllib.unescape(TAG.sub('', m.group(1)))).strip() if m else ''


DIV_TAG = re.compile(r'(?i)<(/?)div\b[^>]*>')


def container_body(html, selector):
    attr, _, value = selector.rpartition('=') if '=' in selector else ('class', '', selector)
    opening = re.search(
        r'(?is)<div\b[^>]*\b' + re.escape(attr) + r'\s*=\s*["\']' + re.escape(value)
        + r'["\'][^>]*>', html)
    if not opening:
        return None
    depth = 1
    for tag in DIV_TAG.finditer(html, opening.end()):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[opening.end():tag.start()]
    return html[opening.end():]


def extract_text(html, source):
    containers = source.get('container') or []
    if isinstance(containers, str):
        containers = [containers]
    body = html
    for selector in containers:
        found = container_body(html, selector)
        if found is not None:
            body = found
            break

    for marker in source.get('cut_after', []):
        pos = body.find(marker)
        if pos > 0:
            body = body[:pos]

    body = DROP_TAGS.sub(' ', body)
    body = TAG.sub(' ', body)
    body = htmllib.unescape(body)
    return re.sub(r'\s+', ' ', body).strip()


def content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
