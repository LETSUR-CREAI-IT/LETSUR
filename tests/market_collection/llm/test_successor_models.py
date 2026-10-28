"""Successor-model collection checks (Gemini 4 Argon, Claude Haiku 5.5, Kimi K3.1 Pro, GLM 5.3); fixtures contain no network data."""

import unittest
from dataclasses import replace

from scripts.market_collection.adapters import llm
from scripts.market_collection.collectors import google
from scripts.market_collection.core import _validate_parse, validate_registry
from scripts.market_collection.sources import llm_endpoint_source_specs, source_specs
from tests.market_collection.anthropic_page import HIGH, LOW, PAGE as ANTHROPIC_PAGE, RATES as ANTHROPIC_RATES, page as anthropic_page, table_row
from tests.market_collection.google_page import ARGON_RATES, IMPLICIT_ONLY, PAGE, argon_section, flash38_section, neighbour_section, page

FLASH38 = "google_gemini_38_flash"
ARGON = "google_gemini_4_argon"
HAIKU = "anthropic_claude_haiku_5_5"
HAIKU_45 = "anthropic_claude_haiku_4_5"
STORAGE_UNKNOWN = "source quotes USD per token-hour; core has no canonical token-hour price unit"


def flash38_locator(tier, label, text):
    return f"Gemini 3.8 Flash > {tier} > {label} > Paid Tier: {text}"


# (billing_item, price_kind, amount, unit, condition_set_id, effective_from, effective_until, locator, unknown_reason)
# as the Google parser emitted them for this page before it learned a second model.
FLASH38_PRICES = [
    ("input", "published_rate", "0.75", "1M tokens", "google_standard_input_current", "", "2026-12-31", flash38_locator("Standard", "Input price", "$0.75 through December 31, 2026."), ""),
    ("input", "announced_future_rate", "1.50", "1M tokens", "google_standard_input_future", "2027-01-01", "", flash38_locator("Standard", "Input price", "$1.50 starting January 1, 2027."), ""),
    ("output", "published_rate", "3.75", "1M tokens", "google_standard_output_current", "", "2026-12-31", flash38_locator("Standard", "Output price (including thinking tokens)", "$3.75 through December 31, 2026."), ""),
    ("output", "announced_future_rate", "7.50", "1M tokens", "google_standard_output_future", "2027-01-01", "", flash38_locator("Standard", "Output price (including thinking tokens)", "$7.50 starting January 1, 2027."), ""),
    ("cache_read", "published_rate", "0.075", "1M tokens", "google_standard_cache_read_current", "", "2026-12-31", flash38_locator("Standard", "Context caching price", "$0.075 through December 31, 2026."), ""),
    ("cache_read", "announced_future_rate", "0.15", "1M tokens", "google_standard_cache_read_future", "2027-01-01", "", flash38_locator("Standard", "Context caching price", "$0.15 starting January 1, 2027."), ""),
    ("cache_storage", "published_rate", "", "", "google_standard_cache_storage_current", "", "2026-12-31", flash38_locator("Standard", "Context caching price", "$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026."), STORAGE_UNKNOWN),
    ("cache_storage", "announced_future_rate", "", "", "google_standard_cache_storage_future", "2027-01-01", "", flash38_locator("Standard", "Context caching price", "$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027."), STORAGE_UNKNOWN),
    ("input", "published_rate", "0.375", "1M tokens", "google_batch_input_current", "", "2026-12-31", flash38_locator("Batch", "Input price", "$0.375 through December 31, 2026."), ""),
    ("input", "announced_future_rate", "0.75", "1M tokens", "google_batch_input_future", "2027-01-01", "", flash38_locator("Batch", "Input price", "$0.75 starting January 1, 2027."), ""),
    ("cache_read", "published_rate", "0.0375", "1M tokens", "google_batch_cache_read_current", "", "2026-12-31", flash38_locator("Batch", "Context caching price", "$0.0375 through December 31, 2026."), ""),
    ("cache_read", "announced_future_rate", "0.075", "1M tokens", "google_batch_cache_read_future", "2027-01-01", "", flash38_locator("Batch", "Context caching price", "$0.075 starting January 1, 2027."), ""),
    ("cache_storage", "published_rate", "", "", "google_batch_cache_storage_current", "", "2026-12-31", flash38_locator("Batch", "Context caching price", "$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026."), STORAGE_UNKNOWN),
    ("cache_storage", "announced_future_rate", "", "", "google_batch_cache_storage_future", "2027-01-01", "", flash38_locator("Batch", "Context caching price", "$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027."), STORAGE_UNKNOWN),
]


def argon_expected():
    """condition_set_id -> (billing_item, price_kind, amount, effective_from, effective_until) for the collected tiers."""
    rows = {}
    for tier, rates in ARGON_RATES.items():
        if tier == "contributor":
            continue
        for item, (now, later) in zip(("input", "output", "cache_read"), rates):
            rows[f"google_{tier}_{item}_current"] = (item, "published_rate", now, "", "2026-11-30")
            rows[f"google_{tier}_{item}_future"] = (item, "announced_future_rate", later, "2026-12-01", "")
    return rows


class GoogleSuccessorTests(unittest.TestCase):
    def spec(self, key):
        specs = {spec.source_key: spec for spec in llm.source_specs()}
        if key not in specs:
            self.fail(f"{key} is not registered")
        return specs[key]

    def parse(self, key, body):
        spec = self.spec(key)
        result = llm.parse_source(spec, body, "source-1")
        _validate_parse(spec, "source-1", *result)
        return result

    def conditions_by_set(self, conditions):
        by_set = {}
        for row in conditions:
            by_set.setdefault(row["condition_set_id"], []).append((row["condition_key"], row["operator"], row["value"], row["unit"], row["status"], row["locator"]))
        return by_set

    def test_flash38_rows_and_conditions_are_unchanged(self):
        prices, conditions = self.parse(FLASH38, PAGE)
        self.assertEqual([(p["billing_item"], p["price_kind"], p["amount"], p["unit"], p["condition_set_id"], p["effective_from"], p["effective_until"], p["locator"], p["unknown_reason"]) for p in prices], FLASH38_PRICES)
        self.assertEqual({p["provider_model_id"] for p in prices}, {"gemini-3.8-flash"})
        expected = {row[4]: [("service_tier", "eq", row[4].split("_")[1], "", "stated", row[7]),
                             ("rate_period", "eq", "through" if row[4].endswith("current") else "starting", "", "stated", row[7])] for row in FLASH38_PRICES}
        self.assertEqual(self.conditions_by_set(conditions), expected)

    def test_argon_is_registered_as_an_official_google_source(self):
        spec = self.spec(ARGON)
        self.assertEqual((spec.model_id, spec.modality, spec.route, spec.task, spec.url),
                         ("gemini-4-argon", "llm", "google_direct", "text_generation", self.spec(FLASH38).url))

    def test_argon_collects_both_periods_of_every_standard_tier_row(self):
        prices, _ = self.parse(ARGON, PAGE)
        self.assertEqual(len(prices), 24)
        self.assertEqual({p["provider_model_id"] for p in prices}, {"gemini-4-argon"})
        actual = {p["condition_set_id"]: (p["billing_item"], p["price_kind"], p["amount"], p["effective_from"], p["effective_until"]) for p in prices}
        self.assertEqual(actual, argon_expected())
        self.assertEqual(actual["google_standard_input_current"], ("input", "published_rate", "2.00", "", "2026-11-30"))
        self.assertEqual(actual["google_standard_input_future"], ("input", "announced_future_rate", "4.00", "2026-12-01", ""))
        self.assertTrue(all(p["amount"] and p["unit"] == "1M tokens" and p["locator"].startswith("Gemini 4 Argon > ") for p in prices))

    def test_argon_cache_rows_state_implicit_only_caching_on_both_periods(self):
        prices, conditions = self.parse(ARGON, PAGE)
        by_set = self.conditions_by_set(conditions)
        self.assertEqual(set(by_set), {p["condition_set_id"] for p in prices})
        for set_id, rows in by_set.items():
            keys = [(key, operator, value, unit, status) for key, operator, value, unit, status, _ in rows]
            tier = set_id.split("_")[1]
            period = "through" if set_id.endswith("current") else "starting"
            expected = [("service_tier", "eq", tier, "", "stated"), ("rate_period", "eq", period, "", "stated")]
            if "_cache_read_" in set_id:
                expected.append(("explicit_prompt_caching", "eq", "not supported", "", "stated"))
                self.assertTrue(rows[-1][5].endswith(IMPLICIT_ONLY), rows[-1][5])
            self.assertEqual(sorted(keys), sorted(expected), set_id)

    def test_argon_skips_a_cell_with_unrecognised_trailing_text(self):
        body = page(argon_section(cache_note="Explicit caching costs extra."), neighbour_section("Gemini 3.6 Flash", "gemini-3.6-flash", "8.88"))
        prices, _ = self.parse(ARGON, body)
        self.assertEqual({p["billing_item"] for p in prices}, {"input", "output"})
        self.assertEqual(len(prices), 16)
        sentence_on_input = argon_section().replace("$2.00 through November 30, 2026.<br>$4.00 starting December 1, 2026.", f"$2.00 through November 30, 2026.<br>$4.00 starting December 1, 2026.<br>{IMPLICIT_ONLY}")
        prices, _ = self.parse(ARGON, page(sentence_on_input))
        self.assertNotIn("google_standard_input_current", {p["condition_set_id"] for p in prices})
        self.assertEqual(len(prices), 22)

    def test_each_model_reads_only_its_own_section(self):
        self.assertEqual(self.parse(ARGON, page(flash38_section(), neighbour_section("Gemini 3.6 Flash", "gemini-3.6-flash", "8.88"))), ([], []))
        prices, _ = self.parse(FLASH38, page(argon_section(), neighbour_section("Gemini 3.6 Flash", "gemini-3.6-flash", "8.88")))
        self.assertEqual(prices, [])
        prices, _ = self.parse(ARGON, PAGE)
        self.assertFalse({p["amount"] for p in prices} & {"0.75", "3.75", "8.88"})

    def test_google_parser_rejects_a_model_it_has_no_section_for(self):
        with self.assertRaises(ValueError):
            google.parse_source(replace(self.spec(FLASH38), model_id="gemini-9-x"), PAGE, "source-1")


def haiku_expected():
    """(billing_item, amount, stated conditions) of the ten collected Haiku 5.5 rows: five items in each prompt-length tier.
    Only the tier above 100,000 tokens carries a prompt_tokens condition, as the gateway endpoint rows do (llm_endpoints.py)."""
    rows = []
    for title, over in ((LOW, False), (HIGH, True)):
        for (item, duration), amount in zip((("input", ""), ("cache_write", "5m"), ("cache_write", "1h"), ("cache_read", ""), ("output", "")), ANTHROPIC_RATES[title]):
            conditions = {"service_tier": ("eq", "standard")}
            if duration:
                conditions["cache_duration"] = ("eq", duration)
            if over:
                conditions["prompt_tokens"] = ("gt", "100000")
            rows.append((item, amount, tuple(sorted(conditions.items()))))
    return sorted(rows)


class HaikuTierTests(unittest.TestCase):
    def parse(self, key, body):
        spec = next(spec for spec in llm.source_specs() if spec.source_key == key)
        result = llm.parse_source(spec, body, "source-1")
        _validate_parse(spec, "source-1", *result)
        return result

    def rows(self, key, body):
        prices, conditions = self.parse(key, body)
        by_set = {}
        for row in conditions:
            by_set.setdefault(row["condition_set_id"], {})[row["condition_key"]] = (row["operator"], row["value"])
        return prices, conditions, sorted((p["billing_item"], p["amount"], tuple(sorted(by_set[p["condition_set_id"]].items()))) for p in prices)

    def test_both_prompt_length_tiers_are_collected_as_separate_rows(self):
        prices, conditions, rows = self.rows(HAIKU, ANTHROPIC_PAGE)
        self.assertEqual(rows, haiku_expected())
        self.assertEqual(len({p["condition_set_id"] for p in prices}), 10)
        self.assertEqual({(p["provider_model_id"], p["price_kind"], p["unit"], p["effective_from"], p["effective_until"]) for p in prices},
                         {("claude-haiku-5-5", "published_rate", "1M tokens", "", "")})
        self.assertNotIn("cache_read_multiplier", {row["condition_key"] for row in conditions})

    def test_every_row_cites_its_own_tier_and_ignores_neighbouring_rows(self):
        prices, conditions, _ = self.rows(HAIKU, ANTHROPIC_PAGE)
        for price in prices:
            self.assertTrue(price["locator"].startswith((f"Model pricing > {LOW} > ", f"Model pricing > {HIGH} > ")), price["locator"])
        over = [row for row in conditions if row["condition_key"] == "prompt_tokens"]
        self.assertEqual(len(over), 5)
        self.assertEqual({row["locator"] for row in over}, {f"Model pricing > {HIGH}"})
        self.assertFalse({"1.25", "2", "5", "0.25"} & {p["amount"] for p in prices})

    def test_a_missing_or_renamed_row_cell_header_or_section_raises(self):
        without_high = anthropic_page({title: rates for title, rates in ANTHROPIC_RATES.items() if title != HIGH})
        listed_twice = ANTHROPIC_PAGE.replace(b"\n## Batch processing", b"\n" + table_row(HIGH, ANTHROPIC_RATES[HIGH]).encode() + b"\n## Batch processing")
        broken = {
            "section renamed": ANTHROPIC_PAGE.replace(b"## Model pricing", b"## Model prices"),
            "low tier renamed": ANTHROPIC_PAGE.replace(LOW.encode(), b"Claude Haiku 5.5 (for prompts up to 200,000 tokens)"),
            "high tier absent": without_high,
            "high tier listed twice": listed_twice,
            "cell without a price": ANTHROPIC_PAGE.replace(b"$0.625 / MTok", b"N/A"),
            "header changed": ANTHROPIC_PAGE.replace(b"| 5m cache writes |", b"| 5 minute cache writes |"),
            "empty page": b"",
        }
        for name, body in broken.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.parse(HAIKU, body)

    def test_haiku_45_is_unchanged_next_to_the_new_rows(self):
        prices, _, rows = self.rows(HAIKU_45, ANTHROPIC_PAGE)
        self.assertEqual(rows, sorted(
            [("input", "1", (("service_tier", ("eq", "standard")),)),
             ("cache_write", "1.25", (("cache_duration", ("eq", "5m")), ("service_tier", ("eq", "standard")))),
             ("cache_write", "2", (("cache_duration", ("eq", "1h")), ("service_tier", ("eq", "standard")))),
             ("cache_read", "0.10", (("service_tier", ("eq", "standard")),)),
             ("output", "5", (("service_tier", ("eq", "standard")),))]))
        self.assertEqual({p["provider_model_id"] for p in prices}, {"claude-haiku-4-5"})
        # An existing model that its row cannot be found for still yields nothing instead of raising.
        no_row = anthropic_page({title: rates for title, rates in ANTHROPIC_RATES.items() if title != "Claude Haiku 4.5"})
        self.assertEqual(self.parse(HAIKU_45, no_row), ([], []))


# The registry before the successors ends here. Retained fixtures pin everything up to this source, so the
# successors are listed after it and the endpoint sources stay last.
ORIGINAL_LAST = "image25_fal_guide_flare_doc"
SUCCESSOR_MODEL_IDS = {"gemini-4-argon", "claude-haiku-5-5", "kimi-k3.1-pro", "glm-5.3"}

# (source_key, model_id, route, url, parser)
SUCCESSOR_SOURCES = [
    ("google_gemini_4_argon", "gemini-4-argon", "google_direct", "https://ai.google.dev/gemini-api/docs/pricing", "google_gemini_4_argon"),
    ("anthropic_claude_haiku_5_5", "claude-haiku-5-5", "anthropic_direct", "https://platform.claude.com/docs/en/about-claude/pricing.md", "anthropic_claude_haiku_5_5"),
    ("openrouter_claude_haiku_5_5", "claude-haiku-5-5", "openrouter", "https://openrouter.ai/anthropic/claude-haiku-5.5/providers", "openrouter_claude_haiku_5_5"),
    ("vercel_claude_haiku_5_5", "claude-haiku-5-5", "vercel_ai_gateway", "https://vercel.com/ai-gateway/models/claude-haiku-5.5.md", "vercel_claude_haiku_5_5"),
    ("bedrock_claude_haiku_5_5", "claude-haiku-5-5", "aws_bedrock", "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html", "cloud_bedrock_claude_haiku_5_5"),
    ("azure_claude_haiku_5_5", "claude-haiku-5-5", "azure_foundry", "https://ai.azure.com/catalog/models/claude-haiku-5-5", "cloud_azure_claude_haiku_5_5"),
    ("openrouter_gemini_4_argon", "gemini-4-argon", "openrouter", "https://openrouter.ai/google/gemini-4-argon", "missing_openrouter_gemini_4_argon"),
    ("vercel_gemini_4_argon", "gemini-4-argon", "vercel_ai_gateway", "https://vercel.com/ai-gateway/models/gemini-4-argon.md", "missing_vercel_gemini_4_argon"),
    ("openrouter_kimi_k3_1_pro", "kimi-k3.1-pro", "openrouter", "https://openrouter.ai/moonshotai/kimi-k3.1-pro", "missing_openrouter_kimi_k3_1_pro"),
    ("vercel_kimi_k3_1_pro", "kimi-k3.1-pro", "vercel_ai_gateway", "https://vercel.com/ai-gateway/models/kimi-k3.1-pro.md", "missing_vercel_kimi_k3_1_pro"),
    ("openrouter_glm_5_3", "glm-5.3", "openrouter", "https://openrouter.ai/z-ai/glm-5.3", "missing_openrouter_glm_5_3"),
    ("vercel_glm_5_3", "glm-5.3", "vercel_ai_gateway", "https://vercel.com/ai-gateway/models/glm-5.3.md", "missing_vercel_glm_5_3"),
]

# (source key suffix, model_id, OpenRouter id, Vercel id)
SUCCESSOR_GATEWAY_IDS = [
    ("gemini_4_argon", "gemini-4-argon", "google/gemini-4-argon", "google/gemini-4-argon"),
    ("claude_haiku_5_5", "claude-haiku-5-5", "anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5"),
    ("kimi_k3_1_pro", "kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro"),
    ("glm_5_3", "glm-5.3", "z-ai/glm-5.3", "zai/glm-5.3"),
]


def successor_endpoint_sources():
    rows = []
    for suffix, model, openrouter_id, vercel_id in SUCCESSOR_GATEWAY_IDS:
        rows.append((f"openrouter_llm_endpoints_{suffix}", model, "openrouter", f"https://openrouter.ai/api/v1/models/{openrouter_id}/endpoints", "openrouter_endpoints_llm"))
        rows.append((f"vercel_llm_endpoints_{suffix}", model, "vercel_ai_gateway", f"https://ai-gateway.vercel.sh/v1/models/{vercel_id}/endpoints", "vercel_endpoints_llm"))
    return rows


class SuccessorRegistryTests(unittest.TestCase):
    def test_successors_follow_the_original_sources_and_the_endpoints_stay_last(self):
        specs = source_specs()
        validate_registry(specs)
        keys = [spec.source_key for spec in specs]
        start = keys.index(ORIGINAL_LAST) + 1
        end = start + len(SUCCESSOR_SOURCES)
        self.assertEqual(keys[start:end], [row[0] for row in SUCCESSOR_SOURCES])
        self.assertEqual(specs[end:], llm_endpoint_source_specs())
        ahead = [spec.source_key for spec in specs[:start] if spec.model_id in SUCCESSOR_MODEL_IDS]
        self.assertEqual(ahead, [])

    def test_successor_sources_are_registered_with_exact_urls_and_parsers(self):
        by_key = {spec.source_key: spec for spec in source_specs()}
        for key, model, route, url, parser in SUCCESSOR_SOURCES + successor_endpoint_sources():
            with self.subTest(source=key):
                self.assertIn(key, by_key)
                spec = by_key[key]
                self.assertEqual((spec.model_id, spec.modality, spec.route, spec.task, spec.url, spec.parser),
                                 (model, "llm", route, "text_generation", url, parser))


if __name__ == "__main__":
    unittest.main()
