"""Small shared helpers for scoped video metadata and quote rows."""
import json
import re
from decimal import Decimal
from scripts.market_collection.core import ADAPTER_PRICE_FIELDS
from .common import llm_condition


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("video rate is not numeric")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("video rate must be finite and nonnegative")
    # Preserve the quoted decimal representation, including source precision.
    return str(value)


def price(spec, provider, upstream, value, unit, group, locator, *, version="3.1", reason=""):
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    row.update(provider_model_id=provider, model_version=version, upstream_provider=upstream, task=spec.task,
               billing_item="generated_video", price_kind="published_rate", amount=amount(value) if value is not None else "",
               currency="USD" if value is not None else "", unit=unit if value is not None else "",
               condition_set_id=group, locator=locator, evidence_status="direct", unknown_reason=reason)
    return row


def conditions(group, locator, **values):
    return [llm_condition(group, key, str(value), locator) for key, value in values.items()]


def flight_payloads(body):
    """Decode actual Next.js Flight string arguments; do not globally unescape HTML."""
    decoder = json.JSONDecoder()
    payloads = []
    for match in re.finditer(r"self\.__next_f\.push\(", body):
        try:
            values, _ = decoder.raw_decode(body, match.end())
        except ValueError:
            continue
        if isinstance(values, list) and len(values) == 2 and isinstance(values[1], str):
            payloads.append(values[1])
    # Compact retained fixtures can provide their unescaped object directly.
    return payloads or [body]


def objects_with_key(payload, key):
    """Decode JSON objects that have the requested *direct* field."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", payload):
        try:
            obj, _ = decoder.raw_decode(payload, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and key in obj:
            yield obj
