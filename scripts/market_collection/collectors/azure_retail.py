"""Fixed-page Azure image token meters; never follow catalog pagination."""
import json
import re
from .common import _number, llm_condition
from scripts.market_collection.core import ADAPTER_PRICE_FIELDS


def parse_source(spec, body, source_id):
    data = json.loads(body)
    if data.get("BillingCurrency") != "USD" or "NextPageLink" not in data or data["NextPageLink"] not in (None, ""):
        raise ValueError("Azure currency or fixed-page completeness invalid")
    prefix = "Image-2.5-" + spec.model_id.split("-")[-1]
    labels = {"img inp": "Image tokens / Input", "img opt": "Image tokens / Output",
              "txt inp": "Text tokens / Input", "txt cd inp": "Text tokens / Cached input",
              "img cd inp": "Image tokens / Cached input"}
    prices, conditions, seen, meters = [], [], set(), set()
    for i, row in enumerate(data["Items"]):
        name = row.get("meterName", "")
        if not name.startswith(prefix + " "):
            continue
        match = re.fullmatch(re.escape(prefix) + r" (img inp|img opt|txt inp|txt cd inp|img cd inp) Gl 1M Tokens", name)
        if not match or any(row.get(k) != v for k, v in {
            "currencyCode": "USD", "armRegionName": "koreacentral", "type": "Consumption",
            "unitOfMeasure": "1M", "productName": "Azure OpenAI Media", "serviceName": "Foundry Models",
        }.items()) or row.get("tierMinimumUnits") != 0 or row.get("skuName") != prefix + " " + match[1] + " Gl":
            raise ValueError("Azure exact meter identity, region, unit, or type invalid")
        label = labels[match[1]]
        meter = row.get("meterId")
        if not meter or label in seen or meter in meters:
            raise ValueError("Azure duplicate or missing meter")
        seen.add(label); meters.add(meter)
        locator = f"Items/{i}; meterId={meter}; meterName={name}; retailPrice={row['retailPrice']}"
        group = spec.source_key + "_" + meter
        amount = _number(str(row["retailPrice"]))
        price = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
        price.update(provider_model_id=spec.model_id, model_version="2.5", upstream_provider="OpenAI", task=spec.task,
                     billing_item=label, price_kind="published_rate", amount=amount, currency="USD", unit="1M tokens",
                     condition_set_id=group, effective_from=row["effectiveStartDate"], locator=locator, evidence_status="direct")
        prices.append(price)
        for key in ("meterId", "meterName", "productName", "skuId", "skuName", "armSkuName", "armRegionName", "tierMinimumUnits", "effectiveStartDate"):
            conditions.append(llm_condition(group, key, str(row[key]), locator))
        conditions.extend([llm_condition(group, "meter_scope", "Global (Gl)", locator),
                           llm_condition(group, "price_basis", "Azure retailPrice; Consumption", locator),
                           dict(condition_set_id=group, condition_key="deployment_eligibility", operator="eq", value="not established by retail API", unit="", status="unknown", locator=locator)])
    if seen != set(labels.values()):
        raise ValueError("Azure exact model five-meter set incomplete")
    return prices, conditions
