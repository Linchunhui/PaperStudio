"""Normalize supported research URLs without fetching untrusted landing pages."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from fastapi import HTTPException

_ARXIV = re.compile(r"(?P<id>(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z0-9.-]*/\d{7}))(?P<version>v[1-9]\d*)?$")
_DOI = re.compile(r"(?:doi:\s*)?10\.\d{4,9}/\S+", re.I)


def resolve_source(value: str) -> dict:
    value = value.strip()
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(422, "请输入有效的论文链接（最长 4096 字符）")
    if _DOI.fullmatch(value):
        raise HTTPException(422, "DOI 不是 PDF 下载地址；本版请提供对应的公开 PDF 或 arXiv 链接。")
    if value.lower().startswith("arxiv:"):
        value = "https://arxiv.org/abs/" + value.split(":", 1)[1].strip()
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError):
        raise HTTPException(422, "论文链接格式无效") from None
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise HTTPException(422, "只支持不含账号密码的 HTTP(S) 公网链接")
    if host in {"doi.org", "dx.doi.org", "www.doi.org"}:
        raise HTTPException(422, "DOI 全文解析尚未接入；请提供公开 PDF 或 arXiv 链接，不能把出版社网页作为 PDF 导入。")
    if port not in {None, 80, 443}:
        raise HTTPException(422, "论文下载仅支持公网 HTTP/HTTPS 标准端口")
    if host in {"huggingface.co", "www.huggingface.co"} and parts.path.startswith('/papers/'):
        return resolve_source('https://arxiv.org/abs/' + parts.path.removeprefix('/papers/').strip('/'))
    if host in {"openreview.net", "www.openreview.net"} and parts.path in {'/forum', '/pdf'}:
        identifier = parse_qs(parts.query).get('id', [''])[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,100}', identifier):
            raise HTTPException(422, 'OpenReview 链接缺少有效论文 ID')
        return {'kind': 'openreview', 'canonical_url': f'https://openreview.net/forum?id={identifier}',
                'download_url': f'https://openreview.net/pdf?id={identifier}',
                'arxiv_id': None, 'version': None, 'version_policy': 'download-sha-snapshot'}
    if host == 'aclanthology.org':
        identifier = parts.path.strip('/').removesuffix('.pdf')
        if re.fullmatch(r'\d{4}\.[A-Za-z0-9-]+\.[1-9]\d*', identifier):
            return {'kind': 'acl', 'canonical_url': f'https://aclanthology.org/{identifier}/',
                    'download_url': f'https://aclanthology.org/{identifier}.pdf',
                    'arxiv_id': None, 'version': None, 'version_policy': 'download-sha-snapshot'}
        raise HTTPException(422, '请提供 ACL 单篇论文链接，会议目录不能作为论文导入')
    if host in {'openaccess.thecvf.com', 'www.openaccess.thecvf.com'} and '/html/' in parts.path and parts.path.endswith('_paper.html'):
        pdf_path = parts.path.replace('/html/', '/papers/').removesuffix('.html') + '.pdf'
        return {'kind': 'cvf', 'canonical_url': f'https://openaccess.thecvf.com{parts.path}',
                'download_url': f'https://openaccess.thecvf.com{pdf_path}',
                'arxiv_id': None, 'version': None, 'version_policy': 'download-sha-snapshot'}
    if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        path = unquote(parts.path)
        if not path.startswith(("/abs/", "/pdf/")):
            raise HTTPException(422, "请提供 arXiv 的 /abs/ 或 /pdf/ 论文链接")
        identifier = path[5:].removesuffix(".pdf").rstrip("/")
        match = _ARXIV.fullmatch(identifier)
        if not match:
            raise HTTPException(422, "arXiv 论文编号或版本格式无效")
        identifier = match.group("id") + (match.group("version") or "")
        return {"kind": "arxiv", "canonical_url": f"https://arxiv.org/abs/{identifier}",
                "download_url": f"https://arxiv.org/pdf/{identifier}", "arxiv_id": match.group("id"),
                "version": int(match.group("version")[1:]) if match.group("version") else None,
                "version_policy": "explicit" if match.group("version") else "download-sha-snapshot"}
    # A PDF endpoint need not have a .pdf suffix. Actual PDF validation happens
    # after downloading. Queries may be necessary for signed public file URLs.
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    normalized = urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
    return {"kind": "pdf-url", "canonical_url": normalized, "download_url": normalized,
            "arxiv_id": None, "version": None, "version_policy": "download-sha-snapshot"}

