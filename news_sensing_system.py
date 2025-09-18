"""
news_sensing_system.py
======================

This script provides a starting point for building an hourly compliance‑news
sensing and auto‑emailing system.  It pulls articles from a configurable
set of RSS/Atom feeds, filters them by keywords relevant to
regulatory compliance, translates any foreign‑language articles into Korean,
and prepares a digest that can be emailed to stakeholders.  The code is
designed to be easy to extend: you can add more feeds, tweak the keyword
lists, or integrate a different translation service without changing
much of the core logic.

Key features:

* Uses the ``feedparser`` library to read RSS/Atom feeds.  Feedparser
  gracefully handles poorly formatted feeds and exposes entries via a
  simple Python API.
* Uses the ``googletrans`` package to translate non‑Korean content into
  Korean.  Googletrans wraps the free Google Translate Ajax API; it
  automatically detects source languages and supports bulk translation【713202688050716†L38-L49】.
  If a more stable or enterprise translation API is preferred, swap
  out the translator implementation here.
* Stores a small cache of previously processed article identifiers in
  ``processed_items.txt``.  This prevents duplicate notifications when
  the script runs again.
* Gathers matching articles into a plain‑text email body.  Each entry
  includes the source name, original title and description, a Korean
  translation (for non‑Korean sources), and a permalink.
* The ``send_email`` function is stubbed to show how you might send
  the digest via SMTP.  Credentials and server settings are read from
  environment variables so sensitive information never appears in
  the source code.

Before running this script you need to install dependencies:

    pip install feedparser googletrans==3.0.0rc1 schedule requests beautifulsoup4

The ``requests`` and ``beautifulsoup4`` packages are used to scrape
public data pages (e.g. the Ministry of Government Legislation) that
do not expose RSS feeds.  ``googletrans`` is required for on‑the‑fly
translations.

Next, set up environment variables for your email account, for example:

    export SMTP_HOST="smtp.gmail.com"
    export SMTP_PORT="465"
    export SMTP_USER="your_email@gmail.com"
    export SMTP_PASSWORD="your_email_password"
    export EMAIL_TO="recipient1@example.com,recipient2@example.com"

You can then schedule the script to run every hour using cron or
the ``schedule`` module (the scheduling code is included but
commented out; you can uncomment it if you prefer a pure‑Python
solution).
"""

import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import feedparser
from googletrans import Translator


# ---------------------------------------------------------------------------
# Configuration
#
# Each feed definition includes a human‑readable ``name`` (used in the
# digest), a ``url`` pointing at an RSS/Atom feed, and a ``language``
# code indicating the primary language of the content.  For Korean
# sources set ``language`` to ``'ko'`` so the system knows no
# translation is needed.
FEEDS: List[Dict[str, str]] = [
    {
        "name": "Global Compliance News",
        "url": "https://www.globalcompliancenews.com/feed/",
        "language": "en",
    },
    {
        "name": "Corporate Compliance Insights",
        "url": "https://www.corporatecomplianceinsights.com/feed/",
        "language": "en",
    },
    # Lexology provides a number of topic‑specific RSS feeds.  The URL
    # below corresponds to the general feed; replace or add feeds as
    # needed.  Access to Lexology may require registration.
    {
        "name": "Lexology",
        "url": "https://www.lexology.com/rss" ,
        "language": "en",
    },
    # LawTimes Korea does not publish a public RSS feed at the time of
    # writing.  To include it in this system you would need to build a
    # custom scraper that fetches pages and parses out the latest
    # articles.  Once you have such a scraper you can expose its
    # results as a feed or integrate it directly into ``fetch_feed``.
    # {
    #     "name": "법률신문 (LawTimes)",
    #     "url": "https://www.lawtimes.co.kr/rss",  # hypothetical
    #     "language": "ko",
    # },
]

# Additional Korean public sources might not provide RSS feeds.  For
# example, the Ministry of Government Legislation’s portal
# (https://www.moleg.go.kr) lists public datasets and announcements
# but does not expose an Atom feed.  The function below shows how
# such pages can be scraped manually using ``requests`` and
# ``BeautifulSoup``.  You can extend this pattern for other
# government sources.

import re
import requests
from bs4 import BeautifulSoup  # type: ignore


def fetch_moleg_public_data(processed: set) -> List[Dict[str, str]]:
    """
    Scrape the Ministry of Government Legislation’s public data page
    for new announcements.  Because the site does not publish an
    official RSS feed, we fetch the HTML, parse relevant list items
    and return article dictionaries.  This is a simple example and
    may need to be adjusted if the page structure changes.

    Args:
        processed: set of already processed identifiers.

    Returns:
        List of article dictionaries similar to those returned by
        ``fetch_feed``.
    """
    url = "https://www.moleg.go.kr/menu.es?mid=a10203010000"
    results: List[Dict[str, str]] = []
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # Example: find list items under a section containing public data entries.
        # On the current page, announcements may be structured as <li><a>Title</a></li>.
        # Adjust the selectors based on the actual markup.
        for link in soup.select("div.boardType01 li a"):
            title = link.get_text(strip=True)
            href = link.get("href")
            if not href:
                continue
            # Build absolute URL if necessary
            if href.startswith("/"):
                full_link = "https://www.moleg.go.kr" + href
            else:
                full_link = href
            identifier = full_link
            if identifier in processed:
                continue
            # Basic keyword filter – check Korean keywords only for moleg
            if not any(kw in title for kw in KEYWORDS_KO):
                continue
            # Since moleg pages are in Korean, translation is not required
            results.append(
                {
                    "id": identifier,
                    "source": "법제처 공공데이터",
                    "title": title,
                    "summary": title,
                    "translation": title,
                    "link": full_link,
                    "published": "",
                }
            )
            processed.add(identifier)
    except Exception as exc:
        # Log or handle scraping errors
        print(f"Error scraping MOLEG public data page: {exc}")
    return results

# Keywords to match against article titles and descriptions.  The
# system checks Korean and English keywords separately so you can
# capture articles in either language.  Feel free to customise these
# lists; the ones below are drawn from the user’s request.
KEYWORDS_KO: List[str] = [
    "독점규제및공정거래에관한법률",
    "공정거래법",
    "상법",
    "노동법",
    "노조법",
    "근로기준법",
    "노랑봉투법",
    "중대재해재법",
    "대리점법",
    "하도급법",
    "정보보호법",
]

KEYWORDS_EN: List[str] = [
    "FCPA",
    "GDPR",
    "U.S. visas",
    "US visas",
    "immigration",
    "tariffs",
    # Add more English keywords relevant to the business.
]

# File for storing already processed entry identifiers (e.g. GUIDs or links).
PROCESSED_FILE = "processed_items.txt"


def load_processed() -> set:
    """Load a set of previously processed identifiers from disk."""
    processed = set()
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            for line in f:
                processed.add(line.strip())
    return processed


def save_processed(processed: set) -> None:
    """Persist the set of processed identifiers to disk."""
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        for item in sorted(processed):
            f.write(f"{item}\n")


def article_matches(text: str) -> bool:
    """
    Determine whether the given text matches any of the configured
    keywords.  Searches both the Korean and English keyword lists.

    Args:
        text: Combined title/summary string to search.

    Returns:
        True if any keyword appears (case‑insensitive), False otherwise.
    """
    lower_text = text.lower()
    for kw in KEYWORDS_KO:
        if kw.lower() in lower_text:
            return True
    for kw in KEYWORDS_EN:
        if kw.lower() in lower_text:
            return True
    return False


def fetch_feed(feed: Dict[str, str], processed: set, translator: Translator) -> List[Dict[str, str]]:
    """
    Parse a single feed and return a list of new entries that match
    the configured keywords.  Each result includes the original
    language title/summary, a Korean translation (if necessary), and
    source metadata.

    Args:
        feed: A dictionary describing the feed (name, url, language).
        processed: A set of identifiers of already processed entries.
        translator: An instance of googletrans.Translator for
            translations.

    Returns:
        A list of dictionaries representing matched articles.
    """
    results = []
    parsed = feedparser.parse(feed["url"])
    for entry in parsed.entries:
        # Use the entry link or guid as a unique identifier
        identifier = entry.get("id") or entry.get("guid") or entry.get("link")
        if not identifier or identifier in processed:
            continue  # skip duplicates

        # Combine title and summary for keyword search
        title = entry.get("title", "")
        summary = entry.get("summary", entry.get("description", ""))
        combined = f"{title}\n{summary}"

        if not article_matches(combined):
            continue  # skip irrelevant articles

        # Prepare translation if the feed is not Korean
        if feed["language"] != "ko":
            try:
                translated = translator.translate(combined, dest="ko").text
            except Exception as exc:
                translated = f"(번역 오류: {exc})"
        else:
            translated = combined

        results.append(
            {
                "id": identifier,
                "source": feed["name"],
                "title": title.strip(),
                "summary": summary.strip(),
                "translation": translated.strip(),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
            }
        )
        processed.add(identifier)

    return results


def build_digest(entries: List[Dict[str, str]]) -> str:
    """
    Build a plain‑text digest from the list of entries.  Each entry
    includes the original language and Korean translation (if needed).

    Args:
        entries: List of article dictionaries.

    Returns:
        A string suitable for inclusion in an email body.
    """
    lines = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"규제 준수 뉴스 요약 – {timestamp} (Asia/Seoul 기준)")
    lines.append("".rjust(60, "−"))
    for idx, item in enumerate(entries, 1):
        lines.append(f"{idx}. [{item['source']}] {item['title']}")
        # Include publication date if available
        if item.get("published"):
            lines.append(f"   발행일: {item['published']}")
        # Original language summary
        lines.append(f"   원문 요약: {item['summary']}")
        # Korean translation (will be identical to summary for Korean sources)
        # Add translation only if it differs from the original
        if item["translation"] and item["translation"] != item["summary"]:
            lines.append(f"   한국어 번역: {item['translation']}")
        # Provide the link for further reading
        if item.get("link"):
            lines.append(f"   링크: {item['link']}")
        lines.append("")  # blank line between entries
    return "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    """
    Send an email using SMTP.  SMTP configuration is drawn from
    environment variables.  In a production environment you may want
    to integrate with a transactional email service (e.g. Amazon SES,
    SendGrid) instead of raw SMTP.

    Args:
        subject: Subject line for the email.
        body: Plain‑text body of the message.
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    recipients = os.environ.get("EMAIL_TO", "").split(",")

    if not (smtp_host and smtp_user and smtp_pass and recipients):
        raise RuntimeError("SMTP credentials or recipient list not configured")

    from email.mime.text import MIMEText
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    import smtplib
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())


def run_once() -> None:
    """Fetch feeds, build a digest and send it via email if there are results."""
    processed = load_processed()
    translator = Translator(service_urls=["translate.google.co.kr", "translate.google.com"])
    all_entries: List[Dict[str, str]] = []
    for feed in FEEDS:
        try:
            entries = fetch_feed(feed, processed, translator)
            all_entries.extend(entries)
        except Exception as exc:
            # In production you might log this exception or send an alert
            print(f"Error processing feed {feed['name']}: {exc}")
    # Fetch additional sources such as MOLEG public data page
    more_entries = fetch_moleg_public_data(processed)
    all_entries.extend(more_entries)
    # Persist processed identifiers to avoid duplicates on next run
    save_processed(processed)
    if all_entries:
        # Sort entries by published date descending (if available)
        try:
            all_entries.sort(key=lambda x: x.get("published", ""), reverse=True)
        except Exception:
            pass
        body = build_digest(all_entries)
        subject = "[Compliance Digest] 신규 규제 소식 / 법률 변경 알림"
        send_email(subject, body)
        print(f"Sent digest with {len(all_entries)} articles.")
    else:
        print("No new articles matched the keywords.")


if __name__ == "__main__":
    # For demonstration purposes we run the fetch/send cycle once.  To
    # schedule hourly execution using Python's schedule library, uncomment
    # the following lines:
    #
    # import schedule
    # schedule.every().hour.do(run_once)
    # while True:
    #     schedule.run_pending()
    #     time.sleep(60)
    run_once()