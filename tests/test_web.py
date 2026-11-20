"""Real network requests; run with STUDIO_WEB=1. Not part of the default suite."""
import pytest

from studio.catalog.fetch import UnsafeURL, safe_download

pytestmark = pytest.mark.web


async def test_pinned_https_download_of_a_public_page():
    got = await safe_download('https://www.iana.org/help/example-domains')
    assert got.content_type == 'text/html'
    assert got.final_url.startswith('https://www.iana.org/')
    assert 'example' in got.bytes.decode('utf-8', 'replace').lower()


@pytest.mark.parametrize('url', ['https://localhost/', 'http://example.com/'])
async def test_unsafe_urls_are_refused(url):
    with pytest.raises(UnsafeURL):
        await safe_download(url)
