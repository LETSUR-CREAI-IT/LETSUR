"""Fixed-page Azure GPT token meters: Global Standard, short context, koreacentral; never follow catalog pagination."""
import json
import math
from .common import _number, llm_condition, llm_price

PARSERS = {"azure_llm_retail"}
GPT6, GPT5 = "Azure OpenAI GPT6", "Azure OpenAI GPT5"
# Exact skuName per billing item (meterName adds " 1M Tokens"); Azure spells the models unevenly (6-sol, 6.1-sol,
# 5.6 sol), so names are listed, never matched by pattern. The items are the ones OpenAI direct lists for the model:
# GPT-5.6 has no cache-write row there, so its Azure "Cd Wr" meters stay out. LongCo (long context), DZ (Data Zone),
# PP (priority), Flex and other meters are not read.
METERS = {
    "gpt-6-astra": (GPT6, {"input": "6-astra ShortCo Inp Std Gl", "cache_read": "6-astra ShortCo Cd Inp Std Gl",
                           "cache_write": "6-astra ShortCo Cd Wr Std Gl", "output": "6-astra ShortCo Opt Std Gl"}),
    "gpt-6-sol": (GPT6, {"input": "6-sol ShortCo Inp Std Gl", "cache_read": "6-sol ShortCo Cd Inp Std Gl",
                         "cache_write": "6-sol ShortCo Cd Wr Std Gl", "output": "6-sol ShortCo Opt Std Gl"}),
    "gpt-6.1-sol": (GPT6, {"input": "6.1-sol ShortCo Inp Std Gl", "cache_read": "6.1-sol ShortCo Cd Inp Std Gl",
                           "cache_write": "6.1-sol ShortCo Cd Wr Std Gl", "output": "6.1-sol ShortCo Opt Std Gl"}),
    "gpt-6-luna": (GPT6, {"input": "6-luna ShortCo Inp Std Gl", "cache_read": "6-luna ShortCo Cd Inp Std Gl",
                          "cache_write": "6-luna ShortCo Cd Wr Std Gl", "output": "6-luna ShortCo Opt Std Gl"}),
    "gpt-5.6-sol": (GPT5, {"input": "5.6 sol ShortCo Inp Std Gl", "cache_read": "5.6 sol ShortCo Cd Inp Std Gl",
                           "output": "5.6 sol ShortCo Opt Std Gl"}),
    "gpt-5.6-terra": (GPT5, {"input": "5.6 terra ShortCo Inp Std Gl", "cache_read": "5.6 terra ShortCo Cd Inp Std Gl",
                             "output": "5.6 terra ShortCo Opt Std Gl"}),
    "gpt-5.6-luna": (GPT5, {"input": "5.6 luna ShortCo Inp Std Gl", "cache_read": "5.6 luna ShortCo Cd Inp Std Gl",
                            "output": "5.6 luna ShortCo Opt Std Gl"}),
}


def parse_source(spec, body, source_id):
    data = json.loads(body)
    if data.get("BillingCurrency") != "USD" or "NextPageLink" not in data or data["NextPageLink"] not in (None, ""):
        raise ValueError("Azure currency or fixed-page completeness invalid")
    product, skus = METERS[spec.model_id]
    item_of = {sku: item for item, sku in skus.items()}
    found, meters = {}, set()
    for i, row in enumerate(data["Items"]):
        sku = row.get("skuName", "")
        name = row.get("meterName", "")
        if sku not in item_of and name.removesuffix(" 1M Tokens") not in item_of:
            continue
        if row.get("unitOfMeasure") != "1M" or not name.endswith(" 1M Tokens"):
            raise ValueError("Azure meter not priced per 1M tokens")
        if name != sku + " 1M Tokens" or any(row.get(k) != v for k, v in {
            "currencyCode": "USD", "armRegionName": "koreacentral", "type": "Consumption",
            "productName": product, "serviceName": "Foundry Models",
        }.items()) or row.get("tierMinimumUnits") != 0:
            raise ValueError("Azure exact meter identity, region, or type invalid")
        price, meter = row.get("retailPrice"), row.get("meterId")
        if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
            raise ValueError("Azure retailPrice invalid")
        if not meter or sku in found or meter in meters:
            raise ValueError("Azure duplicate or missing meter")
        found[sku] = (i, row); meters.add(meter)
    if set(found) != set(item_of):
        raise ValueError("Azure exact model meter set incomplete")
    prices, conditions = [], []
    for item, sku in skus.items():
        i, row = found[sku]
        locator = f"Items/{i}; meterId={row['meterId']}; meterName={row['meterName']}; retailPrice={row['retailPrice']}"
        group = f"{spec.source_key}_{item}"
        prices.append(llm_price(provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate",
                                amount=_number(str(row["retailPrice"])), unit="1M tokens", set_id=group, locator=locator,
                                effective_from=row["effectiveStartDate"]))
        conditions.extend(llm_condition(group, key, value, locator) for key, value in (
            ("service_tier", "standard"), ("meter_scope", "Global (Gl)"), ("context_band", "short context (ShortCo)"),
            ("price_basis", "Azure retailPrice; Consumption; koreacentral")))
    return prices, conditions
