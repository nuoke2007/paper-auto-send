from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RESOURCE_DOMAINS = ("appsj.szxuexiao.com", "njzxxyy.com", "21cnjy.com", "xxsj.org")


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


PAPER_CATEGORIES = (
    "期中考试卷", "期末考试卷", "单元检测", "随堂练习与阶段测评",
    "专项训练", "综合复习", "其他试卷",
)
PAPER_SUBJECTS = ("语文", "数学", "英语", "道德与法治", "科学", "音乐", "美术", "综合／未明确学科")


def paper_subject(title: str) -> str:
    compact = re.sub(r"\s+", "", title)
    subjects = [
        subject for subject in PAPER_SUBJECTS[:-1]
        if subject in compact or (
            subject == "道德与法治" and any(alias in compact for alias in ("道法", "品德与生活", "品德与社会"))
        )
    ]
    return subjects[0] if len(subjects) == 1 else PAPER_SUBJECTS[-1]


def paper_category(title: str) -> str:
    compact = re.sub(r"\s+", "", title)
    if any(word in compact for word in ("专项", "专题", "题型", "易错", "知识点", "考点")):
        return "专项训练"
    if any(word in compact for word in ("复习", "汇总", "归纳")):
        return "综合复习"
    if "期中" in compact:
        return "期中考试卷"
    if "期末" in compact:
        return "期末考试卷"
    if re.search(r"单元|Unit\s*\d|Module\s*\d", compact, re.IGNORECASE):
        return "单元检测"
    if any(word in compact for word in ("随堂", "课堂", "课时", "同步", "每日", "周测", "周练", "月考", "阶段", "作业", "练习")):
        return "随堂练习与阶段测评"
    return "其他试卷"


def build_email_html(
    papers: list[dict[str, str]], *, source_status: dict[str, str] | None = None,
) -> str:
    groups: dict[str, dict[str, list[str]]] = {
        subject: {category: [] for category in PAPER_CATEGORIES}
        for subject in PAPER_SUBJECTS
    }
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
        if paper.get("region_basis"):
            timing += f"；地区依据：{html.escape(paper['region_basis'], quote=True)}"
        if paper.get("eligibility_basis"):
            timing += f"<br>入选依据：{html.escape(paper['eligibility_basis'], quote=True)}"
        source = html.escape(paper.get("source") or urlsplit(safe_link).hostname or "", quote=True)
        groups[paper_subject(paper["title"])][paper_category(paper["title"])].append(
            '<li style="margin:0 0 16px;padding:0 0 12px;border-bottom:1px solid #e5e7eb;">'
            f'<div style="font-size:15px;line-height:1.7;font-weight:600;">{title}</div>'
            f'<div style="font-size:12px;line-height:1.8;color:#64748b;">来源：{source}<br>{timing}</div>'
            f'<a href="{link}" style="color:#1d4ed8;font-size:14px;line-height:2;">查看原站详情与下载入口 →</a></li>'
        )
    subject_counts = {
        subject: sum(len(rows) for rows in categories.values())
        for subject, categories in groups.items()
    }
    summary = " · ".join(f"{subject} {count}份" for subject, count in subject_counts.items() if count)
    sections = []
    for subject, categories in groups.items():
        count = subject_counts[subject]
        if not count:
            continue
        breakdown = " · ".join(
            f"{category} {len(rows)}份" for category, rows in categories.items() if rows
        )
        contents = "".join(
            f'<h3 style="font-size:15px;color:#334155;margin:20px 0 8px;">{category} '
            f'<span style="font-size:12px;font-weight:normal;">{len(rows)}份</span></h3>'
            f'<ol style="padding-left:22px;margin-top:12px;">{"".join(rows)}</ol>'
            for category, rows in categories.items() if rows
        )
        sections.append(
            '<tr><td style="padding:8px 24px;">'
            f'<h2 style="font-size:19px;color:#0f172a;border-left:4px solid #2563eb;padding-left:10px;">'
            f'{subject} <span style="font-size:13px;font-weight:normal;color:#64748b;">{count}份</span></h2>'
            f'<p style="font-size:12px;color:#64748b;line-height:1.8;">{breakdown}</p>'
            f'{contents}</td></tr>'
        )
    status_panel = ""
    if source_status:
        statuses = "".join(
            f"<li>{html.escape(source, quote=True)}：{html.escape(status, quote=True)}</li>"
            for source, status in source_status.items()
        )
        status_panel = (
            '<tr><td style="padding:16px 24px;background:#fff7ed;">'
            '<h2 style="font-size:16px;margin:0;">今日来源扫描状态</h2>'
            f'<ul style="padding-left:20px;font-size:13px;line-height:1.9;">{statuses}</ul>'
            '<p style="font-size:12px;">本封为今日重新抓取汇总，可能包含已推送资源，不修改正式发送历史；'
            '抓取失败不代表该站没有资源。</p></td></tr>'
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;padding:16px 8px;background:#f1f5f9;color:#1e293b;font-family:Arial,sans-serif;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:760px;margin:0 auto;background:#ffffff;">'
        '<tr><td style="padding:24px;background:#1e3a8a;color:#ffffff;">'
        f'<h1 style="font-size:22px;margin:0 0 10px;">{"今日四站试卷汇总" if source_status else "每日新增试卷"}</h1>'
        f'<p style="margin:0;font-size:14px;line-height:1.8;">{"本次汇总" if source_status else "本次新增"} {len(papers)} 份 · 当前年级学期 · 近3年标题年份</p>'
        '</td></tr><tr><td style="padding:18px 24px;background:#eff6ff;">'
        f'<p style="margin:0;font-size:13px;line-height:1.8;">{summary or "暂无新增试卷"}</p>'
        '</td></tr>'
        f'{status_panel}'
        f'{"".join(sections)}'
        '<tr><td style="padding:20px 24px;font-size:12px;line-height:1.8;color:#64748b;background:#f8fafc;">'
        '按学科、试卷类型两级分类；多学科或无法明确学科的归入“综合／未明确学科”。'
        '分类根据资源标题自动判断，未核验试卷正文；专项训练、复习资料优先归入对应类别，'
        '不因标题含“期末”等字样就当作整套考试卷。<br>'
        '标题年份不是网页发布日期；点击链接前往原站，附件是否可下载以原站为准。'
        '</td></tr></table></body></html>'
    )
