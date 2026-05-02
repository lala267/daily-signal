#!/usr/bin/env python3
"""
Daily Signal: collect recent YouTube updates and write an AI-ready Markdown brief.

The first version intentionally uses only Python's standard library so it can run
from cron, launchd, or GitHub Actions without dependency friction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import ssl
import sqlite3
import time
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"
FALLBACK_CERT_FILES = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)


@dataclass(frozen=True)
class Source:
    name: str
    kind: str
    value: str
    limit: int


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    url: str
    channel: str
    published: dt.datetime
    summary: str
    source: str


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: str) -> dt.datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def slug_date(day: dt.date) -> str:
    return day.isoformat()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_sources(config: dict[str, Any]) -> list[Source]:
    sources = []
    for raw in config.get("youtube_sources", []):
        kind = raw.get("type")
        value = raw.get("value")
        name = raw.get("name") or value
        if kind not in {"channel_id", "playlist_id"}:
            raise SystemExit(f"Unsupported YouTube source type: {kind!r}")
        if not value:
            raise SystemExit(f"YouTube source is missing value: {raw}")
        sources.append(Source(name=name, kind=kind, value=value, limit=int(raw.get("limit", 8))))
    return sources


def build_feed_url(source: Source) -> str:
    query_key = "channel_id" if source.kind == "channel_id" else "playlist_id"
    return f"{YOUTUBE_FEED_URL}?{urllib.parse.urlencode({query_key: source.value})}"


def ssl_context() -> ssl.SSLContext:
    if os.environ.get("DAILY_SIGNAL_INSECURE_SSL") == "1":
        return ssl._create_unverified_context()

    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    default_paths = ssl.get_default_verify_paths()
    if default_paths.cafile and Path(default_paths.cafile).exists():
        return ssl.create_default_context(cafile=default_paths.cafile)

    for cert_file in FALLBACK_CERT_FILES:
        if Path(cert_file).exists():
            return ssl.create_default_context(cafile=cert_file)

    return ssl.create_default_context()


def http_get(url: str, timeout: int = 30, retries: int = 2) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DailySignal/0.1 (+https://youtube.com)",
            "Accept": "application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1 + attempt)
    assert last_error is not None
    raise last_error


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_feed(payload: bytes, source: Source, since: dt.datetime) -> list[Item]:
    root = ET.fromstring(payload)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    channel = clean_text(root.findtext("atom:title", namespaces=ns)) or source.name
    items: list[Item] = []

    for entry in root.findall("atom:entry", ns):
        published_raw = entry.findtext("atom:published", namespaces=ns)
        if not published_raw:
            continue
        published = parse_time(published_raw)
        if published < since:
            continue

        video_id = entry.findtext("yt:videoId", namespaces=ns) or entry.findtext("atom:id", namespaces=ns)
        title = clean_text(entry.findtext("atom:title", namespaces=ns))
        link_node = entry.find("atom:link", ns)
        url = link_node.attrib.get("href", "") if link_node is not None else ""
        media_group = entry.find("media:group", ns)
        description = ""
        if media_group is not None:
            description = clean_text(media_group.findtext("media:description", namespaces=ns))

        if video_id and title and url:
            items.append(
                Item(
                    id=video_id,
                    title=title,
                    url=url,
                    channel=channel,
                    published=published,
                    summary=description,
                    source=source.name,
                )
            )

    return sorted(items, key=lambda item: item.published, reverse=True)[: source.limit]


def fetch_items(sources: list[Source], since: dt.datetime) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    for source in sources:
        url = build_feed_url(source)
        try:
            payload = http_get(url)
            items.extend(parse_feed(payload, source, since))
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            errors.append(f"{source.name}: {exc}")

    deduped: dict[str, Item] = {}
    for item in items:
        deduped[item.id] = item
    return sorted(deduped.values(), key=lambda item: item.published, reverse=True), errors


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_items (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        )
        """
    )
    return conn


def filter_seen(conn: sqlite3.Connection, items: list[Item], include_seen: bool) -> list[Item]:
    if include_seen:
        return items
    fresh = []
    for item in items:
        row = conn.execute("SELECT 1 FROM seen_items WHERE id = ?", (item.id,)).fetchone()
        if row is None:
            fresh.append(item)
    return fresh


def mark_seen(conn: sqlite3.Connection, items: list[Item]) -> None:
    now = utc_now().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_items (id, url, title, first_seen_at) VALUES (?, ?, ?, ?)",
        [(item.id, item.url, item.title, now) for item in items],
    )
    conn.commit()


def compact_item_for_ai(item: Item) -> dict[str, str]:
    return {
        "title": item.title,
        "channel": item.channel,
        "published": item.published.isoformat(),
        "url": item.url,
        "description": item.summary[:1200],
    }


def call_openai(items: list[Item], config: dict[str, Any], brief_date: dt.date) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None

    model = config.get("openai_model") or DEFAULT_MODEL
    focus = config.get("focus", "global technology, AI, business, science, and major world events")
    max_events = int(config.get("max_events", 10))
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You write concise Chinese intelligence briefs from YouTube metadata. "
                    "Group duplicate topics, keep uncertainty visible, and do not invent facts. "
                    "Return Markdown only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "date": brief_date.isoformat(),
                        "focus": focus,
                        "max_events": max_events,
                        "items": [compact_item_for_ai(item) for item in items],
                        "required_format": [
                            "## 今日大事",
                            "For each event: title, why it matters, source links, confidence, keywords.",
                            "## 值得继续追踪",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90, context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return f"> AI 解析失败：`{exc}`\n"

    text_parts: list[str] = []
    for output in data.get("output", []):
        for content in output.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                text_parts.append(content.get("text", ""))
    return "\n\n".join(part.strip() for part in text_parts if part.strip()) or None


def fallback_brief(items: list[Item], config: dict[str, Any]) -> str:
    max_events = int(config.get("max_events", 10))
    if not items:
        return "## 今日大事\n\n今天没有抓到新的 YouTube 内容。\n"

    lines = ["## 今日大事", ""]
    for index, item in enumerate(items[:max_events], start=1):
        description = item.summary or "暂无简介。建议打开来源视频确认上下文。"
        description = textwrap.shorten(description, width=260, placeholder="...")
        published = item.published.astimezone().strftime("%Y-%m-%d %H:%M")
        lines.extend(
            [
                f"### {index}. {item.title}",
                "",
                f"- 来源：[{item.channel}]({item.url})",
                f"- 发布时间：{published}",
                f"- 摘要：{description}",
                "- 重要性：待 AI 解析；当前仅基于标题和简介收录。",
                "",
            ]
        )
    lines.extend(["## 值得继续追踪", "", "- 设置 `OPENAI_API_KEY` 后可自动合并同类话题、判断重要性并生成中文分析。", ""])
    return "\n".join(lines)


def render_markdown(
    items: list[Item],
    ai_brief: str | None,
    errors: list[str],
    config: dict[str, Any],
    brief_date: dt.date,
    since: dt.datetime,
) -> str:
    title = config.get("title", "今日信号")
    generated = utc_now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# {title} - {slug_date(brief_date)}",
        "",
        f"- 生成时间：{generated}",
        f"- 时间窗口：{since.astimezone().strftime('%Y-%m-%d %H:%M')} 至今",
        f"- 收录视频：{len(items)} 条",
        "",
    ]
    lines.append(ai_brief if ai_brief is not None else fallback_brief(items, config))

    lines.extend(["", "## 原始来源", ""])
    if items:
        for item in items:
            published = item.published.astimezone().strftime("%Y-%m-%d %H:%M")
            lines.append(f"- [{item.title}]({item.url}) - {item.channel} - {published}")
    else:
        lines.append("- 无")

    if errors:
        lines.extend(["", "## 抓取警告", ""])
        for error in errors:
            lines.append(f"- {error}")

    return "\n".join(lines).rstrip() + "\n"


def write_markdown(output_dir: Path, brief_date: dt.date, markdown: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"daily-{slug_date(brief_date)}.md"
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a YouTube-powered Markdown daily brief.")
    parser.add_argument("--config", default="config.example.json", help="Path to JSON config.")
    parser.add_argument("--output-dir", default="briefs", help="Directory for generated Markdown files.")
    parser.add_argument("--db", default=".daily-signal/seen.sqlite3", help="SQLite cache for seen videos.")
    parser.add_argument("--lookback-hours", type=int, default=None, help="Only include videos newer than this window.")
    parser.add_argument("--date", default=None, help="Brief date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--include-seen", action="store_true", help="Include items already processed before.")
    parser.add_argument("--no-ai", action="store_true", help="Skip OpenAI even when OPENAI_API_KEY is set.")
    parser.add_argument("--dry-run", action="store_true", help="Print Markdown instead of writing a file or updating cache.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    sources = parse_sources(config)
    if not sources:
        raise SystemExit("No youtube_sources configured.")

    brief_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    lookback_hours = args.lookback_hours or int(config.get("lookback_hours", 24))
    since = utc_now() - dt.timedelta(hours=lookback_hours)

    conn = init_db(Path(args.db))
    fetched_items, errors = fetch_items(sources, since)
    items = filter_seen(conn, fetched_items, args.include_seen)
    ai_brief = None if args.no_ai else call_openai(items, config, brief_date)
    markdown = render_markdown(items, ai_brief, errors, config, brief_date, since)

    if args.dry_run:
        print(markdown)
    else:
        output_path = write_markdown(Path(args.output_dir), brief_date, markdown)
        mark_seen(conn, items)
        print(f"Wrote {output_path}")
        print(f"Collected {len(items)} new item(s); fetched {len(fetched_items)} recent item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
