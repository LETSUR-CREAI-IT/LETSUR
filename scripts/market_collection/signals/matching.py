"""Model aliases, claim terms, and quote extraction for external price signals.

A signal only says that a public text mentions a registered model together with
a price-related claim. It never proves a price change; people confirm it against
the origin source (see docs/external-signals.md).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from scripts.market_collection.models import (BROKER_API_MODELS, BROKER_CATALOG_MODELS, FLARE_MODEL, GOOGLE_BROKER_MODEL,
                                              GOOGLE_LLM_MODEL, SEEDANCE_MODEL, SUNBURST_MODEL)
from scripts.market_collection.sources import _targets

MAX_QUOTES = 2
MAX_QUOTE_CHARS = 400
MAX_DATE_PHRASES = 5


@dataclass(frozen=True)
class ModelAlias:
    label: str
    model_ids: tuple[str, ...]
    match: str  # "exact" or "family"
    pattern: re.Pattern


def _flex(text: str) -> str:
    """Let '-' and spaces in a display name match either separator."""
    tokens = [re.escape(token) for token in re.split(r"[-\s]+", text.strip()) if token]
    return r"[-\s]*".join(tokens)


def _compile(texts: list[str]) -> re.Pattern:
    alternatives = sorted({_flex(text) for text in texts if text}, key=len, reverse=True)
    # Do not match inside a longer identifier (GPT-6 inside GPT-6.1, sol inside solar).
    return re.compile(r"(?<![\w.])(?:" + "|".join(alternatives) + r")(?![\w]|\.\d)", re.IGNORECASE)


def _exact_aliases() -> list[ModelAlias]:
    names: dict[str, list[str]] = {}
    for model, (title, _, slug, vercel_title, openrouter_id) in _targets().items():
        names[model] = [title, vercel_title, slug, openrouter_id, model]
    names[GOOGLE_LLM_MODEL] = ["Gemini 3.8 Flash", GOOGLE_LLM_MODEL, GOOGLE_BROKER_MODEL]
    for model, title, _, openrouter_id, vercel_id in BROKER_CATALOG_MODELS:
        names[model] = [title, openrouter_id, vercel_id, model]
    for model, title, openrouter_id, vercel_id in BROKER_API_MODELS:
        names[model] = [title, openrouter_id, vercel_id, model]  # _compile skips a missing (None) id
    names[SUNBURST_MODEL] = ["GPT Image 2.5 Sunburst", SUNBURST_MODEL]
    names[FLARE_MODEL] = ["GPT Image 2.5 Flare", FLARE_MODEL]
    names[SEEDANCE_MODEL] = ["Seedance 2.5", SEEDANCE_MODEL]
    return [ModelAlias(names[model][0], (model,), "exact", _compile(names[model])) for model in names]


def _family_aliases(exact: list[ModelAlias]) -> list[ModelAlias]:
    families: dict[str, list[str]] = {}
    for alias in exact:
        if re.fullmatch(r"GPT-[\d.]+ \w+", alias.label):
            families.setdefault(alias.label.rsplit(" ", 1)[0], []).extend(alias.model_ids)
    families["GPT Image 2.5"] = [SUNBURST_MODEL, FLARE_MODEL]
    return [ModelAlias(label, tuple(models), "family", _compile([label])) for label, models in families.items()]


EXACT_ALIASES = _exact_aliases()
FAMILY_ALIASES = _family_aliases(EXACT_ALIASES)

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")

# Claim kinds. "deprecation" is listed because a model retirement changes what
# can be bought even when no price moves.
CLAIM_TERMS = {
    "discount": (r"\b\d{1,3}(?:\.\d+)?\s?%\s?off\b", r"\bdiscount(?:s|ed)?\b", r"\bpromo(?:tion|tional)?s?\b", r"\bhalf[- ]price\b"),
    "free": (r"\bfree\s+(?:through|until|for|of charge)\b", r"\bat no (?:cost|charge)\b", r"\bno charge\b"),
    "price_increase": (r"\bprice\s+(?:increase|hike|rise)s?\b", r"\bprice up\b", r"\b(?:raise[sd]?|raising|increase[sd]?|increasing)\s+(?:the\s+)?(?:list\s+)?pric(?:e|es|ing)\b", r"\bmore expensive\b"),
    "price_decrease": (r"\bprice\s+(?:cut|drop|reduction|decrease)s?\b", r"\bprice down\b", r"\b(?:lowered|lowers|lowering|reduced|reduces|reducing|cuts?|cutting)\s+(?:the\s+|its\s+|their\s+)?(?:list\s+)?pric(?:e|es|ing)\b", r"\bnow cheaper\b"),
    "deprecation": (r"\bdeprecat\w*", r"\bsunset(?:s|ting)?\b", r"\bretir(?:e|es|ed|ing|ement)\b", r"\bend[- ]of[- ]life\b"),
}
_CLAIMS = {kind: [re.compile(term, re.IGNORECASE) for term in terms] for kind, terms in CLAIM_TERMS.items()}

DATE_PHRASES = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:at least\s+)?(?:through|until|till|thru|ends?|ending|expires?|expiring|starting|effective|begins?|beginning|from|before|by)\s+(?:on\s+)?" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?",
    r"\b(?:at least\s+)?(?:through|until|till)\s+\d{4}-\d{2}-\d{2}\b",
    r"\bfor the next\s+(?:\d+\s+)?(?:day|week|month)s?\b",
    r"\b(?:in|for|from|starting(?:\s+in)?)\s+" + _MONTH + r"(?:\s+\d{4})?\b(?!\.?\s+\d)",
    r"\blimited[- ]time\b",
)]

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_HYPHENS = re.compile("[‐‑‒–—−]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“])")


def normalize(text: str) -> str:
    """Plain text from feed HTML, with Unicode hyphens folded to '-'."""
    text = html.unescape(_TAGS.sub(" ", text or ""))
    text = _HYPHENS.sub("-", text.replace(" ", " "))
    return _SPACE.sub(" ", text).strip()


def sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def models_in(sentence: str) -> list[ModelAlias]:
    """Exact model mentions first; a family counts only where no member model is named."""
    found: list[ModelAlias] = []
    covered: list[tuple[int, int]] = []
    for alias in EXACT_ALIASES:
        spans = [match.span() for match in alias.pattern.finditer(sentence)]
        if spans:
            found.append(alias)
            covered.extend(spans)
    named = {model for alias in found for model in alias.model_ids}
    for alias in FAMILY_ALIASES:
        # "GPT-5.6 Sol, the flagship of the GPT-5.6 series" is about Sol, not the whole family.
        if named & set(alias.model_ids):
            continue
        for match in alias.pattern.finditer(sentence):
            start, end = match.span()
            if not any(start < other_end and other_start < end for other_start, other_end in covered):
                found.append(alias)
                break
    return found


def claims_in(sentence: str) -> tuple[list[str], list[str]]:
    kinds: list[str] = []
    terms: list[str] = []
    for kind, patterns in _CLAIMS.items():
        for pattern in patterns:
            match = pattern.search(sentence)
            if match:
                if kind not in kinds:
                    kinds.append(kind)
                terms.append(match.group(0))
    return kinds, terms


def date_phrases(texts: list[str]) -> list[str]:
    phrases: list[str] = []
    for text in texts:
        for pattern in DATE_PHRASES:
            for match in pattern.finditer(text):
                phrase = match.group(0).strip()
                if phrase.lower() not in (seen.lower() for seen in phrases):
                    phrases.append(phrase)
    return phrases[:MAX_DATE_PHRASES]


def _clip(text: str) -> str:
    return text if len(text) <= MAX_QUOTE_CHARS else text[:MAX_QUOTE_CHARS - 1].rstrip() + "…"


def entry_signals(title: str, body: str) -> list[dict]:
    """One candidate per mentioned model (or family) whose sentence carries a claim.

    A body sentence that names no model is attributed to the models named in the
    title, because changelog posts usually name the model only once.
    """
    title = normalize(title)
    lines = [title] + sentences(normalize(body))
    title_models = models_in(title)
    grouped: dict[tuple[str, ...], dict] = {}
    for index, line in enumerate(lines):
        kinds, terms = claims_in(line)
        if not kinds:
            continue
        named = models_in(line)
        for alias in named or (title_models if index else []):
            item = grouped.setdefault((alias.label,) + alias.model_ids, {"alias": alias, "kinds": [], "terms": [], "quotes": [], "named": []})
            for kind in kinds:
                if kind not in item["kinds"]:
                    item["kinds"].append(kind)
            item["terms"].extend(term for term in terms if term.lower() not in (seen.lower() for seen in item["terms"]))
            item["quotes"].append(line)
            item["named"].append(bool(named))
    results = []
    for item in grouped.values():
        # Prefer sentences that name the model over title-context sentences.
        ranked = [quote for _, quote in sorted(zip(item["named"], item["quotes"]), key=lambda pair: not pair[0])]
        quotes = list(dict.fromkeys(ranked))[:MAX_QUOTES]
        alias = item["alias"]
        results.append({
            "model_label": alias.label,
            "model_ids": ";".join(alias.model_ids),
            "model_match": alias.match,
            "claim_kinds": ";".join(item["kinds"]),
            "match_terms": ";".join(item["terms"]),
            "quote": " / ".join(_clip(quote) for quote in quotes),
            "date_phrases": ";".join(date_phrases(quotes)),
        })
    return results
