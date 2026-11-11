"""Read-only probe of NSDL's official historical FPI and DII WebForms archives.

This phase preserves source HTML and request provenance. It deliberately does not
publish normalized flow rows or treat a third-party scraper as source evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.fpi.nsdl.co.in/web/"
FPI_PATH = "Reports/Archive.aspx"
DII_PATH = "Users/DIIGenerateReport.aspx?Rep=A"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DATE_FORMAT = "%d-%b-%Y"


class HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        item = {key.lower(): value or "" for key, value in attrs}
        name = item.get("name")
        if name:
            self.fields[name] = item.get("value", "")


def hidden_fields(html: str) -> dict[str, str]:
    parser = HiddenInputParser()
    parser.feed(html)
    return parser.fields


def month_query_dates(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date precedes start date")
    out: list[date] = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        out.append(min(next_month - timedelta(days=1), end))
        year, month = next_month.year, next_month.month
    return out


def weekdays(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date precedes start date")
    out = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class NsdlWebFormsClient:
    def __init__(self, delay: float = 1.0, timeout: int = 60, retries: int = 4):
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.last_request = 0.0
        self.states: dict[str, dict[str, str]] = {}

    def _request(self, request: Request) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        raise RuntimeError(f"unexpected HTTP {response.status}")
                    return response.read()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {403, 429, 500, 502, 503, 504} or attempt + 1 == self.retries:
                    break
            except (URLError, TimeoutError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 == self.retries:
                    break
            time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"NSDL request failed: {last_error}") from last_error

    def get_form(self, relative_url: str) -> tuple[str, str]:
        url = BASE + relative_url
        request = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        html = self._request(request).decode("utf-8", errors="replace")
        fields = hidden_fields(html)
        if "__VIEWSTATE" not in fields:
            raise ValueError(f"NSDL WebForms page has no __VIEWSTATE: {url}")
        self.states[url] = fields
        return url, html

    def post_date(self, relative_url: str, query_date: date) -> tuple[str, bytes]:
        url = BASE + relative_url
        if url not in self.states:
            self.get_form(relative_url)
        state = self.states[url]
        date_text = query_date.strftime(DATE_FORMAT)
        fields = {
            **state,
            "__EVENTTARGET": "btnSubmit1",
            "__EVENTARGUMENT": "",
            "txtDate": date_text,
            "hdnDate": date_text,
        }
        body = urlencode(fields).encode("ascii")
        request = Request(url, data=body, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
            "Origin": "https://www.fpi.nsdl.co.in",
        })
        payload = self._request(request)
        html = payload.decode("utf-8", errors="replace")
        next_state = hidden_fields(html)
        if "__VIEWSTATE" not in next_state:
            self.states.pop(url, None)
            raise ValueError(f"NSDL postback response lacks __VIEWSTATE for {query_date}")
        self.states[url] = next_state
        return url, payload


def classify_response(kind: str, payload: bytes) -> dict:
    html = payload.decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()
    if "access denied" in low or "captcha" in low or "request blocked" in low:
        raise ValueError(f"{kind} response appears to be a block page")
    if kind == "fpi":
        has_header = "reporting date" in low and "gross purchase" in low
        if not has_header:
            raise ValueError("FPI archive response lacks expected reporting-date/gross-purchase table")
        date_cells = re.findall(
            r"\b\d{1,2}[-/](?:[A-Za-z]{3,9}|\d{1,2})[-/]\d{4}\b", text
        )
        return {"response_type": "data", "date_tokens_found": len(date_cells)}
    if "no data found" in low:
        return {"response_type": "no_data"}
    if "type of dii" not in low or "gross purchase" not in low:
        raise ValueError("DII archive response lacks expected Type of DII/gross-purchase table")
    return {"response_type": "data", "has_dii_table": True}


def capture(start: date, end: date, output: Path, delay: float = 1.0) -> dict:
    client = NsdlWebFormsClient(delay=delay)
    records: list[dict] = []
    for kind, relative_url, query_dates in (
        ("fpi", FPI_PATH, month_query_dates(start, end)),
        ("dii", DII_PATH, weekdays(start, end)),
    ):
        for query_date in query_dates:
            source_url, payload = client.post_date(relative_url, query_date)
            observation = classify_response(kind, payload)
            digest = hashlib.sha256(payload).hexdigest()
            folder = output / kind
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{query_date.isoformat()}_{digest[:16]}.html"
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"immutable raw-source collision: {path}")
            if not path.exists():
                path.write_bytes(payload)
            records.append({
                "source": "NSDL",
                "dataset": kind,
                "query_date": query_date.isoformat(),
                "source_url": source_url,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "http_status": 200,
                "response_bytes": len(payload),
                "response_sha256": digest,
                "raw_path": str(path),
                **observation,
            })
    manifest = {
        "publisher": "National Securities Depository Limited",
        "scope": "official_NS​​DL_archive_probe".replace("\u200b", ""),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fpi_query_count": sum(r["dataset"] == "fpi" for r in records),
        "dii_query_count": sum(r["dataset"] == "dii" for r in records),
        "observations": records,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-08-17")
    parser.add_argument("--end-date", default="2026-11-10")
    parser.add_argument("--output", type=Path, default=Path("data/raw/nsdl_cash_flows"))
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    result = capture(date.fromisoformat(args.start_date), date.fromisoformat(args.end_date), args.output, args.delay)
    print(json.dumps({
        "start_date": result["start_date"],
        "end_date": result["end_date"],
        "fpi_query_count": result["fpi_query_count"],
        "dii_query_count": result["dii_query_count"],
        "dii_data_days": sum(r.get("response_type") == "data" for r in result["observations"] if r["dataset"] == "dii"),
        "dii_no_data_days": sum(r.get("response_type") == "no_data" for r in result["observations"] if r["dataset"] == "dii"),
    }, indent=2))


if __name__ == "__main__":
    main()
