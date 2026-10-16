"""Typed claims from official notice text in registered Atom/RSS feeds.

One adapter per raw format: the registered signal parser re-reads the preserved
body, and only sentences that name exactly one registered model and use one of
the fixed phrasings below become typed claims for classify_claim. Anything else
(other phrasings, retellings, family names, several models in one sentence)
stays with the generic signal rows as a candidate or gap. No model-specific code.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from scripts.market_collection import signals as signal_runs
from scripts.market_collection.signals.matching import models_in, normalize, sentences
from scripts.market_collection.signals.parsers import atom_entries, rss_entries
from scripts.market_collection.signals.registry import SignalSourceSpec

from .catalog import Catalog, authority

OFFICIAL = ("manufacturer_official", "channel_official")
# Fields the registered parser writes; a CSV row must equal a re-parsed row on all of them.
PARSED_FIELDS = ("entry_id", "entry_url", "entry_title", "published_at_utc", "locator", "model_label", "model_ids",
                 "model_match", "claim_kinds", "match_terms", "quote", "date_phrases")
PRICE_KINDS = {"discount", "free", "price_increase", "price_decrease", "discount_active", "condition"}

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(r"\b(" + _MONTHS + r")\s+(\d{1,2}),\s*(\d{4})\b")
RELEASE = re.compile(r"\b(?:is|are)\s+(?:now\s+)?available\s+today\b|\b(?:launch|releas)(?:es|ed|e|ing)?\b[^.]*\btoday\b", re.I)
# "available today" alone may be an existing model reaching a new tier, plan, or region; a first release
# also needs the maker to call it a new model, and no negation or narrowed scope in the same sentence.
NEW_MODEL = re.compile(r"\bnew(?:est)?\s+(?:\w+\s+)?model\b|\bfirst\s+release\b", re.I)
NEGATED = re.compile(r"\bnot\b|n't\b|\bno\s+longer\b|\bcancel|\bpostpon|\bdelay|\bwithdr[ae]w", re.I)
NARROW_SCOPE = re.compile(r"\bBatch\b|\bChatGPT\b|\bfree\s+tier\b|\bregions?\b|\b(?:Plus|Pro|Team|Enterprise)\s+(?:users|plans?)\b", re.I)
ROUTE_ADD = re.compile(r"\b(?:is|are)\s+now\s+available\s+on\b", re.I)
ROUTE_REMOVE = re.compile(r"\bwill\s+be\s+removed\s+from\b", re.I)
RETIRE = re.compile(r"\bwill\s+be\s+(?:retired|shut\s+down|deprecated)\b", re.I)
WHOLE_MODEL = re.compile(r"\bacross\s+all\s+(?:products|platforms|surfaces)\b|\beverywhere\b", re.I)
PART_ONLY = re.compile(r"\b(?:endpoints?|snapshots?|alias(?:es)?|versions?|in\s+ChatGPT)\b", re.I)
PERCENT_OFF = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%\s?off\b", re.I)
DISCOUNTED_TO = re.compile(r"\bdiscounted\s+to\s+\$([0-9]+(?:\.[0-9]+)?)\s+per\s+1M\b", re.I)
UNTIL = re.compile(r"\b(?:through|until)\s+(?=(?:" + _MONTHS + r")\s)", re.I)
CONDITIONS = (("cache_multiplier", re.compile(r"\bcache\b[^.]*\bmultiplier\b", re.I)),
              ("context_limit", re.compile(r"\b(?:max(?:imum)?\s+)?context\s+(?:window|length)\b", re.I)))
FROM_TO = re.compile(r"\bfrom\s+(\d[\d.,]*\s?(?:x|K|M)?)\s+to\s+(\d[\d.,]*\s?(?:x|K|M)?)(?!\w|\.\d)", re.I)
HEARSAY = re.compile(r"\baccording to\b|\breportedly\b|\brumou?r", re.I)


def iso_date(text: str) -> str | None:
    match = DATE.search(text)
    return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date().isoformat() if match else None


def reread(spec: dict, body: bytes, window_start: datetime) -> tuple[list[dict], list] | None:
    """(parser rows, in-window feed entries) from the registered parser, or None if the raw does not parse."""
    try:
        parsed = SignalSourceSpec(**spec)
        _, _, rows = signal_runs.parse_body(parsed, body, window_start)
        entries = [] if parsed.parser == "openrouter_endpoints" else (atom_entries(body) if parsed.parser == "atom" else rss_entries(body))
    except Exception:
        return None
    return rows, [entry for entry in entries if entry.published is None or entry.published >= window_start]


def row_key(row: dict) -> tuple:
    return tuple(row.get(field, "") for field in PARSED_FIELDS)


def ambiguous_row(row: dict, model_id: str) -> bool:
    """A price predicate whose quote also names another model, a family, or (title context) several models."""
    parts = [part for part in row["quote"].split(" / ") if part]
    for part in parts:
        named = models_in(part)
        if any(alias.match != "exact" or alias.model_ids != (model_id,) for alias in named):
            return True
    if all(not models_in(part) for part in parts):
        return len(models_in(normalize(row["entry_title"]))) > 1
    return False


def maker_route(catalog: Catalog, model_id: str) -> str:
    routes = {spec.route for spec in catalog.specs if spec.model_id == model_id and authority(model_id, spec.route) == "manufacturer_official"}
    return routes.pop() if len(routes) == 1 else ""


def entry_claims(entry, spec: dict, catalog: Catalog) -> list[dict]:
    """Typed claims from one official feed entry; each carries the sentence it came from."""
    # The feed's own publication timestamp, as a UTC date; never the observation date.
    published = entry.published.astimezone(timezone.utc).date().isoformat() if entry.published else None
    lines = [normalize(entry.title)] + sentences(normalize(entry.body))
    found = []
    for index, line in enumerate(lines):
        named = models_in(line)
        if not named or HEARSAY.search(line):
            continue
        ambiguous = len(named) > 1 or any(alias.match != "exact" for alias in named)
        for alias in named:
            if alias.match != "exact":
                continue
            model_id = alias.model_ids[0]
            info = catalog.signal_source(spec, model_id)
            if info["authority"] not in OFFICIAL:
                continue
            maker = info["authority"] == "manufacturer_official"
            route = maker_route(catalog, model_id) if maker else info["route"]
            base = {"model_id": model_id, "quote": line, "line": index, "info": info, "route": route}
            date = iso_date(line)
            if not ambiguous:
                if (maker and RELEASE.search(line) and NEW_MODEL.search(line)
                        and not NEGATED.search(line) and not NARROW_SCOPE.search(line)):
                    found.append(dict(base, kind="availability", maker_first_release_date=published, route_added_date=published))
                elif not maker and ROUTE_ADD.search(line) and route:
                    found.append(dict(base, kind="availability", route_added_date=published))
                elif not maker and ROUTE_REMOVE.search(line) and route and date:
                    found.append(dict(base, kind="route_removal", effective_from=date))
                elif maker and RETIRE.search(line) and date:
                    if PART_ONLY.search(line):
                        found.append(dict(base, kind="endpoint_deprecation", route="", effective_from=date))
                    elif WHOLE_MODEL.search(line):
                        found.append(dict(base, kind="endpoint_deprecation", route="", model_wide=True, effective_from=date))
            percent, price, until = PERCENT_OFF.search(line), DISCOUNTED_TO.search(line), UNTIL.search(line)
            if (percent or price) and until:
                found.append(dict(base, kind="discount", end_date=iso_date(line[until.start():]), ambiguous_model=ambiguous,
                                  discount_amount=price.group(1) if price else None))
            change = FROM_TO.search(line)
            for key, pattern in CONDITIONS:
                if pattern.search(line) and change:
                    found.append(dict(base, kind="condition", condition_key=key, ambiguous_model=ambiguous,
                                      before={"value": change.group(1).strip()}, after={"value": change.group(2).strip()}))
    return found
