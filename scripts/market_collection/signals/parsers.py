"""Read public Atom/RSS feeds and the OpenRouter endpoints API into signal candidates."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

from .matching import entry_signals

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
_DECLARATION = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


class SignalParseError(ValueError):
    """The preserved body is not the expected feed or API shape."""


@dataclass(frozen=True)
class Entry:
    entry_id: str
    url: str
    title: str
    published: datetime | None
    body: str
    locator: str


def utc_text(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else ""


def _iso(text: str | None) -> datetime | None:
    if not text:
        return None
    value = text.strip().replace("Z", "+00:00")
    # Python 3.9 fromisoformat rejects 1-2 or 4-5 digit fractions such as .000 + offset variants.
    value = re.sub(r"(\.\d{1,6})\d*(?=[+-]\d\d:\d\d$|$)", lambda m: (m.group(1) + "000000")[:7], value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _rfc822(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _xml(body: bytes) -> ET.Element:
    if _DECLARATION.search(body):
        raise SignalParseError("feed declares a DOCTYPE or ENTITY")
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise SignalParseError(f"feed is not XML: {exc}") from exc


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    if element.get("type") == "xhtml" or len(element):
        return " ".join(element.itertext())
    return element.text or ""


def atom_entries(body: bytes) -> list[Entry]:
    root = _xml(body)
    if root.tag != ATOM + "feed":
        raise SignalParseError("not an Atom feed")
    entries = []
    for index, item in enumerate(root.findall(ATOM + "entry")):
        links = [link.get("href", "") for link in item.findall(ATOM + "link") if link.get("rel") in (None, "alternate")]
        entry_id = (item.findtext(ATOM + "id") or (links[0] if links else "")).strip()
        published = _iso(item.findtext(ATOM + "published")) or _iso(item.findtext(ATOM + "updated"))
        content = item.find(ATOM + "content")
        body_text = _text(content) if content is not None else _text(item.find(ATOM + "summary"))
        entries.append(Entry(entry_id or f"entry[{index}]", links[0] if links else "", _text(item.find(ATOM + "title")), published, body_text, f"feed/entry[{index}]"))
    return entries


def rss_entries(body: bytes) -> list[Entry]:
    root = _xml(body)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SignalParseError("not an RSS 2.0 feed")
    entries = []
    for index, item in enumerate(channel.findall("item")):
        link = (item.findtext("link") or "").strip()
        entry_id = (item.findtext("guid") or link).strip()
        body_text = item.findtext(CONTENT) or item.findtext("description") or ""
        entries.append(Entry(entry_id or f"item[{index}]", link, item.findtext("title") or "", _rfc822(item.findtext("pubDate")), body_text, f"rss/channel/item[{index}]"))
    return entries


def feed_signals(entries: list[Entry], window_start: datetime) -> tuple[int, list[dict]]:
    """Signals from entries published on/after window_start; undated entries are kept."""
    in_window = [entry for entry in entries if entry.published is None or entry.published >= window_start]
    rows = []
    for entry in in_window:
        for candidate in entry_signals(entry.title, entry.body):
            rows.append({"entry_id": entry.entry_id, "entry_url": entry.url, "entry_title": " ".join(entry.title.split()),
                         "published_at_utc": utc_text(entry.published), "locator": entry.locator, **candidate})
    return len(in_window), rows


def _rate(value: object) -> str:
    """OpenRouter quotes USD per token; show USD per 1M tokens like the price CSV."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return "?"
    return format((amount * 1000000).normalize(), "f")


def openrouter_endpoint_signals(body: bytes, model_id: str, model_label: str) -> tuple[int, list[dict]]:
    """One signal per endpoint whose pricing.discount is above zero.

    The tag (for example openai/flex) is kept: Flex/Batch/regional endpoints are
    different processing conditions, not discounts, and must stay distinguishable.
    """
    try:
        data = json.loads(body)["data"]
        endpoints = data["endpoints"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SignalParseError("not an OpenRouter endpoints response") from exc
    if not isinstance(endpoints, list):
        raise SignalParseError("endpoints is not a list")
    rows = []
    for index, endpoint in enumerate(endpoints):
        pricing = endpoint.get("pricing") or {}
        try:
            discount = Decimal(str(pricing.get("discount", 0) or 0))
        except InvalidOperation as exc:
            raise SignalParseError(f"invalid discount at endpoint {index}") from exc
        if discount <= 0:
            continue
        provider = str(endpoint.get("provider_name", "?"))
        tag = str(endpoint.get("tag", ""))
        quote = (f"{provider} ({tag}): pricing.discount={format(discount.normalize(), 'f')}; "
                 f"input {_rate(pricing.get('prompt'))} / output {_rate(pricing.get('completion'))} USD per 1M tokens (discount applied)")
        rows.append({"entry_id": f"{data.get('id', model_id)}#{tag or provider}", "entry_url": "", "entry_title": str(data.get("name", "")),
                     "published_at_utc": "", "locator": f"data.endpoints[{index}].pricing.discount",
                     "model_label": model_label, "model_ids": model_id, "model_match": "exact", "claim_kinds": "discount_active",
                     "match_terms": "pricing.discount", "quote": quote, "date_phrases": ""})
    expiration = data.get("expiration_date")
    if expiration:
        rows.append({"entry_id": f"{data.get('id', model_id)}#expiration_date", "entry_url": "", "entry_title": str(data.get("name", "")),
                     "published_at_utc": "", "locator": "data.expiration_date", "model_label": model_label, "model_ids": model_id,
                     "model_match": "exact", "claim_kinds": "deprecation", "match_terms": "expiration_date",
                     "quote": f"expiration_date={expiration}", "date_phrases": str(expiration)})
    return len(endpoints), rows
