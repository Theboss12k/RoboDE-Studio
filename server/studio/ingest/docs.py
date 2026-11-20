"""Documentation intake: turn an uploaded manual into plain text the LLM can read and quotes can be checked against."""
import html
import io
import re

MAX_DOC_BYTES = 40 * 1024 * 1024
MAX_TEXT = 400_000
TEXT_TYPES = ('.md', '.txt', '.rst', '.json', '.yaml', '.yml', '.py', '.h', '.hpp', '.c', '.cpp', '.csv', '.xml')


def extract_text(payload, filename):
    if len(payload) > MAX_DOC_BYTES:
        raise ValueError('Documents must be at most 40 MB')
    lower = filename.lower()
    if lower.endswith('.pdf'):
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError('PDF reading needs the pypdf package') from error
        try:
            reader = PdfReader(io.BytesIO(payload))
            text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
        except Exception as error:
            raise ValueError('Could not read this PDF') from error
    elif lower.endswith(('.html', '.htm')):
        text = re.sub(r'<(script|style)\b.*?</\1>', ' ', payload.decode('utf-8', 'replace'), flags=re.S | re.I)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    elif lower.endswith(TEXT_TYPES):
        text = payload.decode('utf-8-sig', 'replace')
    else:
        raise ValueError('Upload documentation as PDF, Markdown, text, HTML, JSON, YAML or source headers')
    text = re.sub(r'[ \t]+\n', '\n', text).strip()
    if not text:
        raise ValueError('No text could be extracted; scanned PDFs need OCR first')
    return text[:MAX_TEXT]


def normalized(text):
    return ' '.join(text.casefold().replace('`', '').split())


def contains_quote(documentation, quote):
    return bool(quote) and normalized(quote) in normalized(documentation)


def search(documentation, query, limit=5, window=500):
    """Paragraphs that best match the query words, for the twin agent's search tool."""
    words = [w for w in re.findall(r'[a-z0-9_]+', query.lower()) if len(w) > 2]
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', documentation) if p.strip()]
    scored = sorted(((sum(p.lower().count(w) for w in words), i, p) for i, p in enumerate(paragraphs)), reverse=True)
    return [p[:window] for score, _, p in scored[:limit] if score > 0]
