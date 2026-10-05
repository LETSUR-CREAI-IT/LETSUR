"""Exact Databricks endpoint listing; the source supplies no numeric rates."""
import re
from .common import _Page, llm_condition, llm_price


def parse_source(spec, body, source_id):
    page = _Page(); page.feed(body.decode("utf-8")); page.close()
    if page.h2.count("Google Gemini 3.8 Flash") != 1:
        raise ValueError("Databricks exact model heading absent or duplicate")
    scoped = " ".join(value for section, value in page.text if section == "Google Gemini 3.8 Flash")
    endpoint = "databricks-gemini-3-8-flash"
    if not re.search(r"(?<![a-zA-Z0-9_-])" + re.escape(endpoint) + r"(?![a-zA-Z0-9_-])", scoped) or "global endpoint" not in scoped or "cross geography routing to be enabled" not in scoped:
        raise ValueError("Databricks exact endpoint or geographic conditions absent")
    group = spec.source_key + "_listing"
    locator = "Gemini 3.8 Flash > Endpoint name: " + endpoint
    reason = "Exact supported-model listing provides no Databricks-specific numeric input/output rates"
    return [llm_price(provider_model_id=endpoint, upstream="Google", item=item, kind="published_rate", amount="", unit="", set_id=group, locator=locator, unknown_reason=reason) for item in ("input", "output")], [
        llm_condition(group, "endpoint_scope", "global", locator),
        llm_condition(group, "cross_geography_routing", "required", locator),
        llm_condition(group, "price_basis", "Azure Databricks supported-model listing; no numeric quote", locator)]
