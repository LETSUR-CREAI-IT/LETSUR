"""Source-scoped LLM extraction checks; fixtures contain no network data."""

import unittest

from scripts.market_collection.adapters import llm
from scripts.market_collection.models import STANDARD_LLM_MODELS
from scripts.market_collection.core import _validate_parse, validate_registry


def google_page(input_amount="0.75", *, paid_header="Paid Tier, per 1M tokens in USD", input_row=True):
    row = f"<tr><td>Input price</td><td>Free of charge</td><td>${input_amount} through December 31, 2026.<br>$1.50 starting January 1, 2027.</td></tr>" if input_row else ""
    return f"""<h2 id='gemini-3.7-flash'>Gemini 3.7 Flash</h2><h3>Standard</h3><table><tr><th></th><th>Free Tier</th><th>{paid_header}</th></tr><tr><td>Input price</td><td></td><td>$999 through December 31, 2026.</td></tr></table>
<h2 id='gemini-3.8-flash'>Gemini 3.8 Flash</h2>
<h3 id='standard'>Standard</h3><table><tr><th></th><th>Free Tier</th><th>{paid_header}</th></tr>
{row}<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$3.75 through December 31, 2026.<br>$7.50 starting January 1, 2027.</td></tr>
<tr><td>Context caching price</td><td>Free of charge</td><td>$0.075 through December 31, 2026.<br>$0.15 starting January 1, 2027.<br>$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.<br>$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027.</td></tr></table>
<h3 id='flex'>Flex</h3><table><tr><th></th><th>Free Tier</th><th>{paid_header}</th></tr><tr><td>Input price</td><td>Not available</td><td>$0.375 through December 31, 2026.<br>$0.75 starting January 1, 2027.</td></tr></table>
<h3 id='priority'>Priority</h3><table><tr><th></th><th>Free Tier</th><th>{paid_header}</th></tr><tr><td>Input price</td><td>Free of charge</td><td>$1.35 through December 31, 2026.<br>$2.70 starting January 1, 2027.</td></tr></table>
<h2 id='gemini-3.9-flash'>Gemini 3.9 Flash</h2><h3>Standard</h3><table><tr><th></th><th>Free Tier</th><th>{paid_header}</th></tr><tr><td>Input price</td><td></td><td>$888 through December 31, 2026.</td></tr></table>""".encode()


class LlmAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specs = {spec.parser: spec for spec in llm.source_specs()}

    def parse(self, parser, body):
        result = llm.parse_source(self.specs[parser], body, "source-1")
        _validate_parse(self.specs[parser], "source-1", *result)
        return result

    def test_registry_is_fixed_and_distinct(self):
        validate_registry(llm.source_specs())
        self.assertEqual(len(llm.source_specs()), 109)
        self.assertEqual(len({spec.source_key for spec in self.specs.values()}), 69)  # parser keys: endpoints and Azure retail share theirs
        self.assertEqual([spec.model_id for spec in llm.source_specs()[:3]], ["gemini-3.8-flash"] * 3)
        self.assertTrue({"gpt-6-astra", "claude-fable-5-1"}.issubset({spec.model_id for spec in llm.source_specs()}))
        for model, _, _ in STANDARD_LLM_MODELS:
            self.assertEqual({spec.route for spec in llm.source_specs() if spec.model_id == model and spec.route in {"openai_direct", "anthropic_direct", "openrouter", "vercel_ai_gateway"}}, {"openai_direct" if model.startswith("gpt-") else "anthropic_direct", "openrouter", "vercel_ai_gateway"})

    def test_google_amount_change_periods_and_tiers(self):
        prices, conditions = self.parse("google", google_page())
        changed, _ = self.parse("google", google_page("0.92"))
        standard_input = [row for row in prices if row["billing_item"] == "input" and row["condition_set_id"].startswith("google_standard")]
        self.assertEqual([(r["amount"], r["price_kind"]) for r in standard_input], [("0.75", "published_rate"), ("1.50", "announced_future_rate")])
        self.assertEqual([r["amount"] for r in changed if r["condition_set_id"] == "google_standard_input_current"], ["0.92"])
        self.assertTrue(all("Gemini 3.8 Flash" in r["locator"] and "999" not in r["amount"] and "888" not in r["amount"] for r in prices))
        self.assertEqual({r["effective_from"] for r in prices if r["price_kind"] == "announced_future_rate"}, {"2027-01-01"})
        self.assertEqual({r["effective_until"] for r in prices if r["price_kind"] == "published_rate"}, {"2026-12-31"})
        self.assertIn("google_flex_input_current", {r["condition_set_id"] for r in prices})
        self.assertIn("google_priority_input_current", {r["condition_set_id"] for r in prices})
        self.assertIn("google_standard_cache_read_current", {r["condition_set_id"] for r in prices})
        storage = [r for r in prices if r["billing_item"] == "cache_storage"]
        self.assertEqual(len(storage), 2)
        self.assertTrue(all(not r["amount"] and "token-hour" in r["unknown_reason"] for r in storage))
        self.assertEqual({c["condition_key"] for c in conditions}, {"service_tier", "rate_period"})

    def test_missing_row_or_unit_never_borrows_another_model(self):
        prices, _ = self.parse("google", google_page(input_row=False))
        self.assertFalse(any(r["billing_item"] == "input" and r["condition_set_id"].startswith("google_standard") for r in prices))
        prices, _ = self.parse("google", google_page(paid_header="Paid Tier"))
        self.assertEqual(prices, [])

    def test_broker_prices_are_scoped_and_qualified(self):
        openrouter = b"<h1>Google: Gemini 3.8 Flash</h1><p>In / Out Price</p><p>50% off</p><p>$0.75 / $3.75 per 1M</p><h1>Other model</h1><p>$999 / $999 per 1M</p>"
        vercel = b"<h1>Gemini 3.8 Flash</h1><p>Prices from: Input $0.75, Output $3.75, Per 1M tokens</p><h1>Other model</h1><p>Prices from: Input $999, Output $999, Per 1M tokens</p>"
        for parser, body, kind in (("openrouter", openrouter, "published_rate"), ("vercel", vercel, "published_rate")):
            prices, conditions = self.parse(parser, body)
            self.assertEqual([r["amount"] for r in prices], ["0.75", "3.75"])
            self.assertEqual({r["price_kind"] for r in prices}, {kind})
            self.assertEqual(len(conditions), 2 if parser == "openrouter" else 1)
            if parser == "openrouter":
                self.assertIn(("promotion_label", "50% off", "stated"), {(c["condition_key"], c["value"], c["status"]) for c in conditions})
            self.assertEqual(self.parse(parser, b"<h1>Other model</h1>" + body.replace(b"<h1>Gemini 3.8 Flash</h1>", b"" ).replace(b"<h1>Google: Gemini 3.8 Flash</h1>", b""))[0], [])

        no_promo = openrouter.replace(b"50% off</p>", b"</p>")
        prices, conditions = self.parse("openrouter", no_promo)
        self.assertEqual([r["amount"] for r in prices], ["0.75", "3.75"])
        self.assertFalse(any(c["condition_key"] == "promotion_label" for c in conditions))

    def test_vercel_retained_html_comment_layout(self):
        # Faithful fragment of the retained public Vercel raw: React comments
        # divide the screen-reader price text into separate HTML text nodes.
        body = (b'<h1 class="text-heading-32">Gemini 3.8 Flash</h1>'
                b'<span class="sr-only">Prices from: <!-- -->Input $0.75, Output $3.75'
                b'<!-- -->, Per 1M tokens</span>'
                b'<h2>Other model</h2><span>Prices from: Input $999, Output $999, Per 1M tokens</span>')
        prices, conditions = self.parse("vercel", body)
        self.assertEqual([(row["billing_item"], row["amount"], row["unit"], row["price_kind"])
                          for row in prices], [("input", "0.75", "1M tokens", "published_rate"),
                                               ("output", "3.75", "1M tokens", "published_rate")])
        self.assertTrue(all("Prices from: Input $0.75, Output $3.75" in row["locator"] for row in prices))
        self.assertEqual([(c["condition_key"], c["value"]) for c in conditions], [("price_basis", "prices_from_minimum")])

    def test_unbound_rate_and_parse_exception(self):
        self.assertEqual(self.parse("openrouter", b"<h1>Google: Gemini 3.8 Flash</h1><p>In / Out Price Loading</p>")[0], [])
        self.assertEqual(self.parse("vercel", b"<h1>Gemini 3.8 Flash</h1><p>Input $0.75, Output $3.75</p>")[0], [])
        with self.assertRaises(ValueError):
            llm.parse_source(self.specs["google"].__class__(**{**self.specs["google"].__dict__, "url": "https://example.com"}), b"", "source-1")

    def test_broker_next_heading_ends_model_scope(self):
        # These reproduce the independent QA failure: a target h1 followed
        # by an unrelated h2 must not lend its rate to Gemini.
        for parser, target, quote in (
            ("openrouter", b"Google: Gemini 3.8 Flash", b"In / Out Price $999 / $999 per 1M"),
            ("vercel", b"Gemini 3.8 Flash", b"Prices from: Input $999, Output $999, Per 1M tokens"),
        ):
            for heading in (b"h2", b"h3"):
                body = b"<h1>" + target + b"</h1><" + heading + b">Other model</" + heading + b"><p>" + quote + b"</p>"
                self.assertEqual(self.parse(parser, body), ([], []))

        # OpenRouter uses its own slug as a target-model h3 above the quote.
        own = b"<h1>Google: Gemini 3.8 Flash</h1><h3>google /gemini-3.8-flash</h3><p>In / Out Price $0.75 / $3.75 per 1M</p>"
        self.assertEqual([row["amount"] for row in self.parse("openrouter", own)[0]], ["0.75", "3.75"])

    def test_openai_astra_exact_table_and_context_condition(self):
        body = b"""# GPT-6 Astra
Model ID: `gpt-6-astra`
## Pricing
### Text tokens
| Metric | Price | Unit |
| --- | ---: | --- |
| Input | $10 | 1M tokens |
| Cached input | $1 | 1M tokens |
| Cache writes | $12.5 | 1M tokens |
| Output | $50 | 1M tokens |
- Prompts with more than 272K input tokens are priced at 2x input and cache rates and 1.5x output for the full request.
## Neighbor
| Input | $999 | 1M tokens |"""
        prices, conditions = self.parse("openai_astra", body)
        self.assertEqual([(p["billing_item"], p["amount"], p["provider_model_id"]) for p in prices], [("input", "10", "gpt-6-astra"), ("cache_read", "1", "gpt-6-astra"), ("cache_write", "12.5", "gpt-6-astra"), ("output", "50", "gpt-6-astra")])
        self.assertEqual(sum(c["condition_key"] == "long_context_multiplier" for c in conditions), 4)
        self.assertEqual(self.parse("openai_astra", body.replace(b"# GPT-6 Astra", b"# GPT-6 Sol"))[0], [])
        self.assertEqual(self.parse("openai_astra", body.replace(b"Model ID: `gpt-6-astra`", b"Model ID: `gpt-6-sol`"))[0], [])
        self.assertEqual(self.parse("openai_astra", body.replace(b"Model ID: `gpt-6-astra`", b"Model ID: `gpt-6-astra`\nModel ID: `gpt-6-sol`"))[0], [])
        self.assertEqual(self.parse("openai_astra", body.replace(b"$10 | 1M tokens", b"$10 | 1K tokens"))[0][0]["billing_item"], "cache_read")
        self.assertEqual(self.parse("openai_astra", body.replace(b"### Text tokens", b"### Other tokens"))[0], [])

    def test_anthropic_fable_exact_row_and_units(self):
        body = b"""# Pricing
## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Mythos 5.1 | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok |
| Claude Fable 5.1 | $10 / MTok | $12.50 / MTok | $20 / MTok | $0.25 / MTok<sup>1</sup> | $50 / MTok |
| Claude Fable 5 | $888 / MTok | $888 / MTok | $888 / MTok | $888 / MTok | $888 / MTok |
## Batch processing
| Model | Batch input | Batch output |
| --- | --- | --- |
| Claude Fable 5.1 | $5 / MTok | $25 / MTok |"""
        prices, conditions = self.parse("anthropic_fable", body)
        self.assertEqual([(p["billing_item"], p["amount"]) for p in prices], [("input", "10"), ("cache_write", "12.50"), ("cache_write", "20"), ("cache_read", "0.25"), ("output", "50")])
        self.assertEqual({c["value"] for c in conditions if c["condition_key"] == "cache_duration"}, {"5m", "1h"})
        self.assertEqual(self.parse("anthropic_fable", body.replace(b"Claude Fable 5.1 | $10", b"Claude Fable 5 | $10"))[0], [])
        self.assertEqual(self.parse("anthropic_fable", body.replace(b"$10 / MTok", b"$10 / 1K tokens"))[0][0]["billing_item"], "cache_write")
        self.assertEqual(self.parse("anthropic_fable", body.replace(b"## Model pricing", b"## Other pricing"))[0], [])

    def test_new_brokers_require_exact_model_scope_and_unit(self):
        cases = (
            ("openrouter_astra", b"<h1>OpenAI: GPT-6 Astra</h1><h3>openai /gpt-6-astra</h3><p>In / Out Price $10 / $50 per 1M</p>", b"<h1>OpenAI: GPT-6 Sol</h1>"),
            ("openrouter_fable", b"<h1>Anthropic: Claude Fable 5.1</h1><h3>anthropic /claude-fable-5.1</h3><p>In / Out Price $10 / $50 per 1M</p>", b"<h1>Anthropic: Claude Fable 5</h1>"),
            ("vercel_astra", b"# GPT-6 Astra\n- **Model ID:** `openai/gpt-6-astra`\n- **Pricing:** $10/1M input tokens, $50/1M output tokens", b"# GPT-6 Sol"),
            ("vercel_fable", b"# Claude Fable 5.1\n- **Model ID:** `anthropic/claude-fable-5.1`\n- **Pricing:** $10/1M input tokens, $50/1M output tokens", b"# Claude Fable 5"),
        )
        for parser, body, wrong_h1 in cases:
            with self.subTest(parser=parser):
                prices, conditions = self.parse(parser, body)
                self.assertEqual([p["amount"] for p in prices], ["10", "50"])
                self.assertEqual([c["condition_key"] for c in conditions], ["price_basis"])
                self.assertEqual(self.parse(parser, body.replace(body.split(b"\n", 1)[0] if parser.startswith("vercel") else body.split(b"</h1>", 1)[0] + b"</h1>", wrong_h1))[0], [])
                if parser.startswith("openrouter"):
                    self.assertEqual(self.parse(parser, body.replace(b"per 1M", b"per 1K"))[0], [])
                    self.assertEqual(self.parse(parser, body.replace(b"<h3>", b"<h2>Other</h2><h3>"))[0], [])
                    self.assertEqual(self.parse(parser, body + b"<p>In / Out Price $11 / $51 per 1M</p>")[0], [])
                else:
                    self.assertEqual(self.parse(parser, body.replace(b"/1M input tokens", b"/1K input tokens"))[0], [])
                    self.assertEqual(self.parse(parser, body.replace(b"**Pricing:**", b"**Other:**"))[0], [])
                    self.assertEqual(self.parse(parser, body + b"\n- **Pricing:** $11/1M input tokens, $51/1M output tokens")[0], [])

    def test_bedrock_card_is_unknown_and_model_scoped(self):
        body = b"""# Claude Fable 5.1
## Pricing
For pricing information, see the [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/) page.
## Programmatic Access
| **Endpoint** | **Model ID** | **In-Region endpoint URL** |
| --- | --- | --- |
| bedrock-runtime | anthropic.claude-fable-5-1 | N/A |
## Other model
| bedrock-runtime | anthropic.claude-fable-5 | $999 |"""
        prices, _ = self.parse("bedrock_fable", body)
        self.assertEqual([p["billing_item"] for p in prices], ["input", "output"])
        self.assertTrue(all(not p["amount"] and "separate Bedrock pricing page" in p["unknown_reason"] for p in prices))
        self.assertEqual(self.parse("bedrock_fable", body.replace(b"anthropic.claude-fable-5-1", b"anthropic.claude-fable-5"))[0], [])
        self.assertEqual(self.parse("bedrock_fable", body.replace(b"# Claude Fable 5.1", b"# Claude Fable 5"))[0], [])

    def test_nine_new_models_direct_tables_are_exact_and_isolated(self):
        for model, title, slug in STANDARD_LLM_MODELS:
            provider = "openai" if model.startswith("gpt-") else "anthropic"
            parser = f"{provider}_{model.replace('-', '_').replace('.', '_')}"
            with self.subTest(model=model):
                if provider == "openai":
                    cache_write = "" if model.startswith("gpt-5.6") else "| Cache writes | $2.5 | 1M tokens |\n"
                    note = "- GPT-5.6 Sol’s promotional pricing is available at least through November 21, 2026.\n" if model == "gpt-5.6-sol" else ""
                    body = f"# {title}\nModel ID: `{model}`\n## Pricing\n### Text tokens\n| Metric | Price | Unit |\n| --- | ---: | --- |\n| Input | $2 | 1M tokens |\n| Cached input | $0.2 | 1M tokens |\n{cache_write}| Output | $10 | 1M tokens |\n{note}- Prompts with >272K input tokens are priced at 2x input and 1.5x output for the full request.\n## Next model\n| Input | $999 | 1M tokens |".encode()
                    prices, conditions = self.parse(parser, body)
                    self.assertEqual([p["billing_item"] for p in prices], ["input", "cache_read"] + ([] if not cache_write else ["cache_write"]) + ["output"])
                    self.assertTrue(all(p["provider_model_id"] == model and p["amount"] != "999" for p in prices))
                    self.assertEqual(self.parse(parser, body.replace(f"Model ID: `{model}`".encode(), b"Model ID: `wrong-model`"))[0], [])
                    self.assertFalse(any(p["billing_item"] == "input" for p in self.parse(parser, body.replace(b"| Input | $2 | 1M tokens |", b"| Input | $2 | 1K tokens |"))[0]))
                    if model == "gpt-5.6-sol":
                        self.assertEqual({c["condition_key"] for c in conditions if c["condition_key"] == "promotion_note"}, {"promotion_note"})
                        self.assertTrue(all(not p["effective_until"] for p in prices))
                else:
                    body = f"## Model pricing\n| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |\n| --- | --- | --- | --- | --- | --- |\n| Other model | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok |\n| {title} | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |\n## Batch processing\n| {title} | $2 / MTok | $10 / MTok |".encode()
                    prices, conditions = self.parse(parser, body)
                    self.assertEqual([(p["billing_item"], p["amount"]) for p in prices], [("input", "4"), ("cache_write", "5"), ("cache_write", "8"), ("cache_read", "0.20"), ("output", "20")])
                    self.assertEqual({c["value"] for c in conditions if c["condition_key"] == "cache_duration"}, {"5m", "1h"})
                    self.assertEqual(self.parse(parser, body.replace(f"| {title} |".encode(), b"| Wrong model |"))[0], [])
                    self.assertFalse(any(p["billing_item"] == "input" for p in self.parse(parser, body.replace(b"$4 / MTok", b"$4 / 1K tokens"))[0]))

    def test_nine_new_broker_pages_use_exact_heading_slug_and_promotion(self):
        for model, title, slug in STANDARD_LLM_MODELS:
            provider = "openai" if model.startswith("gpt-") else "anthropic"
            key = model.replace("-", "_").replace(".", "_")
            with self.subTest(model=model):
                promo = "50% off " if model == "gpt-5.6-sol" else ""
                h1 = f"{'OpenAI' if provider == 'openai' else 'Anthropic'}: {title}"
                openrouter = f"<h1>{h1}</h1><h3>{provider} /{slug}</h3><p>In / Out Price {promo}$2 / $10 per 1M</p><h2>Other model</h2><p>In / Out Price $999 / $999 per 1M</p>".encode()
                prices, conditions = self.parse("openrouter_" + key, openrouter)
                self.assertEqual([p["amount"] for p in prices], ["2", "10"])
                self.assertEqual([c["value"] for c in conditions if c["condition_key"] == "promotion_label"], ["50% off"] if promo else [])
                self.assertEqual(self.parse("openrouter_" + key, openrouter.replace(h1.encode(), b"Neighbor model"))[0], [])
                self.assertEqual(self.parse("openrouter_" + key, openrouter.replace(slug.encode(), b"wrong-model"))[0], [])
                vercel_title = title.replace("GPT-5.6", "GPT 5.6")
                vercel = f"# {vercel_title}\n- **Model ID:** `{provider}/{slug}`\n- **Pricing:** $2/1M input tokens, $10/1M output tokens\n## Next\n- **Pricing:** $999/1M input tokens, $999/1M output tokens".encode()
                prices, _ = self.parse("vercel_" + key, vercel)
                self.assertEqual([p["amount"] for p in prices], ["2", "10"])
                prices, _ = self.parse("vercel_" + key, vercel.split(b"## Next")[0])
                self.assertEqual([p["amount"] for p in prices], ["2", "10"])
                self.assertEqual(self.parse("vercel_" + key, vercel.split(b"## Next")[0] + b"- **Pricing:** $11/1M input tokens, $51/1M output tokens")[0], [])
                self.assertEqual(self.parse("vercel_" + key, vercel.split(b"## Next")[0].replace(vercel_title.encode(), b"Neighbor model"))[0], [])
                self.assertEqual(self.parse("vercel_" + key, vercel.split(b"## Next")[0].replace(slug.encode(), b"wrong-model"))[0], [])


if __name__ == "__main__":
    unittest.main()
