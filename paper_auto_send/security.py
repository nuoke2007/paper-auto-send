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
    new_links: set[str] | None = None,
) -> str:
    groups: dict[str, dict[str, list[str]]] = {
        subject: {category: [] for category in PAPER_CATEGORIES}
        for subject in PAPER_SUBJECTS
    }
    ordered_papers = sorted(papers, key=lambda paper: paper["link"] not in new_links) if new_links is not None else papers
    for paper in ordered_papers:
        title = html.escape(paper["title"], quote=True)
        if new_links is not None:
            title = ("<strong style=\"color:#b45309;\">【今日新增】</strong>" if paper["link"] in new_links else "【历史保留】") + title
        safe_link = safe_resource_url(paper["link"], RESOURCE_DOMAINS)
        if safe_link is None:
            raise ValueError("email resource link is not an allowed HTTPS URL")
        link = html.escape(safe_link, quote=True)
        if paper.get("date_basis") == "title_year":
            timing = f"标题年份：{html.escape(paper['title_year'], quote=True)}（非发布日期）"
        else:
            timing = f"发布日期：{html.escape(paper.get('published', '未提供'), quote=True)}"
        if paper.get("region_basis"):
            timing += f"；地区依据：{html.escape(paper['region_basis'], quote=True)}"
        if paper.get("eligibility_basis"):
            timing += f" · 入选依据：{html.escape(paper['eligibility_basis'], quote=True)}"
        source = html.escape(paper.get("source") or urlsplit(safe_link).hostname or "", quote=True)
        groups[paper_subject(paper["title"])][paper_category(paper["title"])].append(
            f'<a href="{link}" style="color:#1d4ed8;font-size:14px;line-height:1.55;'
            f'font-weight:600;text-decoration:none;overflow-wrap:anywhere;">{title}</a>'
            f'<div style="font-size:11px;line-height:1.5;color:#64748b;margin-top:4px;">'
            f'来源：{source} · {timing}</div>'
        )
    subject_counts = {
        subject: sum(len(rows) for rows in categories.values())
        for subject, categories in groups.items()
    }
    summary = " · ".join(f"{subject} {count}份" for subject, count in subject_counts.items() if count)
    sections = []
    navigation = []
    for subject_index, (subject, categories) in enumerate(groups.items()):
        count = subject_counts[subject]
        if not count:
            continue
        section_id = f"subject-{subject_index}"
        navigation.append(
            f'<a href="#{section_id}" style="display:inline-block;padding:6px 12px;'
            f'margin:3px;background:#ffffff;border:1px solid #cbd5e1;border-radius:6px;'
            f'color:#1e40af;font-size:13px;text-decoration:none;">{subject} {count}</a>'
        )
        breakdown = " · ".join(
            f"{category} {len(rows)}份" for category, rows in categories.items() if rows
        )
        category_sections = []
        for category, rows in categories.items():
            if not rows:
                continue
            pairs = []
            for offset in range(0, len(rows), 2):
                cells = "".join(
                    '<td class="paper-cell" width="50%" valign="top" '
                    'style="width:50%;padding:9px 10px;border-bottom:1px solid #e2e8f0;'
                    f'overflow-wrap:anywhere;">{row}</td>'
                    for row in rows[offset:offset + 2]
                )
                if offset + 1 == len(rows):
                    cells += '<td class="empty-cell" width="50%" style="width:50%;"></td>'
                pairs.append(f"<tr>{cells}</tr>")
            category_sections.append(
                f'<h3 style="font-size:14px;color:#334155;margin:14px 0 4px;">{category} '
                f'<span style="font-size:12px;font-weight:normal;">{len(rows)}份</span></h3>'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                f'style="table-layout:fixed;">{"".join(pairs)}</table>'
            )
        contents = "".join(category_sections)
        sections.append(
            '<tr><td class="section" style="padding:8px 20px;">'
            f'<h2 id="{section_id}" style="font-size:18px;color:#0f172a;border-left:4px solid #2563eb;padding-left:10px;">'
            f'{subject} <span style="font-size:13px;font-weight:normal;color:#64748b;">{count}份</span></h2>'
            f'<p style="font-size:11px;color:#64748b;line-height:1.5;margin:4px 0;">{breakdown}</p>'
            f'{contents}<p style="text-align:right;margin:8px 0;">'
            '<a href="#digest-top" style="font-size:11px;color:#64748b;">返回目录 ↑</a></p></td></tr>'
        )
    status_panel = ""
    updates_panel = ""
    if new_links is not None:
        additions = [paper for paper in papers if paper["link"] in new_links]
        addition_summary = " · ".join(
            f"{subject} {sum(paper_subject(paper['title']) == subject for paper in additions)}份"
            for subject in PAPER_SUBJECTS
            if any(paper_subject(paper["title"]) == subject for paper in additions)
        )
        updates_panel = (
            '<tr><td style="padding:18px 24px;background:#f0fdf4;">'
            f'<h2 style="font-size:17px;">今日新增 {len(additions)} 份</h2>'
            + (f'<p style="font-size:13px;">{addition_summary}。新增条目在下方各分类内优先显示，以橙色【今日新增】标记。</p>' if additions else
               '<p>本次未发现尚未推送的合格资源，下方保留历史合格清单；如有来源失败，不能据此认定该站无新增。</p>')
            + '</td></tr>'
        )
    if source_status:
        statuses = "".join(
            f"<li>{html.escape(source, quote=True)}：{html.escape(status, quote=True)}</li>"
            for source, status in source_status.items()
        )
        status_panel = (
            '<tr><td style="padding:10px 20px;background:#fff7ed;">'
            '<h2 style="font-size:16px;margin:0;">今日来源扫描状态</h2>'
            f'<ul style="padding-left:20px;font-size:11px;line-height:1.5;margin:6px 0;">{statuses}</ul>'
            f'<p style="font-size:12px;">{"历史资源按当前规则重新核验后保留；今天未抓到不代表失效。" if new_links is not None else "本封为今日重新抓取汇总，可能包含已推送资源，不修改正式发送历史；"}'
            '抓取失败不代表该站没有资源。</p></td></tr>'
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<style>@media only screen and (max-width:600px){'
        '.paper-cell{display:block!important;width:auto!important;}'
        '.empty-cell{display:none!important;}.section{padding:8px 12px!important;}}</style></head>'
        '<body style="margin:0;padding:16px 8px;background:#f1f5f9;color:#1e293b;font-family:Arial,sans-serif;">'
        '<table id="digest-top" role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:960px;margin:0 auto;background:#ffffff;">'
        '<tr><td style="padding:20px;background:#1e3a8a;color:#ffffff;">'
        f'<h1 style="font-size:22px;margin:0 0 10px;">{"每日试卷更新与完整清单" if new_links is not None else "今日四站试卷汇总" if source_status else "每日新增试卷"}</h1>'
        f'<p style="margin:0;font-size:14px;line-height:1.8;">{"合格资源共" if new_links is not None else "本次汇总" if source_status else "本次新增"} {len(papers)} 份 · 当前年级学期 · 近3年标题年份</p>'
        '</td></tr><tr><td style="padding:18px 24px;background:#eff6ff;">'
        f'<p style="margin:0;font-size:13px;line-height:1.8;">{summary or ("暂无符合当前规则的资源" if new_links is not None else "暂无新增试卷")}</p>'
        f'<div style="margin-top:8px;">{"".join(navigation)}</div>'
        '</td></tr>'
        f'{updates_panel}'
        f'{status_panel}'
        f'{"".join(sections)}'
        '<tr><td style="padding:20px 24px;font-size:12px;line-height:1.8;color:#64748b;background:#f8fafc;">'
        '按学科、试卷类型两级分类；多学科或无法明确学科的归入“综合／未明确学科”。'
        '分类根据资源标题自动判断，未核验试卷正文；专项训练、复习资料优先归入对应类别，'
        '不因标题含“期末”等字样就当作整套考试卷。<br>'
        '标题年份不是网页发布日期；跨年学年取起始年。点击完整标题前往原站，附件是否可下载以原站为准。'
        '电脑双栏、手机单栏；若邮箱不支持目录跳转或响应式样式，仍可顺序阅读全部内容。'
        '</td></tr></table></body></html>'
    )
