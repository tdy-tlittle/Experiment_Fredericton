#!/usr/bin/env python3
"""Fetch public event listings for Fredericton, NB.

Collects events from:
1) City of Fredericton calendar pages
2) Eventbrite's Fredericton listings page

Outputs date, time, event name, cost, location, and website.

Usage examples:
    py get_events.py
    py get_events.py --format json
    py get_events.py --days-ahead 180
    py get_events.py --days-ahead 365 --csv events.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "Mozilla/5.0 (compatible; FrederictonEventsBot/1.0)"
TIMEOUT_SECONDS = 20

CITY_HOME = "https://www.fredericton.ca/en"
CITY_EVENT_LINK_PREFIX = "https://www.fredericton.ca/about-fredericton/calendar-events/"
EVENTBRITE_LIST = "https://www.eventbrite.ca/d/canada--fredericton/events/"
EVENTBRITE_NB_LIST = "https://www.eventbrite.ca/d/canada--new-brunswick/events/"
FREDERICTON_COORDS = (45.9636, -66.6431)

# Approximate city-center coordinates for rough distance estimates.
NB_CITY_COORDS = {
    "fredericton": (45.9636, -66.6431),
    "saint john": (45.2733, -66.0633),
    "moncton": (46.0878, -64.7782),
    "bathurst": (47.6186, -65.6517),
    "miramichi": (47.0289, -65.5019),
    "edmundston": (47.3737, -68.3267),
    "woodstock": (46.1529, -67.5989),
    "campbellton": (48.0075, -66.6737),
    "caraquet": (47.7946, -64.9385),
    "sussex": (45.7222, -65.5142),
    "st. stephen": (45.1922, -67.2756),
    "st stephen": (45.1922, -67.2756),
    "shediac": (46.2207, -64.5411),
    "dieppe": (46.0951, -64.7519),
    "riverview": (46.0618, -64.8052),
    "oromocto": (45.8351, -66.4796),
    "tracadie": (47.5129, -64.9186),
    "grand falls": (47.0474, -67.7392),
    "grand-sault": (47.0474, -67.7392),
}


@dataclass
class Event:
    date: str
    time: str
    event_name: str
    cost: str
    location: str
    distance_from_fredericton: str
    website: str
    source: str


def fetch_url(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8", errors="replace")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_tags(html: str) -> str:
    html = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>",
        " ",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", html, flags=re.IGNORECASE
    )
    html = re.sub(r"<[^>]+>", "\n", html)
    return unescape(html)


def text_lines_from_html(html: str) -> list[str]:
    text = strip_tags(html)
    lines = [normalize_space(x) for x in text.splitlines()]
    return [x for x in lines if x]


def first_match(patterns: Iterable[re.Pattern[str]], lines: Iterable[str]) -> str:
    for line in lines:
        for pat in patterns:
            if pat.search(line):
                return line
    return ""


DATE_TIME_PATTERNS = [
    re.compile(
        r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{1,2}:\d{2}\s*(AM|PM)(\s*ADT)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(Today|Tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+at\s+\d{1,2}:\d{2}\s*(AM|PM)(\s*ADT)?$",
        re.IGNORECASE,
    ),
    re.compile(r"^[A-Za-z]+\s+at\s+\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE),
]

WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


COST_PATTERNS = [
    re.compile(r"^Free$", re.IGNORECASE),
    re.compile(r"^From\s*\$\s*\d", re.IGNORECASE),
    re.compile(r"^Check ticket price on event$", re.IGNORECASE),
    re.compile(r"^\$\s*\d"),
]


def looks_like_noise(line: str) -> bool:
    lowered = line.lower()
    noise_markers = [
        "save this event",
        "share this event",
        "promoted",
        "followers",
        "going fast",
        "almost full",
        "view more",
        "see more",
        "site navigation",
    ]
    return any(x in lowered for x in noise_markers)


def split_city_date_time(value: str) -> tuple[str, str]:
    m = re.search(r"^([A-Za-z]+\s+\d{1,2}\s+\d{4})\s*-\s*(.+)$", value)
    if m:
        return m.group(1), m.group(2)
    return value, "Not listed"


def extract_date_from_eventbrite_line(raw: str) -> tuple[str, str]:
    value = normalize_space(raw)

    m = re.match(
        r"^(Today|Tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+at\s+(.+)$",
        value,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2)

    m = re.match(
        r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+([A-Za-z]{3}\s+\d{1,2})(?:,\s*(\d{4}))?,\s+(.+)$",
        value,
        flags=re.IGNORECASE,
    )
    if m:
        month_day = m.group(2)
        year = m.group(3)
        date_part = f"{month_day}, {year}" if year else month_day
        return date_part, m.group(4)

    return value, "Not listed"


def _next_weekday(today: date, target_weekday: int) -> date:
    days_ahead = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def resolve_event_date(event: Event, reference_date: date) -> date | None:
    raw = normalize_space(event.date)
    lowered = raw.lower()

    if lowered == "today":
        return reference_date
    if lowered == "tomorrow":
        return reference_date + timedelta(days=1)
    if lowered in WEEKDAY_TO_INDEX:
        return _next_weekday(reference_date, WEEKDAY_TO_INDEX[lowered])

    for pattern in ["%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue

    for pattern in ["%B %d", "%b %d"]:
        try:
            tentative = (
                datetime.strptime(raw, pattern).date().replace(year=reference_date.year)
            )
            if tentative < reference_date:
                tentative = tentative.replace(year=reference_date.year + 1)
            return tentative
        except ValueError:
            continue

    return None


def filter_events_within_days(
    events: list[Event], days_ahead: int, reference_date: date
) -> list[Event]:
    latest = reference_date + timedelta(days=days_ahead)
    filtered: list[Event] = []
    for event in events:
        resolved = resolve_event_date(event, reference_date)
        if resolved is None:
            continue
        if reference_date <= resolved <= latest:
            filtered.append(event)
    return filtered


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def estimate_distance_from_fredericton(location: str) -> str:
    lowered = location.lower()
    for city, coords in NB_CITY_COORDS.items():
        if city in lowered:
            km = haversine_km(
                FREDERICTON_COORDS[0], FREDERICTON_COORDS[1], coords[0], coords[1]
            )
            return f"{km:.0f} km"
    return "Unknown"


def parse_city_events() -> list[Event]:
    try:
        home_html = fetch_url(CITY_HOME)
    except (HTTPError, URLError) as exc:
        print(f"Warning: could not fetch city homepage: {exc}", file=sys.stderr)
        return []

    links = sorted(
        {
            urljoin(CITY_HOME, m.group(1))
            for m in re.finditer(
                r'href="([^"]*/about-fredericton/calendar-events/[^"]+)"',
                home_html,
                flags=re.IGNORECASE,
            )
            if 'calendar-events"' not in m.group(1)
            and not m.group(1).rstrip("/").endswith("/calendar-events")
        }
    )

    events: list[Event] = []
    for link in links:
        if not link.startswith(CITY_EVENT_LINK_PREFIX):
            continue
        try:
            html = fetch_url(link)
        except (HTTPError, URLError):
            continue

        title_match = re.search(
            r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL
        )
        title = (
            normalize_space(strip_tags(title_match.group(1)))
            if title_match
            else "Untitled event"
        )

        lines = text_lines_from_html(html)
        date_time_line = first_match(
            [re.compile(r"^[A-Za-z]+\s+\d{1,2}\s+\d{4}\s*-\s*.+$")],
            lines,
        )
        if not date_time_line:
            continue

        date, time = split_city_date_time(date_time_line)

        location = "Not listed"
        loc_match = re.search(
            r"Location:\s*(.+?)(Agenda\s*-|Find City Hall|Connect with the City)",
            strip_tags(html),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if loc_match:
            location = normalize_space(loc_match.group(1))

        cost = "Not listed"
        cost_line = first_match(COST_PATTERNS, lines)
        if cost_line:
            cost = cost_line

        events.append(
            Event(
                date=date,
                time=time,
                event_name=title,
                cost=cost,
                location=location,
                distance_from_fredericton=estimate_distance_from_fredericton(location),
                website=link,
                source="City of Fredericton",
            )
        )

    return events


def extract_eventbrite_links(html: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r'<a[^>]+href="(?P<href>(?:https?://(?:www\.)?eventbrite\.(?:ca|com)|/e/)[^"]*?/e/[^"\s]*tickets-[^"\s]*)"[^>]*>(?P<text>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    found: list[tuple[str, str]] = []
    for m in pattern.finditer(html):
        href = unescape(m.group("href"))
        if href.startswith("/e/"):
            href = urljoin("https://www.eventbrite.ca", href)
        title = normalize_space(strip_tags(m.group("text")))
        if title.lower().startswith("view "):
            title = title[5:].strip()
        if not title or looks_like_noise(title):
            continue
        found.append((title, href))

    dedup: dict[tuple[str, str], None] = {}
    for item in found:
        dedup[item] = None
    return list(dedup.keys())


def parse_eventbrite_events(list_url: str, source_name: str) -> list[Event]:
    try:
        html = fetch_url(list_url)
    except (HTTPError, URLError) as exc:
        print(f"Warning: could not fetch {source_name} listing: {exc}", file=sys.stderr)
        return []

    lines = text_lines_from_html(html)
    links = extract_eventbrite_links(html)

    events: list[Event] = []
    for title, href in links:
        idx = -1
        for i, line in enumerate(lines):
            if line == title or line == f"View {title}":
                idx = i
                break
        if idx == -1:
            continue

        window = lines[idx : idx + 25]
        date_time = first_match(DATE_TIME_PATTERNS, window)
        cost = first_match(COST_PATTERNS, window) or "Not listed"

        location = "Not listed"
        if date_time:
            try:
                dt_idx = window.index(date_time)
            except ValueError:
                dt_idx = 0

            for candidate in window[dt_idx + 1 :]:
                if candidate == cost:
                    break
                if looks_like_noise(candidate):
                    continue
                if any(p.search(candidate) for p in DATE_TIME_PATTERNS):
                    continue
                if any(p.search(candidate) for p in COST_PATTERNS):
                    break
                if len(candidate) > 3:
                    location = candidate
                    break

        date = "Not listed"
        time = "Not listed"
        if date_time:
            date, time = extract_date_from_eventbrite_line(date_time)

        events.append(
            Event(
                date=date,
                time=time,
                event_name=title,
                cost=cost,
                location=location,
                distance_from_fredericton=estimate_distance_from_fredericton(location),
                website=href,
                source=source_name,
            )
        )

    return events


def dedupe_events(events: list[Event]) -> list[Event]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Event] = []
    for event in events:
        key = (
            event.event_name.lower(),
            event.date.lower(),
            event.time.lower(),
            event.website.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def sort_events(events: list[Event]) -> list[Event]:
    reference_date = datetime.now().date()

    def score_date(event: Event) -> datetime:
        resolved = resolve_event_date(event, reference_date)
        if resolved is None:
            return datetime.max
        return datetime.combine(resolved, datetime.min.time())

    return sorted(events, key=lambda e: (score_date(e), e.event_name.lower()))


def render_table(events: list[Event]) -> str:
    headers = [
        "Date",
        "Time",
        "Event Name",
        "Cost",
        "Location",
        "Distance From Fredericton",
        "Website",
        "Source",
    ]
    rows = [
        [
            e.date,
            e.time,
            e.event_name,
            e.cost,
            e.location,
            e.distance_from_fredericton,
            e.website,
            e.source,
        ]
        for e in events
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(row: list[str]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    lines = [fmt_row(headers), sep]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


def write_csv(events: list[Event], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "time",
                "event_name",
                "cost",
                "location",
                "distance_from_fredericton",
                "website",
                "source",
            ],
        )
        writer.writeheader()
        for event in events:
            writer.writerow(asdict(event))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch public Fredericton event listings."
    )
    parser.add_argument(
        "--max-events", type=int, default=25, help="Maximum number of events to show."
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format for stdout.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional path to write CSV output.",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=365,
        help="Only include events happening within the next N days (default: 365).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reference_date = datetime.now().date()

    fredericton_events: list[Event] = []
    fredericton_events.extend(parse_city_events())
    fredericton_events.extend(
        parse_eventbrite_events(EVENTBRITE_LIST, "Eventbrite Fredericton")
    )
    fredericton_events = dedupe_events(fredericton_events)
    fredericton_events = filter_events_within_days(
        fredericton_events, args.days_ahead, reference_date
    )
    fredericton_events = sort_events(fredericton_events)

    other_nb_events = parse_eventbrite_events(
        EVENTBRITE_NB_LIST, "Eventbrite New Brunswick"
    )
    other_nb_events = [
        e
        for e in dedupe_events(other_nb_events)
        if "fredericton" not in e.location.lower()
        and "fredericton" not in e.event_name.lower()
    ]
    other_nb_events = filter_events_within_days(
        other_nb_events, args.days_ahead, reference_date
    )
    other_nb_events = sort_events(other_nb_events)

    if args.max_events > 0:
        fredericton_events = fredericton_events[: args.max_events]
        other_nb_events = other_nb_events[: args.max_events]

    all_events = fredericton_events + other_nb_events

    if args.format == "json":
        print(
            json.dumps(
                {
                    "fredericton_events": [asdict(e) for e in fredericton_events],
                    "other_new_brunswick_events": [asdict(e) for e in other_nb_events],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print("Fredericton Events")
        print("=" * len("Fredericton Events"))
        print(
            render_table(fredericton_events)
            if fredericton_events
            else "No events found."
        )
        print("\nOther Events in New Brunswick")
        print("=" * len("Other Events in New Brunswick"))
        print(render_table(other_nb_events) if other_nb_events else "No events found.")

    if args.csv:
        write_csv(all_events, args.csv)
        print(f"\nWrote CSV: {args.csv}")

    if not all_events:
        print("\nNo events found. The source pages may have changed.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
