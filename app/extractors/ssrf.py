import ipaddress
import socket
import urllib.parse
from typing import Optional, Set
import httpx
from app.core.logger import log


class SSRFSecurityError(Exception):
    """Вызывается при попытке обращения к локальным, приватным или запрещенным адресам."""
    pass


FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]

ALLOWED_WEB_PORTS: Set[int] = {80, 443, 8080, 8443}


def is_ip_allowed(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
        for net in FORBIDDEN_NETWORKS:
            if ip in net:
                return False
        return True
    except ValueError:
        return False


def validate_url_safe(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFSecurityError(f"Неподдерживаемый протокол: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityError("В URL отсутствует имя хоста")

    # Валидация порта: запрещаем подключение к внутренним сервисам (22, 5432, 6379, 2375 и др.)
    port = parsed.port
    if port is not None and port not in ALLOWED_WEB_PORTS:
        raise SSRFSecurityError(f"Порт {port} заблокирован политикой безопасности.")

    # Проверка литерала IP
    try:
        ip = ipaddress.ip_address(hostname)
        if not is_ip_allowed(str(ip)):
            raise SSRFSecurityError(f"Доступ к приватному IP-адресу запрещен: {hostname}")
        return
    except ValueError:
        pass

    # Резолвинг доменного имени
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFSecurityError(f"Не удалось разрешить имя хоста '{hostname}': {e}")

    for _, _, _, _, sockaddr in addr_info:
        ip_addr = sockaddr[0]
        if not is_ip_allowed(ip_addr):
            raise SSRFSecurityError(f"Хост '{hostname}' разрешается в запрещенный IP '{ip_addr}'")


class SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """
    Транспорт, гарантирующий проверку URL перед каждым запросом и редиректом.
    """
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_url_safe(str(request.url))
        return await super().handle_async_request(request)


def create_safe_async_client(
    timeout: float = 15.0,
    max_redirects: int = 5,
    user_agent: Optional[str] = None
) -> httpx.AsyncClient:
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    transport = SSRFSafeTransport(verify=True)
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        max_redirects=max_redirects,
        headers=headers,
        follow_redirects=True,
    )
