"""Download a datasheet from a URL the model (or user) supplied, without letting it reach internal services.

Only https; the host is resolved and every address must be public; redirects are followed by hand and re-checked;
the body is size-capped while streaming; only PDF, HTML and text are accepted.
Each request is pinned to the address that was validated (URL host = that IP, Host header and TLS SNI = the real
hostname), so a second DNS answer cannot redirect the connection to an internal address (DNS rebinding).
"""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from ..util import safe_name

TYPES = {'application/pdf': '.pdf', 'text/html': '.html', 'application/xhtml+xml': '.html', 'text/plain': '.txt',
         'text/markdown': '.md', 'text/x-markdown': '.md'}
MAX_REDIRECTS = 5


class UnsafeURL(ValueError):
    pass


@dataclass
class Downloaded:
    bytes: bytes
    content_type: str
    final_url: str
    filename: str


def is_public(address):
    ip = ipaddress.ip_address(address.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified) and ip.is_global


async def system_resolver(host, port):
    infos = await asyncio.get_running_loop().run_in_executor(None, socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_url(url, resolver):
    parts = urlsplit(url)
    if parts.scheme != 'https':
        raise UnsafeURL('Only https:// URLs are allowed')
    host = parts.hostname
    if not host or parts.username or parts.password:
        raise UnsafeURL('The URL has no host or contains credentials')
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list(await resolver(host, parts.port or 443))
        except OSError as error:
            raise UnsafeURL(f'Could not resolve {host}') from error
    if not addresses:
        raise UnsafeURL(f'Could not resolve {host}')
    for address in addresses:
        if not is_public(address):
            raise UnsafeURL(f'{host} resolves to a non-public address')
    return addresses[0]


def pinned(url, address):
    """(URL that connects to `address`, headers, extensions) for a request that must keep the real hostname."""
    parts = urlsplit(url)
    literal = parts.hostname == address
    ip = f'[{address}]' if ':' in address else address
    netloc = ip + (f':{parts.port}' if parts.port else '')
    target = parts._replace(netloc=netloc).geturl()
    if literal:
        return target, {}, {}
    return target, {'Host': parts.netloc}, {'sni_hostname': parts.hostname}


def file_name(url, disposition, content_type):
    name = None
    match = re.search(r'filename\*=(?:UTF-8\'\')?([^;]+)|filename="?([^";]+)"?', disposition or '', re.I)
    candidates = [unquote((match.group(1) or match.group(2)).strip())] if match else []
    candidates.append(unquote(urlsplit(url).path.rsplit('/', 1)[-1]))
    for candidate in candidates:
        try:
            name = safe_name(candidate)
            break
        except ValueError:
            continue
    extension = TYPES[content_type]
    if not name:
        return 'datasheet' + extension
    if not name.lower().endswith(('.pdf', '.html', '.htm', '.txt', '.md')):
        name += extension
    return name


async def safe_download(url, max_bytes=25_000_000, timeout_s=30, *, resolver=None, transport=None):
    resolver = resolver or system_resolver
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s, follow_redirects=False,
                                 headers={'User-Agent': 'RoboDE-Studio/1.0'}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            address = await check_url(url, resolver)
            target, headers, extensions = pinned(url, address)
            async with client.stream('GET', target, headers=headers, extensions=extensions) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise UnsafeURL('Redirect without a location')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ValueError(f'The server answered {response.status_code}')
                content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if content_type not in TYPES:
                    raise ValueError(f'Unsupported content type {content_type or "unknown"}; only PDF, HTML and text')
                declared = response.headers.get('content-length', '')
                if declared.isdigit() and int(declared) > max_bytes:
                    raise ValueError('The document is larger than the size limit')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise ValueError('The document is larger than the size limit')
                name = file_name(url, response.headers.get('content-disposition'), content_type)
                return Downloaded(bytes(body), content_type, url, name)
    raise UnsafeURL('Too many redirects')
