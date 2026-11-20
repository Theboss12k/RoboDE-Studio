"""The user's preferred vendors (a global setting kept by the Java server): normalise the list, match a URL against it and
build the prompt sentence for the search agents. Pure functions; no store or network access."""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

CATEGORIES = ('robots', 'motors', 'electronics')
MAX_VENDORS = 50
_LABEL = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


@dataclass(frozen=True)
class Vendor:
    name: str
    domain: str                    # bare lower-case host, e.g. robu.in
    categories: tuple[str, ...]    # subset of CATEGORIES, in that order
    note: str = ''

    def as_dict(self):
        return {'name': self.name, 'domain': self.domain, 'categories': list(self.categories), 'note': self.note}


def host_of(value):
    """The lower-case host of a URL or a bare domain (no scheme, port, path, credentials or leading 'www.'); '' if none."""
    text = str(value or '').strip().lower()
    if not text:
        return ''
    if '//' not in text:
        text = '//' + text
    try:
        host = urlsplit(text).hostname or ''
    except ValueError:
        return ''
    host = host.rstrip('.')
    return host[4:] if host.startswith('www.') else host


def valid_domain(host):
    labels = host.split('.')
    return len(host) <= 253 and len(labels) >= 2 and all(_LABEL.match(x) for x in labels) and not labels[-1].isdigit()


def normalize(raw):
    """Vendors from the stored or posted list. Entries that are not objects or lack a valid domain or any valid category
    are skipped; the same domain is merged (categories united, first name and note kept); at most 50 are returned."""
    out = {}
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        domain = host_of(item.get('domain'))
        cats = item.get('categories')
        cats = {c for c in cats if c in CATEGORIES} if isinstance(cats, (list, tuple)) else set()
        if not domain or not valid_domain(domain) or not cats:
            continue
        name = str(item.get('name') or '').strip()[:120] or domain
        note = str(item.get('note') or '').strip()[:300]
        if domain in out:
            old = out[domain]
            cats |= set(old.categories)
            out[domain] = Vendor(old.name, domain, tuple(c for c in CATEGORIES if c in cats), old.note or note)
        else:
            out[domain] = Vendor(name, domain, tuple(c for c in CATEGORIES if c in cats), note)
    return list(out.values())[:MAX_VENDORS]


def for_category(vendors, category):
    return [v for v in normalize([v.as_dict() if isinstance(v, Vendor) else v for v in vendors or []]) if category in v.categories]


def matches(url, vendors, category):
    """The preferred vendor of `category` whose domain equals the URL's host or is a parent domain of it, else None."""
    host = host_of(url)
    if not host:
        return None
    for v in for_category(vendors, category):
        if host == v.domain or host.endswith('.' + v.domain):
            return v
    return None


def prompt_block(vendors, category):
    """One paragraph telling a search agent which shops to look at first; '' when there are none for the category."""
    mine = for_category(vendors, category)
    if not mine:
        return ''
    items = '; '.join(f'{v.name} ({v.domain})' + (f': {v.note}' if v.note else '') for v in mine)
    return ('Search these preferred vendors first: ' + items + '. Look for matching products on these sites before any '
            'other shop and keep the same evidence rules (real products, verbatim quotes, no invented figures). Use other '
            'sources only to fill gaps, or when no preferred vendor sells a suitable product.')
