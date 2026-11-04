from __future__ import annotations

import logging
import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .dates import is_recent_title_year, title_year
from .academic import matches_current_term, grade_for_date, semester_for_date
from .history import DEFAULT_HISTORY_PATH, load_sent, save_sent, load_catalog, save_catalog
from .mailer import MailDeliveryError, send_email
from .security import RESOURCE_DOMAINS, safe_resource_url
from .sources import ScanResult, crawl_all
from .eligibility import eligibility_basis

LOGGER = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def newly_found(result: ScanResult, sent: set[str], today: date | None = None) -> list[dict[str, str]]:
    fresh: list[dict[str, str]] = []
    seen = {safe_resource_url(link, RESOURCE_DOMAINS) or link for link in sent}
    for paper in result.papers:
        safe_link = safe_resource_url(paper.get("link", ""), RESOURCE_DOMAINS)
        if safe_link is None:
            LOGGER.warning("Discarded a resource with an unsafe URL")
            continue
        if safe_link in seen:
            continue
        check_date = today or datetime.now(SHANGHAI).date()
        title_based = (
            paper.get("date_basis") == "title_year"
            and paper.get("title_year") == str(title_year(paper.get("title", "")))
            and is_recent_title_year(paper.get("title", ""), check_date)
        )
        if not title_based or not matches_current_term(paper.get("title", ""), check_date):
            continue
        basis = eligibility_basis(paper.get("title", ""))
        if basis is None:
            LOGGER.warning("Discarded a resource without title evidence for Jiangsu or the required textbook version")
            continue
        item = dict(paper)
        item["link"] = safe_link
        item["eligibility_basis"] = basis
        fresh.append(item)
        seen.add(safe_link)
    return fresh


def run(dry_run: bool = False, report_path: Path | None = None, source_filter: str = "all") -> int:
    today = datetime.now(SHANGHAI).date()
    sent = load_sent(DEFAULT_HISTORY_PATH)
    catalog = load_catalog()
    result = crawl_all(today) if source_filter == "all" else crawl_all(today, source_filter)
    merged = {}
    for paper in (*catalog, *result.papers):
        link = safe_resource_url(paper.get("link", ""), RESOURCE_DOMAINS)
        if link:
            merged[link] = dict(paper, link=link)
    # Keep historical grades/terms in storage, but revalidate every displayed item.
    combined = ScanResult(list(merged.values()), result.failures, result.successful_sources)
    eligible = newly_found(combined, set(), today)
    normalized_sent = {safe_resource_url(link, RESOURCE_DOMAINS) or link for link in sent}
    fresh = [paper for paper in eligible if paper["link"] not in normalized_sent]
    new_source_counts = dict(Counter(paper.get("source", "未标明来源") for paper in fresh))
    for source, count in result.source_counts.items():
        LOGGER.info(
            "%s: eligible=%d, unsent=%d, scan=%s",
            source, count, new_source_counts.get(source, 0),
            "incomplete" if source in result.failures else "complete",
        )
    if result.failures:
        LOGGER.error(
            "Partial crawl failure: %d of %d source(s) could not be verified: %s",
            len(result.failures),
            result.expected_sources,
            ", ".join(result.failures),
        )
    elif result.successful_sources != result.expected_sources:
        LOGGER.error("Crawl did not verify all configured sources")
    failed = bool(result.failures) or result.successful_sources != result.expected_sources
    report = {
        "date": today.isoformat(),
        "grade": grade_for_date(today),
        "semester": semester_for_date(today),
        "dry_run": dry_run,
        "successful_sources": result.successful_sources,
        "expected_sources": result.expected_sources,
        "source_filter": source_filter,
        "failed_sources": result.failures,
        "eligible_resources": len(result.papers),
        "catalog_resources": len(merged),
        "daily_resources": len(eligible),
        "new_resources": len(fresh),
        "source_counts": result.source_counts,
        "new_source_counts": new_source_counts,
        "delivered": False,
        "history_saved": False,
    }
    try:
        if dry_run:
            LOGGER.info("DRY RUN: %d new resource(s); SMTP and history writes disabled", len(fresh))
        else:
            # Discovery persistence is separate from the record of accepted emails.
            save_catalog(list(merged.values()))
            statuses = {
                name: (
                    f"扫描未完成，已获取{count}份；历史合格资源仍保留"
                    if name in result.failures else f"扫描完成，本次获取{count}份合格资源"
                )
                for name, count in result.source_counts.items()
            }
            accepted = send_email(eligible, source_status=statuses, new_links={paper["link"] for paper in fresh})
            if not accepted:
                raise MailDeliveryError("Daily email was not accepted for delivery")
            report["delivered"] = True
            if fresh:
                sent.update(paper["link"] for paper in fresh)
                save_sent(sent, DEFAULT_HISTORY_PATH)
                report["history_saved"] = True
            LOGGER.info("Daily email accepted: %d total resource(s), %d new; catalog saved", len(eligible), len(fresh))
    finally:
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily school-paper catalog email with newly added resources")
    parser.add_argument("--dry-run", action="store_true", help="Scan all sources without SMTP or history writes")
    parser.add_argument("--report", type=Path, help="Write a counts-only JSON diagnostic report")
    parser.add_argument("--source", choices=("all", "primary", "xxsj"), default="all",
                        help="Scan all sources or only the primary paper site")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        exit_code = run(dry_run=args.dry_run, report_path=args.report, source_filter=args.source)
    except (RuntimeError, MailDeliveryError) as exc:
        LOGGER.error("%s", exc)
        exit_code = 1
    except Exception as exc:
        LOGGER.error("Unexpected failure (%s); sensitive exception details suppressed", exc.__class__.__name__)
        exit_code = 1
    sys.exit(exit_code)
