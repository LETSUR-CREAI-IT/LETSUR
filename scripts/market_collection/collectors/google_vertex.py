"""Standard Global Gemini rates from exact semantic Vertex table headers."""
import re
from datetime import datetime
from .common import _Page, llm_condition, llm_price, _number

HEADERS = ["Model", "Type", "Region", "Price (/1M tokens)<= 200K input tokens",
           "Price (/1M tokens)> 200K input tokens", "Price (/1M tokens)<= 200K cached input tokens",
           "Price (/1M tokens)> 200K cached input tokens"]


def parse_source(spec, body, source_id):
    page = _Page(); page.feed(body.decode("utf-8")); page.close()
    if not any(section == "Cost of building and deploying AI models in Agent Platform" and "Prices are listed in US Dollars (USD)." in value for section, value in page.text):
        raise ValueError("Vertex page currency declaration missing")
    tables = [rows for section, _, rows in page.tables if section == "Gemini 3" and rows
              and rows[0] == [("th", h) for h in HEADERS]]
    if len(tables) != 1:
        raise ValueError("Vertex exact standard price headers missing or duplicate")
    prices, conditions, seen = [], [], set()
    model, kind = "", ""
    for i, row in enumerate(tables[0][1:], 1):
        if len(row) != 7 or any(tag != "td" for tag, _ in row):
            raise ValueError("Vertex malformed standard table row")
        values = [text for _, text in row]
        if values[0]:
            model = values[0]; kind = ""  # Reset inherited type on every new model.
        if values[1]:
            kind = values[1]
        period = re.fullmatch(r"Gemini 3\.8 Flash\*?(through|Starting) (\w+ \d+, \d{4})", model)
        if not period or values[2] != "Global":
            continue
        future = period[1] == "Starting"
        date = datetime.strptime(period[2], "%B %d, %Y").date().isoformat()
        if kind == "Input (text, image, video, audio)":
            columns = ((3, "input"), (4, "input"), (5, "cache_read"), (6, "cache_read"))
        elif kind == "Text output (response and reasoning)":
            columns = ((3, "output"), (4, "output"))
        else:
            raise ValueError("Vertex exact model type changed")
        for col, item in columns:
            if not re.fullmatch(r"\$\d+(?:\.\d+)?", values[col]):
                raise ValueError("Vertex rate or unit missing")
            band = "<=200K" if col in (3, 5) else ">200K"
            identity = (future, item, band)
            if identity in seen:
                raise ValueError("Vertex duplicate model rate")
            seen.add(identity)
            group = f"{spec.source_key}_{item}_{'low' if col in (3,5) else 'high'}_{'future' if future else 'current'}"
            locator = f"Gemini 3 > standard table row {i}; model={model}; Type={kind}; Region=Global; {HEADERS[col]}={values[col]}"
            prices.append(llm_price(provider_model_id=spec.model_id, upstream="Google", item=item,
                kind="announced_future_rate" if future else "published_rate", amount=_number(values[col][1:]), unit="1M tokens",
                set_id=group, locator=locator, effective_from=date if future else "", effective_until="" if future else date))
            for key, value in (("region", "Global"), ("service_tier", "standard"), ("input_token_band", band),
                               ("input_modalities", "text,image,video,audio"), ("rate_period", period[1]), ("currency_basis", "Prices are listed in US Dollars (USD).")):
                conditions.append(llm_condition(group, key, value, locator))
    if len(prices) != 12:
        raise ValueError("Vertex current/future Global rate set incomplete")
    return prices, conditions
