"""Fixed-page Azure retail token meters of the GPT-4o, GPT-4.1, o-series and GPT-5.x models LETSUR serves through Azure
OpenAI: Global Standard, short context, koreacentral; never follow catalog pagination."""
import json
from decimal import Decimal
from .common import llm_condition, llm_price

PARSERS = {"azure_models_retail"}
OPENAI, REASONING, GPT5 = "Azure OpenAI", "Azure OpenAI Reasoning", "Azure OpenAI GPT5"
# meterName after the skuName and the factor to a 1M-token rate, per unitOfMeasure: the older meters are priced per 1K tokens.
UNITS = {"1K": (" Tokens", 1000), "1M": (" 1M Tokens", 1)}
# model: (productName, unitOfMeasure, Azure model version a version-named meter bills, context band, {item: exact skuName}).
# Azure spells the models unevenly (1120/0718/1217/0416/0131, glbl/Glbl/Gl, Inp/Inpt/input, cached/cchd/ccchd/cd), so names
# are listed, never matched by pattern. Items are the ones OpenAI direct lists: o3-pro has no cached input there. gpt-4o
# reads version 2024-11-20, the only one Azure still opens to new deployments (10/28); 2024-08-06 has the same rates.
# gpt-5.4 has the one Azure version 2026-03-05, so its undated meters also price gpt-5.4-2026-03-05. A context band is
# stated only where Azure has a long-context meter (LongCo/longco, not read). Batch, Data Zone (DZ/Dz/DataZone), priority
# (pp/PP), Flex, fine-tuned, chat, pro and other models' meters are not read.
METERS = {
    "gpt-5": (GPT5, "1M", "", "", {
        "input": "GPT 5 Inpt Glbl", "cache_read": "GPT 5 cchd Inpt Glbl", "output": "GPT 5 outpt Glbl"}),
    "gpt-5-mini": (GPT5, "1M", "", "", {
        "input": "GPT 5 Mini Inpt Glbl", "cache_read": "GPT 5 Mini cchd Inpt Glbl", "output": "GPT 5 Mini outpt Glbl"}),
    "gpt-5-nano": (GPT5, "1M", "", "", {
        "input": "GPT 5 Nano Inpt Glbl", "cache_read": "GPT 5 Nano cchd Inpt Glbl", "output": "GPT 5 Nano outpt Glbl"}),
    "gpt-5-codex": (GPT5, "1K", "", "", {
        "input": "gpt-5-codex-inp-glbl", "cache_read": "gpt-5-codex-ccchd-inp-glbl", "output": "gpt-5-codex-out-glbl"}),
    "gpt-5.1": (GPT5, "1M", "", "", {
        "input": "GPT 5.1 inp Gl", "cache_read": "GPT 5.1 cd inp Gl", "output": "GPT 5.1 opt Gl"}),
    "gpt-5.1-codex": (GPT5, "1M", "", "", {
        "input": "5.1 codex inp Gl", "cache_read": "5.1 codex cd inp Gl", "output": "5.1 codex opt Gl"}),
    "gpt-5.1-codex-mini": (GPT5, "1M", "", "", {
        "input": "5.1 codex mini inp Gl", "cache_read": "5.1 codex mini cd inp Gl", "output": "5.1 codex mini opt Gl"}),
    "gpt-5.2": (GPT5, "1M", "", "", {
        "input": "GPT 5.2 inp Gl", "cache_read": "GPT 5.2 cd inp Gl", "output": "GPT 5.2 opt Gl"}),
    "gpt-5.4": (GPT5, "1M", "", "short context (meter without longco)", {
        "input": "5.4 inp Gl", "cache_read": "5.4 cd inp Gl", "output": "5.4 opt Gl"}),
    "gpt-5.4-2026-03-05": (GPT5, "1M", "", "short context (meter without longco)", {
        "input": "5.4 inp Gl", "cache_read": "5.4 cd inp Gl", "output": "5.4 opt Gl"}),
    "gpt-5.4-mini": (GPT5, "1M", "", "", {
        "input": "5.4 mini Inp Gl", "cache_read": "5.4 mini cd Inp Gl", "output": "5.4 mini Opt Gl"}),
    "gpt-5.4-nano": (GPT5, "1M", "", "", {
        "input": "5.4 nano Inp Gl", "cache_read": "5.4 nano cd Inp Gl", "output": "5.4 nano Opt Gl"}),
    "gpt-5.5": (GPT5, "1M", "", "short context (ShortCo)", {
        "input": "5.5 ShortCo inp Gl", "cache_read": "5.5 ShortCo cd inp Gl", "output": "5.5 ShortCo opt Gl"}),
    "gpt-4o": (OPENAI, "1K", "2024-11-20", "", {
        "input": "gpt 4o 1120 Inp glbl", "cache_read": "gpt 4o 1120 cached Inp glbl", "output": "gpt 4o 1120 Outp glbl"}),
    "gpt-4o-mini": (OPENAI, "1K", "2024-07-18", "", {
        "input": "gpt-4o-mini-0718-Inp-glbl", "cache_read": "gpt 4o mini 0718 cached Inp glbl", "output": "gpt-4o-mini-0718-Outp-glbl"}),
    "gpt-4.1": (OPENAI, "1K", "", "", {
        "input": "gpt 4.1 Inp glbl", "cache_read": "gpt 4.1 cached Inp glbl", "output": "gpt 4.1 Outp glbl"}),
    "gpt-4.1-mini": (OPENAI, "1K", "", "", {
        "input": "gpt 4.1 mini Inp glbl", "cache_read": "gpt 4.1 mini cached Inp glbl", "output": "gpt 4.1 mini Outp glbl"}),
    "gpt-4.1-nano": (OPENAI, "1K", "", "", {
        "input": "gpt 4.1 nano Inp glbl", "cache_read": "gpt 4.1 nano cached Inp glbl", "output": "gpt 4.1 nano Outp glbl"}),
    "o1": (OPENAI, "1K", "2024-12-17", "", {
        "input": "o1 1217 Inp glbl", "cache_read": "o1 1217 cached Inp glbl", "output": "o1 1217 Outp glbl"}),
    "o3": (OPENAI, "1K", "2025-04-16", "", {
        "input": "o3 0416 Inp glbl", "cache_read": "o3 0416 cached Inp glbl", "output": "o3 0416 Outp glbl"}),
    "o3-mini": (OPENAI, "1K", "2025-01-31", "", {
        "input": "o3 mini 0131 input glbl", "cache_read": "o3 mini 0131 cached input glbl", "output": "o3 mini 0131 output glbl"}),
    "o3-pro": (OPENAI, "1K", "", "", {
        "input": "o3-pro Inp glbl", "output": "o3-pro Outp glbl"}),
    "o4-mini": (REASONING, "1K", "2025-04-16", "", {
        "input": "o4-mini 0416 Inp glbl", "cache_read": "o4-mini 0416 cached Inp glbl", "output": "o4-mini 0416 Outp glbl"}),
}


def parse_source(spec, body, source_id):
    data = json.loads(body, parse_float=Decimal)  # Decimal keeps the quoted rate exact through the 1K-to-1M factor
    if data.get("BillingCurrency") != "USD" or "NextPageLink" not in data or data["NextPageLink"] not in (None, ""):
        raise ValueError("Azure currency or fixed-page completeness invalid")
    product, unit, version, band, skus = METERS[spec.model_id]
    suffix, factor = UNITS[unit]
    item_of = {sku: item for item, sku in skus.items()}
    found, meters = {}, set()
    for i, row in enumerate(data["Items"]):
        sku = row.get("skuName", "")
        name = row.get("meterName", "")
        if sku not in item_of and not any(name.removesuffix(end) in item_of for end, _ in UNITS.values()):
            continue
        if row.get("unitOfMeasure") != unit or name != sku + suffix:
            raise ValueError("Azure meter unit or exact meter name invalid")
        if any(row.get(k) != v for k, v in {
            "currencyCode": "USD", "armRegionName": "koreacentral", "type": "Consumption",
            "productName": product, "serviceName": "Foundry Models",
        }.items()) or row.get("tierMinimumUnits") != 0:
            raise ValueError("Azure exact meter identity, region, or type invalid")
        price, meter = row.get("retailPrice"), row.get("meterId")
        if type(price) not in (int, Decimal) or not Decimal(price).is_finite() or price < 0:
            raise ValueError("Azure retailPrice invalid")
        if not meter or sku in found or meter in meters:
            raise ValueError("Azure duplicate or missing meter")
        found[sku] = (i, row); meters.add(meter)
    if set(found) != set(item_of):
        raise ValueError("Azure exact model meter set incomplete")
    prices, conditions = [], []
    for item, sku in skus.items():
        i, row = found[sku]
        locator = (f"Items/{i}; meterId={row['meterId']}; meterName={row['meterName']}; unitOfMeasure={unit}; "
                   f"retailPrice={row['retailPrice']}")
        group = f"{spec.source_key}_{item}"
        prices.append(llm_price(provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate",
                                amount=format((Decimal(row["retailPrice"]) * factor).normalize(), "f"), unit="1M tokens",
                                set_id=group, locator=locator, effective_from=row["effectiveStartDate"]))
        stated = [("service_tier", "standard"), ("meter_scope", "Global (Gl)"), ("context_band", band), ("model_version", version),
                  ("price_basis", "Azure retailPrice; Consumption; koreacentral")]
        conditions.extend(llm_condition(group, key, value, locator) for key, value in stated if value)
    return prices, conditions
