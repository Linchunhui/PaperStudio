"""Bounded public PDF downloader with DNS validation and IP-pinned sockets.

Each redirect gets a new validated DNS resolution. Connections use its exact
sockaddr, while TLS SNI/certificate validation and HTTP Host retain the original
hostname. No environment proxies, second hostname resolution, or insecure TLS.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import tempfile
import time
from urllib.parse import quote, urljoin, urlsplit

from . import pdf


class DownloadError(ValueError):
    pass


class DownloadCancelled(Exception):
    pass


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)
    except ValueError:
        return False


def validated_target(url: str) -> tuple:
    if len(url) > 4096:
        raise DownloadError("下载链接过长")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except (ValueError, UnicodeError):
        raise DownloadError("下载链接格式无效") from None
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise DownloadError("下载只接受不含账号密码的 HTTP(S) 公网链接")
    if port not in {80, 443} or "%" in host or "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise DownloadError("下载链接包含不支持的端口或字符")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise DownloadError("不允许下载本机或内网地址")
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise DownloadError("无法解析论文下载域名") from None
    if not addresses or any(not _public_address(row[4][0]) for row in addresses):
        raise DownloadError("下载目标解析到非公网地址，已拒绝请求")
    return parts, host, port, addresses


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, *, tls, timeout):
        super().__init__(host, port, timeout=timeout)
        self._address, self._tls = address, tls

    def connect(self):
        family, socktype, proto, _, sockaddr = self._address
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(sockaddr)  # numeric address already vetted; no re-resolution
            if self._tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


def download_pdf(url: str, directory: Path, *, cancelled=lambda: False, progress=lambda current, total: None,
                 max_bytes: int = pdf.MAX_PDF_BYTES, timeout: float = 90, max_redirects: int = 5) -> dict:
    """Return a temporary file the caller must remove, never trusting its name."""
    directory.mkdir(parents=True, exist_ok=True)
    started, current_url, redirects = time.monotonic(), url, 0
    temporary = None
    try:
        while True:
            if cancelled():
                raise DownloadCancelled()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise DownloadError("论文下载超时，可稍后重试")
            parts, host, port, addresses = validated_target(current_url)
            connection = _PinnedConnection(host, port, addresses[0], tls=parts.scheme == "https", timeout=min(15, remaining))
            try:
                target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
                if parts.query:
                    target += "?" + quote(parts.query, safe="%/?@:!$&'()*+,;=-._~")
                connection.request("GET", target, headers={"Accept": "application/pdf,application/octet-stream;q=0.8", "Accept-Encoding": "identity", "User-Agent": "PaperStudio/0.2 (personal research library)"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirects >= max_redirects:
                        raise DownloadError("下载重定向无效或次数过多")
                    current_url = urljoin(current_url, location)
                    redirects += 1
                    continue
                if response.status != 200:
                    raise DownloadError(f"论文下载返回 HTTP {response.status}；请确认链接提供公开 PDF")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise DownloadError("服务器未提供未压缩文件，无法在大小限制内可靠下载")
                length = response.getheader("Content-Length")
                try:
                    length = int(length) if length else None
                except ValueError:
                    raise DownloadError("下载服务器返回无效文件长度") from None
                if length is not None and (length < 0 or length > max_bytes):
                    raise DownloadError("PDF 超过 50 MB 下载限制")
                fd, name = tempfile.mkstemp(prefix=".paper-download-", suffix=".pdf", dir=directory)
                temporary = Path(name)
                count = 0
                with os.fdopen(fd, "wb") as output:
                    while True:
                        if cancelled():
                            raise DownloadCancelled()
                        remaining = timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise DownloadError("论文下载超时，可稍后重试")
                        if connection.sock:
                            connection.sock.settimeout(min(15, remaining))
                        chunk = response.read(min(65536, max_bytes - count + 1))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > max_bytes:
                            raise DownloadError("PDF 超过 50 MB 下载限制")
                        output.write(chunk)
                        progress(count, length)
                    output.flush()
                    os.fsync(output.fileno())
                if length is not None and count != length:
                    raise DownloadError("下载未完整结束，请重试")
                if not count:
                    raise DownloadError("论文下载文件为空")
                return {"path": temporary, "resolved_url": current_url, "size_bytes": count}
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise DownloadError("无法安全连接论文下载服务，或连接中断；请检查链接后重试") from None
            finally:
                connection.close()
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
