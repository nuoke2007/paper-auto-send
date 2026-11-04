from __future__ import annotations

import logging
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import truststore

truststore.inject_into_ssl()

import requests
from bs4 import BeautifulSoup, Tag

from .dates import is_recent_title_year, title_year
from .academic import matches_current_term, semester_for_date
from .regions import is_jiangsu_title
from .security import safe_resource_url
from .eligibility import eligibility_basis

LOGGER = logging.getLogger(__name__)
MIN_REQUEST_INTERVAL_SECONDS = 2.0
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_RESULT_PAGES_PER_SOURCE = 24
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "PaperAutoSend/1.0 (daily low-frequency public resource indexer)"
SEARCH_FIELD_HINTS = ("q", "query", "keyword", "key", "search", "word", "title", "wd")
PAID_MARKERS = ("付费", "收费", "学币", "积分下载", "精品", "会员", "登录后下载", "需登录")
NOT_FULLY_FREE_MARKERS = ("免费预览", "部分免费", "部分内容免费")
FREE_MARKERS = ("免费资源", "免费下载", "免费试卷")
ARTICLE_CLASSES = re.compile(r"(list|item|result|resource|thread|card|entry|post|paper|news)", re.I)
MAX_BROWSER_PAGES_PER_QUERY = 3
EMPTY_RESULT_MARKERS = ("暂无相关", "暂无数据", "暂无搜索结果", "没有相关", "没有找到匹配结果", "没有搜索结果", "未找到相关", "未搜索到", "未查询到", "无搜索结果", "共0条")


@dataclass(frozen=True)
class Source:
    name: str
    domains: tuple[str, ...]
    entry_urls: tuple[str, ...]
    keywords: tuple[str, ...]
    only_free: bool = False
    exclude_terms: tuple[str, ...] = ()
    browser_search_url: str | None = None
    jiangsu_alternative: bool = False


@dataclass
class ScanResult:
    papers: list[dict[str, str]]
    failures: list[str]
    successful_sources: int
    source_counts: dict[str, int] = field(default_factory=dict)
    expected_sources: int = 3


@dataclass(frozen=True)
class SearchRequest:
    method: str
    url: str
    params: tuple[tuple[str, str], ...]


def source_definitions(keywords: tuple[str, ...]) -> tuple[Source, ...]:
    english_grade = re.search(r"[一二三四五六七八九十]+年级", keywords[2])
    english_aliases = (keywords[2],)
    if english_grade and english_grade.group() in ("一年级", "二年级"):
        english_aliases += (f"译林版{english_grade.group()}英语",)
    grade_pages = {
        "一年级": "yinianji", "二年级": "ernianji", "三年级": "sannianji",
        "四年级": "sinianji", "五年级": "wunianji", "六年级": "liunianji",
    }
    primary_entries = [
        "https://appsj.szxuexiao.com/",
        "https://appsj.szxuexiao.com/sujiaoban/index.html",
    ]
    if english_grade and english_grade.group() in grade_pages:
        primary_entries.append(
            f"https://appsj.szxuexiao.com/{grade_pages[english_grade.group()]}/index.html"
        )
    grade_slug = grade_pages.get(english_grade.group() if english_grade else "")
    return (
        Source(
            "小学试卷网",
            ("appsj.szxuexiao.com",),
            tuple(primary_entries),
            (keywords[0], keywords[1]),
        ),
        Source(
            "牛津中小学英语网",
            ("njzxxyy.com",),
            (
                "https://www.njzxxyy.com/",
                "https://www.njzxxyy.com/forum-615-1.html",
            ),
            english_aliases,
            exclude_terms=("三起点", "3起点", "三年级起点"),
        ),
        Source(
            "二一教育网",
            ("21cnjy.com",),
            ("https://xiaoxue.21cnjy.com/", "https://www.21cnjy.com/"),
            (
                keywords[0].replace("部编版", "统编版"),
                keywords[1],
                english_aliases[-1],
                *keywords[3:-1],
                keywords[-1].replace("音乐美术", "音乐"),
                keywords[-1].replace("音乐美术", "美术"),
            ),
            only_free=True,
            browser_search_url="https://www.21cnjy.com/",
        ),
        Source(
            "小学试卷网（xxsj.org）",
            ("xxsj.org",),
            (f"https://www.xxsj.org/grade/{grade_slug}.html",) if grade_slug else ("https://www.xxsj.org/",),
            keywords,
            jiangsu_alternative=True,
        ),
    )


class SourceError(RuntimeError):
    pass


class SourceAccessError(SourceError):
    """The source denied access; stop rather than retrying other routes."""


class PoliteFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request: dict[str, float] = {}

    def get(self, url: str, source: Source) -> tuple[str, str]:
        return self.request("GET", url, source)

    def request(
        self,
        method: str,
        url: str,
        source: Source,
        params: tuple[tuple[str, str], ...] = (),
    ) -> tuple[str, str]:
        current_url = url
        current_method = method.upper()
        current_params = params
        for redirect_count in range(MAX_REDIRECTS + 1):
            current = safe_resource_url(current_url, source.domains)
            if current is None:
                raise SourceError("request URL is outside the source's HTTPS domain allowlist")
            host = urlsplit(current).hostname or ""
            wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()
            try:
                response = self.session.request(
                    current_method,
                    current,
                    params=current_params if current_method == "GET" else None,
                    data=current_params if current_method == "POST" else None,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.exceptions.SSLError as exc:
                raise SourceError("TLS validation or handshake failed; certificate checks remain enabled") from exc
            except requests.exceptions.ProxyError as exc:
                raise SourceError("network proxy connection failed") from exc
            except requests.Timeout as exc:
                raise SourceError("network request timed out") from exc
            except requests.RequestException as exc:
                raise SourceError(f"network request failed ({exc.__class__.__name__})") from exc
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    if redirect_count >= MAX_REDIRECTS:
                        raise SourceError("too many redirects")
                    location = response.headers.get("Location")
                    if not location:
                        raise SourceError("redirect response omitted Location")
                    current_url = urljoin(current, location)
                    if current_method != "GET" and response.status_code in (301, 302, 303):
                        current_method = "GET"
                        current_params = ()
                    continue
                if response.status_code in (401, 403, 429):
                    raise SourceAccessError(f"HTTP status {response.status_code}; source scan stopped")
                if response.status_code >= 400:
                    raise SourceError(f"HTTP status {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise SourceError("response exceeded the 3 MiB size limit")
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                response.encoding = response.apparent_encoding or response.encoding
                final_url = safe_resource_url(response.url, source.domains)
                if final_url is None:
                    raise SourceError("response URL is outside the source's HTTPS domain allowlist")
                return response.text, final_url
            finally:
                response.close()
        raise SourceError("too many redirects")


def _text(tag: Tag) -> str:
    return " ".join(tag.stripped_strings)


def _form_search_fields(form: Tag) -> list[Tag]:
    inputs = [
        item
        for item in form.find_all(["input", "textarea"])
        if (item.get("type") or "text").lower() not in ("hidden", "submit", "button", "checkbox", "radio", "password")
        and item.get("name")
    ]
    hinted = [
        item
        for item in inputs
        if any(
            hint in " ".join(str(item.get(key, "")) for key in ("name", "id", "placeholder")).lower()
            for hint in SEARCH_FIELD_HINTS
        )
    ]
    return hinted or (inputs if len(inputs) == 1 else [])


def _search_requests(
    html: str, page_url: str, source: Source, keyword: str
) -> list[SearchRequest]:
    soup = BeautifulSoup(html, "html.parser")
    requests_found: list[SearchRequest] = []
    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        if method not in ("get", "post"):
            continue
        fields = _form_search_fields(form)
        if len(fields) != 1:
            continue
        action = urljoin(page_url, form.get("action") or page_url)
        safe_action = safe_resource_url(action, source.domains)
        if safe_action is None:
            continue
        action_parts = urlsplit(safe_action)
        field_name = str(fields[0].get("name"))
        if method == "post" and not (
            action_parts.path.rstrip("/").lower().endswith("/search.php")
            and field_name.lower() == "srchtxt"
        ):
            continue
        params: list[tuple[str, str]] = []
        for control in form.find_all("input"):
            name = control.get("name")
            if not name or control is fields[0]:
                continue
            if (control.get("type") or "").lower() not in ("submit", "button", "file"):
                params.append((name, str(control.get("value", ""))))
        for control in form.find_all("select"):
            name = control.get("name")
            option = control.find("option", selected=True) or control.find("option")
            if name and option:
                params.append((name, str(option.get("value", _text(option)))))
        params.append((field_name, keyword))
        parts = urlsplit(safe_action)
        if method == "get":
            query = parse_qsl(parts.query, keep_blank_values=True) + params
            safe_action = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
            params = []
        request = SearchRequest(method.upper(), safe_action, tuple(params))
        if request not in requests_found:
            requests_found.append(request)
    return requests_found


def _candidate_container(anchor: Tag) -> Tag:
    row = anchor.find_parent(["li", "tr", "article"])
    if row is not None:
        return row
    for ancestor in anchor.parents:
        if not isinstance(ancestor, Tag):
            continue
        classes = " ".join(ancestor.get("class", []))
        identity = f"{classes} {ancestor.get('id', '')}"
        if ancestor.name in ("li", "tr", "article") or ARTICLE_CLASSES.search(identity):
            return ancestor
        if ancestor.name in ("body", "html"):
            break
    return anchor.parent if isinstance(anchor.parent, Tag) else anchor


def _has_explicit_free_label(container: Tag) -> bool:
    if any(marker in _text(tag) for marker in NOT_FULLY_FREE_MARKERS for tag in container.find_all(True)):
        return False
    if any(marker in _text(container) for marker in PAID_MARKERS):
        return False
    for tag in container.find_all(True):
        classes = " ".join(tag.get("class", [])).lower()
        label = _text(tag).strip()
        if "free" in classes and label in ("免费", "免费资源", "免费下载", "免费试卷"):
            return True
        is_badge = any(token in classes for token in ("tag", "badge", "label"))
        if is_badge and label in ("免费", "免费资源", "免费下载", "免费试卷"):
            return True
        if is_badge and any(marker in label for marker in FREE_MARKERS):
            return True
        if tag.name not in ("a", "script", "style") and label == "免费":
            return True
    return False


def _resource_metadata(container: Tag) -> dict:
    for node in container.select("[hmtproperty]"):
        try:
            metadata = json.loads(str(node.get("hmtproperty", "")))
        except (ValueError, TypeError):
            continue
        if isinstance(metadata, dict) and isinstance(metadata.get("title"), str):
            return metadata
    return {}


def _contains_keyword(text: str, keyword: str) -> bool:
    compact_text = re.sub(r"\s+", "", text).lower()
    compact_keyword = re.sub(r"\s+", "", keyword).lower()
    match = re.fullmatch(r"(.+?)([一二三四五六七八九十]+年级)(.+)", compact_keyword)
    if not match:
        return compact_keyword in compact_text
    version, grade, subject = match.groups()
    versions = {
        "部编版": ("部编版", "部编", "统编版", "统编"),
        "部编": ("部编", "统编"),
        "译林一起点": ("译林一起点", "译林版一起点", "译林版一年级起点"),
    }.get(version, (version,))
    if not any(alias in compact_text for alias in versions) or grade not in compact_text:
        return False
    if subject == "音乐美术":
        return "音乐" in compact_text or "美术" in compact_text
    if subject == "英语" and any(term in compact_text for term in ("三起点", "三年级起点", "3起点")):
        return False
    return subject in compact_text


def extract_papers(
    html: str,
    page_url: str,
    source: Source,
    keywords: Iterable[str],
    today: date,
) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        title = _text(anchor)
        if not title:
            continue
        raw_url = urljoin(page_url, str(anchor["href"]))
        link = safe_resource_url(raw_url, source.domains)
        if link is None:
            continue
        container = _candidate_container(anchor)
        metadata = _resource_metadata(container) if source.only_free else {}
        if any(term in title for term in source.exclude_terms):
            continue
        basis = eligibility_basis(title)
        if basis is None:
            continue
        if not matches_current_term(title, today):
            continue
        if source.jiangsu_alternative and not any(
            word in title for word in ("卷", "试题", "测试", "练习", "检测", "测评", "考试", "作业", "测验", "默写", "听力")
        ):
            continue
        if source.only_free and not _has_explicit_free_label(container):
            continue
        if source.only_free and metadata:
            types = metadata.get("types")
            if not isinstance(types, dict) or "试卷" not in types.values():
                continue
            if str(metadata.get("coin", "0")) not in ("0", "0.0"):
                continue
        elif not any(
            word in title for word in ("试卷", "试题", "测试", "练习", "检测", "默写", "听力", "期末", "期中", "测评", "考试", "作业", "卷")
        ):
            continue
        if not is_recent_title_year(title, today):
            continue
        item = {
            "title": title,
            "link": link,
            "source": source.name,
            "date_basis": "title_year",
            "title_year": str(title_year(title)),
            "eligibility_basis": basis,
        }
        if is_jiangsu_title(title):
            item["region_basis"] = "江苏地区标题"
        results.append(item)
    return results


def _has_public_listing(
    html: str, page_url: str, source: Source, keywords: Iterable[str]
) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        link = safe_resource_url(urljoin(page_url, str(anchor["href"])), source.domains)
        if link is None:
            continue
        title = _text(anchor)
        if any(term in title for term in source.exclude_terms):
            continue
        if any(_contains_keyword(title, keyword) for keyword in keywords):
            return True
    return False


def _scan_source(source: Source, today: date, fetcher: PoliteFetcher) -> tuple[list[dict[str, str]], bool]:
    papers: list[dict[str, str]] = []
    errors: list[str] = []
    search_pages: dict[str, set[SearchRequest]] = {keyword: set() for keyword in source.keywords}
    seed_pages: list[tuple[str, str]] = []
    used_urls: set[str] = set()
    listing_evidence = False
    query_count = 0

    for entry_url in source.entry_urls:
        if entry_url in used_urls:
            continue
        used_urls.add(entry_url)
        try:
            body, final_url = fetcher.get(entry_url, source)
        except SourceAccessError as exc:
            LOGGER.error("%s: %s", source.name, exc)
            return papers, False
        except SourceError as exc:
            errors.append(str(exc))
            continue
        seed_pages.append((body, final_url))
        listing_evidence = listing_evidence or _has_public_listing(
            body, final_url, source, source.keywords
        )
        for keyword in source.keywords:
            if query_count >= MAX_RESULT_PAGES_PER_SOURCE:
                break
            for search_request in _search_requests(body, final_url, source, keyword):
                request_key = f"{search_request.method}:{search_request.url}:{search_request.params!r}"
                if request_key in used_urls:
                    continue
                used_urls.add(request_key)
                search_pages[keyword].add(search_request)
                query_count += 1
                if query_count >= MAX_RESULT_PAGES_PER_SOURCE:
                    break

    for keyword, requests_to_make in search_pages.items():
        for search_request in requests_to_make:
            if query_count > MAX_RESULT_PAGES_PER_SOURCE:
                break
            try:
                body, final_url = fetcher.request(
                    search_request.method,
                    search_request.url,
                    source,
                    search_request.params,
                )
            except SourceAccessError as exc:
                LOGGER.error("%s: %s", source.name, exc)
                return papers, False
            except SourceError as exc:
                errors.append(str(exc))
                continue
            soup = BeautifulSoup(body, "html.parser")
            text = soup.get_text(" ", strip=True)
            if not (
                _has_public_listing(body, final_url, source, (keyword,))
                or any(marker in text for marker in EMPTY_RESULT_MARKERS)
                or soup.select("li.pbw, #threadlisttableid, .slst")
            ):
                errors.append("search response did not contain a recognized result or explicit empty state")
                continue
            papers.extend(extract_papers(body, final_url, source, (keyword,), today))
            listing_evidence = True

    for body, page_url in seed_pages:
        papers.extend(extract_papers(body, page_url, source, source.keywords, today))

    successful = listing_evidence and not errors
    if not successful:
        errors.append("no usable public search form or target listing was discoverable")
    if errors:
        LOGGER.warning("%s: %d diagnostic(s); first: %s", source.name, len(errors), errors[0])
    LOGGER.info("%s: %d candidate(s) discovered", source.name, len(papers))
    return papers, successful


def _browser_navigate(page, url: str, source: Source) -> None:
    safe_url = safe_resource_url(url, source.domains)
    if safe_url is None:
        raise SourceError("public search link is outside the source domain")
    response = page.goto(
        safe_url,
        wait_until="domcontentloaded",
        timeout=REQUEST_TIMEOUT_SECONDS * 1000,
    )
    if response is not None and response.status in (401, 403, 429):
        raise SourceAccessError(f"HTTP status {response.status}; source scan stopped")
    if response is not None and response.status >= 400:
        raise SourceError(f"public search returned HTTP status {response.status}")
    page.wait_for_timeout(2000)
    if safe_resource_url(page.url, source.domains) is None:
        raise SourceError("public search navigated outside the source domain")


def _search_filter_url(html: str, page_url: str, source: Source, label: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href][data-param]"):
        if anchor.get_text(strip=True) == label:
            return safe_resource_url(urljoin(page_url, str(anchor["href"])), source.domains)
    return None


def _verified_result_state(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select(".search-result li.pt-item, .resource-item"):
        return True
    text = soup.get_text(" ", strip=True)
    return bool(re.search(r"共\s*0\s*(?:个|条|份)", text)) or any(
        marker in text for marker in ("暂无搜索结果", "没有搜索结果", "无搜索结果", "未搜索到相关资源")
    )


def _wait_for_result_state(page) -> None:
    page.wait_for_function(
        """() => {
            const text = document.body?.innerText || "";
            return Boolean(document.querySelector(".search-result li.pt-item, .resource-item"))
                || /共\\s*0\\s*(?:个|条|份)/.test(text)
                || ["暂无搜索结果", "没有搜索结果", "无搜索结果", "未搜索到相关资源",
                    "请完成安全验证", "访问过于频繁", "access denied", "request blocked"]
                    .some(marker => text.toLowerCase().includes(marker));
        }""",
        timeout=REQUEST_TIMEOUT_SECONDS * 1000,
    )


def _collect_free_results(page, source: Source, keyword: str, today: date) -> list[dict[str, str]]:
    free_url = _search_filter_url(page.content(), page.url, source, "免费")
    if free_url is None:
        raise SourceError("public free-only filter was not discoverable")
    _browser_navigate(page, free_url, source)
    paper_url = _search_filter_url(page.content(), page.url, source, "试卷")
    if paper_url:
        _browser_navigate(page, paper_url, source)

    papers: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    for page_number in range(MAX_BROWSER_PAGES_PER_QUERY):
        _wait_for_result_state(page)
        body = page.content()
        text = page.locator("body").inner_text(timeout=10_000).lower()
        if any(marker in text for marker in ("请完成安全验证", "访问过于频繁", "access denied", "request blocked")):
            raise SourceAccessError("site presented an access restriction; no bypass attempted")
        if not _verified_result_state(body):
            raise SourceError("free search has neither recognizable resource cards nor an explicit empty state")
        papers.extend(extract_papers(body, page.url, source, (keyword,), today))
        seen_pages.add(page.url)
        soup = BeautifulSoup(body, "html.parser")
        next_url = next(
            (
                safe_resource_url(urljoin(page.url, str(anchor["href"])), source.domains)
                for anchor in soup.select("a[href]")
                if anchor.get_text(strip=True) in ("下一页", "后一页")
                and "disabled" not in anchor.get("class", [])
            ),
            None,
        )
        if next_url is None or next_url in seen_pages:
            break
        if page_number + 1 == MAX_BROWSER_PAGES_PER_QUERY:
            LOGGER.warning("%s: daily scan bounded at %d public result pages per query", source.name, MAX_BROWSER_PAGES_PER_QUERY)
            break
        _browser_navigate(page, next_url, source)
    return papers


def _scan_browser_source(source: Source, today: date) -> tuple[list[dict[str, str]], bool]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        LOGGER.error("%s: Playwright is required for its public search page", source.name)
        return [], False
    if not source.browser_search_url:
        LOGGER.error("%s: public browser search URL is not configured", source.name)
        return [], False

    papers: list[dict[str, str]] = []
    errors: list[str] = []
    verified_queries = 0

    def restrict_network(route) -> None:
        parts = urlsplit(route.request.url)
        if parts.scheme == "data" or (
            parts.scheme == "https"
            and parts.hostname
            and any(
                parts.hostname.lower() == domain
                or parts.hostname.lower().endswith("." + domain)
                for domain in source.domains
            )
        ):
            route.continue_()
        else:
            route.abort()

    try:
        with sync_playwright() as playwright:
            channel = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip()
            if channel not in ("", "chrome", "msedge"):
                raise SourceError("PLAYWRIGHT_CHANNEL must be chrome or msedge")
            browser = playwright.chromium.launch(headless=True, channel=channel or None)
            try:
                context = browser.new_context()
                context.route("**/*", restrict_network)
                document_statuses: dict[str, int] = {}

                def record_document_status(response) -> None:
                    if response.request.resource_type == "document":
                        document_statuses[response.url] = response.status

                context.on("response", record_document_status)
                page = context.new_page()
                def find_one_visible(selectors: tuple[str, ...]):
                    for selector in selectors:
                        locator = page.locator(selector)
                        if locator.count() == 1:
                            return locator
                    return None

                input_selectors = (
                    'input[name="keyword"]:visible',
                    'input[type="search"]:visible',
                    "input.search-input:visible",
                )
                button_selectors = (
                    "#search_centers:visible",
                    "button.search-btn:visible",
                    'button[type="submit"]:visible',
                )
                for keyword in source.keywords:
                    try:
                        _browser_navigate(page, source.browser_search_url, source)
                        search_input = find_one_visible(input_selectors)
                        if search_input is None:
                            response = page.goto(
                                source.browser_search_url,
                                wait_until="domcontentloaded",
                                timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                            )
                            if response is not None and response.status >= 400:
                                raise SourceError(
                                    f"browser navigation returned HTTP status {response.status}"
                                )
                            page.wait_for_timeout(2000)
                            search_input = find_one_visible(input_selectors)
                        if search_input is None:
                            raise SourceError("public search input was not uniquely discoverable")
                        search_button = find_one_visible(button_selectors)
                        if search_button is None:
                            raise SourceError("public search button was not uniquely discoverable")
                        search_input.fill(keyword)
                        popups = []

                        def record_popup(popup) -> None:
                            popups.append(popup)

                        page.on("popup", record_popup)
                        try:
                            search_button.click(timeout=10_000)
                            page.wait_for_timeout(2000)
                        finally:
                            page.remove_listener("popup", record_popup)
                        result_page = popups[-1] if popups else page
                        if result_page is not page:
                            result_page.wait_for_load_state(
                                "domcontentloaded",
                                timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                            )
                            result_page.wait_for_timeout(1000)
                        if safe_resource_url(result_page.url, source.domains) is None:
                            raise SourceError("search navigated outside the source domain")
                        status = document_statuses.get(result_page.url)
                        if status in (401, 403, 429):
                            raise SourceAccessError(f"HTTP status {status}; source scan stopped")
                        if status is not None and status >= 400:
                            raise SourceError(f"search result returned HTTP status {status}")
                        _wait_for_result_state(result_page)
                        body_text = result_page.locator("body").inner_text(timeout=10_000)
                        challenge = result_page.locator(
                            'input[name*="captcha"], input[id*="captcha"], iframe[src*="captcha"]'
                        ).count()
                        if challenge or any(
                            marker in body_text.lower()
                            for marker in (
                                "访问过于频繁",
                                "请完成安全验证",
                                "访问被拒绝",
                                "访问受限",
                                "access denied",
                                "request blocked",
                                "captcha",
                            )
                        ):
                            raise SourceAccessError("site presented an access restriction or challenge")

                        if not _verified_result_state(result_page.content()):
                            LOGGER.warning(
                                "%s: unrecognized search response; HTTP=%s, text_characters=%d, resource_cards=%d",
                                source.name, status if status is not None else "unknown",
                                len(body_text), result_page.locator(".search-result li.pt-item, .resource-item").count(),
                            )
                            raise SourceError("public search returned no verifiable result state; source scan stopped")
                        found = _collect_free_results(result_page, source, keyword, today)
                        papers.extend(found)
                        verified_queries += 1
                        LOGGER.info("%s: completed public free search %d/%d; %d eligible candidate(s)",
                                    source.name, verified_queries, len(source.keywords), len(found))
                        if result_page is not page:
                            result_page.close()
                    except SourceError as exc:
                        errors.append(str(exc))
                        break
                    except PlaywrightError as exc:
                        errors.append(f"browser interaction failed ({exc.__class__.__name__})")
                        break
            finally:
                try:
                    browser.close()
                except PlaywrightError:
                    errors.append("browser failed to close cleanly")
    except SourceError as exc:
        errors.append(str(exc))
    except PlaywrightError as exc:
        errors.append(f"browser startup or navigation failed ({exc.__class__.__name__})")

    successful = verified_queries == len(source.keywords) and not errors
    if errors:
        LOGGER.warning("%s: %d diagnostic(s); first: %s", source.name, len(errors), errors[0])
    LOGGER.info("%s: %d candidate(s) discovered", source.name, len(papers))
    return papers, successful


def _scan_xxsj(source: Source, today: date, fetcher: PoliteFetcher) -> tuple[list[dict[str, str]], bool]:
    grade_path = urlsplit(source.entry_urls[0]).path
    grade = grade_path.rsplit("/", 1)[-1].removesuffix(".html")
    semester = "sc" if semester_for_date(today) == "上" else "xc"
    subjects = r"(?:yuwen|shuxue|yingyu|daode|kexue)"
    category_patterns = (
        rf"/gsubject/{grade}/{subjects}\.html",
        rf"/gsemeter/{grade}/{subjects}/{semester}\.html",
        rf"/sarea/js/{grade}/{subjects}\.html",
    )
    paging = re.compile(
        rf"/(?:gsemeter/{grade}/{subjects}/{semester}|sarea/js/{grade}/{subjects})/2\.html"
    )
    queue = list(source.entry_urls)
    visited: set[str] = set()
    papers: list[dict[str, str]] = []
    errors: list[str] = []
    while queue and len(visited) < MAX_RESULT_PAGES_PER_SOURCE:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            body, final_url = fetcher.get(url, source)
        except SourceAccessError as exc:
            LOGGER.error("%s: %s", source.name, exc)
            return papers, False
        except SourceError as exc:
            errors.append(str(exc))
            continue
        soup = BeautifulSoup(body, "html.parser")
        resource_links = [
            anchor for anchor in soup.select("a[href]")
            if re.fullmatch(r"/sjdetail/\d+\.html", urlsplit(urljoin(final_url, str(anchor["href"]))).path)
        ]
        if not resource_links and not any(marker in soup.get_text() for marker in EMPTY_RESULT_MARKERS):
            errors.append("category has neither resource links nor an explicit empty state")
            continue
        # Only detail links are resources; navigation/sidebar labels cannot qualify.
        listing = "<ul>" + "".join(f"<li>{anchor}</li>" for anchor in resource_links) + "</ul>"
        papers.extend(extract_papers(listing, final_url, source, source.keywords, today))
        for anchor in soup.select("a[href]"):
            target = safe_resource_url(urljoin(final_url, str(anchor["href"])), source.domains)
            if target is None or target in visited or target in queue:
                continue
            path = urlsplit(target).path
            if any(re.fullmatch(pattern, path) for pattern in category_patterns) or paging.fullmatch(path):
                queue.append(target)
    if queue:
        LOGGER.warning("%s: daily scan bounded at %d category pages", source.name, MAX_RESULT_PAGES_PER_SOURCE)
    if errors:
        LOGGER.warning("%s: %d diagnostic(s); first: %s", source.name, len(errors), errors[0])
    LOGGER.info("%s: %d category page(s), %d candidate(s) discovered", source.name, len(visited), len(papers))
    return papers, bool(visited) and not errors


def crawl_all(today: date, source_filter: str = "all") -> ScanResult:
    fetcher = PoliteFetcher()
    papers: list[dict[str, str]] = []
    failures: list[str] = []
    successful_sources = 0
    source_counts: dict[str, int] = {}
    from .academic import keywords_for_date

    if source_filter not in ("all", "primary", "xxsj"):
        raise ValueError("source_filter must be all, primary or xxsj")
    sources = source_definitions(keywords_for_date(today))
    if source_filter == "primary":
        sources = sources[:1]
    elif source_filter == "xxsj":
        sources = sources[3:]
    for source in sources:
        if source.jiangsu_alternative:
            source_papers, successful = _scan_xxsj(source, today, fetcher)
        elif source.browser_search_url:
            source_papers, successful = _scan_browser_source(source, today)
        else:
            source_papers, successful = _scan_source(source, today, fetcher)
        papers.extend(source_papers)
        source_counts[source.name] = len({paper["link"] for paper in source_papers})
        if successful:
            successful_sources += 1
        else:
            failures.append(source.name)
    unique: dict[str, dict[str, str]] = {}
    for paper in papers:
        unique.setdefault(paper["link"], paper)
    return ScanResult(list(unique.values()), failures, successful_sources, source_counts, len(sources))
