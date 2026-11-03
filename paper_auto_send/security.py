from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RESOURCE_DOMAINS = ("appsj.szxuexiao.com", "njzxxyy.com", "21cnjy.com")


def _host_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def safe_resource_url(value: str, allowed_domains: tuple[str, ...]) -> str | None:
    try:
        if any(ord(character) < 32 for character in value):
            return None
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        if not host or parsed.username or parsed.password or not _host_allowed(host, allowed_domains):
            return None
        if parsed.port not in (None, 443) and not (
            parsed.scheme.lower() == "http" and parsed.port == 80
        ):
            return None
        if parsed.scheme.lower() not in ("https", "http"):
            return None
        if parsed.scheme.lower() == "http":
            authority = host.lower() if parsed.port == 80 else parsed.netloc.lower()
            parsed = parsed._replace(scheme="https", netloc=authority)
        tracking_keys = {"kw", "qid", "rank", "fs", "sts", "search_entry", "skip_catch"}
        is_resource = bool(re.search(r"/(?:H/\d+/\d+/\d+\.shtml|soft/\d+\.html)$", parsed.path))
        query = [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and not (is_resource and key.lower() in tracking_keys)
        ]
        return urlunsplit(
            (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", urlencode(query), "")
        )
    except (ValueError, UnicodeError):
        return None


def build_email_html(papers: list[dict[str, str]]) -> str:
    rows = []
    for paper in papers:
        title = html.escape(paper["title"], quote=True)
        safe_link = safe_resource_url(paper["link"], RESOURCE_DOMAINS)
        if safe_link is None:
            raise ValueError("email resource link is not an allowed HTTPS URL")
        link = html.escape(safe_link, quote=True)
        if paper.get("date_basis") == "title_year":
            timing = f"标题年份：{html.escape(paper['title_year'], quote=True)}（非发布日期；跨年学年取起始年）"
        else:
            timing = f"发布日期：{html.escape(paper.get('published', '未提供'), quote=True)}"
        rows.append(f'<li>{title} — {timing} — <a href="{link}">预览&下载</a></li>')
    return (
        "<!doctype html><html><body>"
        "<h3>今日新增试卷（苏州园区适配 · 近3年内）</h3>"
        "<p>来源：小学试卷网 / 牛津中小学英语网 / 二一教育（仅明确免费资源）</p>"
        f"<ul>{''.join(rows)}</ul></body></html>"
    )
