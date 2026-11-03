from __future__ import annotations

import logging
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from .dates import is_recent, is_recent_title_year, title_year
from .history import DEFAULT_HISTORY_PATH, load_sent, save_sent
from .mailer import MailDeliveryError, send_email
from .security import RESOURCE_DOMAINS, safe_resource_url
from .sources import ScanResult, crawl_all

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
        try:
            published = date.fromisoformat(paper["published"]) if paper.get("published") else None
        except ValueError:
            published = None
        check_date = today or datetime.now(SHANGHAI).date()
        title_based = (
            not paper.get("published")
            and paper.get("date_basis") == "title_year"
            and urlsplit(safe_link).hostname == "appsj.szxuexiao.com"
            and paper.get("title_year") == str(title_year(paper.get("title", "")))
            and is_recent_title_year(paper.get("title", ""), check_date)
        )
        if not is_recent(published, check_date) and not title_based:
            continue
        item = dict(paper)
        item["link"] = safe_link
        fresh.append(item)
        seen.add(safe_link)
    return fresh


def run(dry_run: bool = False, report_path: Path | None = None, source_filter: str = "all") -> int:
    today = datetime.now(SHANGHAI).date()
    sent = load_sent(DEFAULT_HISTORY_PATH)
    result = crawl_all(today) if source_filter == "all" else crawl_all(today, source_filter)
    fresh = newly_found(result, sent, today)
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
        "dry_run": dry_run,
        "successful_sources": result.successful_sources,
        "expected_sources": result.expected_sources,
        "source_filter": source_filter,
        "failed_sources": result.failures,
        "eligible_resources": len(result.papers),
        "new_resources": len(fresh),
        "source_counts": result.source_counts,
        "delivered": False,
        "history_saved": False,
    }
    try:
        if dry_run:
            LOGGER.info("DRY RUN: %d new resource(s); SMTP and history writes disabled", len(fresh))
        elif fresh:
            send_email(fresh)
            report["delivered"] = True
            sent.update(paper["link"] for paper in fresh)
            save_sent(sent, DEFAULT_HISTORY_PATH)
            report["history_saved"] = True
            LOGGER.info("Email delivered and %d resource URL(s) recorded", len(fresh))
        elif failed:
            LOGGER.warning("No eligible additions collected; scan incomplete, not a verified empty result")
        else:
            LOGGER.info("No new resources; no email sent")
    finally:
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily public school-paper discovery and email delivery")
    parser.add_argument("--dry-run", action="store_true", help="Scan all sources without SMTP or history writes")
    parser.add_argument("--report", type=Path, help="Write a counts-only JSON diagnostic report")
    parser.add_argument("--source", choices=("all", "primary"), default="all",
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
