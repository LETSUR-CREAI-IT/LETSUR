"""Regression checks for supplied missing LLM model pages."""

import unittest

from scripts.market_collection.adapters import llm
from scripts.market_collection.models import BROKER_CATALOG_MODELS
from scripts.market_collection.sources import broker_catalog_source_specs
from scripts.market_collection.core import _validate_parse, validate_registry


class MissingLlmAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specs = {spec.source_key: spec for spec in llm.source_specs()}

    def parse(self, key, body):
        result = llm.parse_source(self.specs[key], body, "source-1")
        _validate_parse(self.specs[key], "source-1", *result)
        return result

    def test_registry_adds_exactly_nineteen_model_scoped_sources(self):
        validate_registry(llm.source_specs())
        additions = broker_catalog_source_specs()
        self.assertEqual(len(additions), 19)
        self.assertEqual({spec.model_id for spec in additions}, {model[0] for model in BROKER_CATALOG_MODELS})
        self.assertEqual(sum(spec.route == "anthropic_direct" for spec in additions), 1)
        for model, _, _, openrouter_id, vercel_id in BROKER_CATALOG_MODELS:
            specs = [spec for spec in additions if spec.model_id == model]
            self.assertEqual({spec.route for spec in specs}, {"openrouter", "vercel_ai_gateway"} | ({"anthropic_direct"} if model == "claude-sonnet-5" else set()))
            self.assertIn(openrouter_id, next(spec.url for spec in specs if spec.route == "openrouter"))
            self.assertEqual(vercel_id, "zai/" + model if model.startswith("glm-") else vercel_id)

    def test_openrouter_requires_exact_h1_slug_and_one_per_million_quote(self):
        for model, _, openrouter_title, openrouter_id, _ in BROKER_CATALOG_MODELS:
            key = "openrouter_" + model.replace("-", "_").replace(".", "_")
            body = (f"<h1>{openrouter_title}</h1><h3>{openrouter_id.replace('/', ' /')}</h3>"
                    "<p>In / Out Price $2 / $10 per 1M</p><h2>Neighbor</h2>"
                    "<p>In / Out Price $999 / $999 per 1M</p>").encode()
            with self.subTest(model=model):
                prices, conditions = self.parse(key, body)
                self.assertEqual([row["amount"] for row in prices], ["2", "10"])
                self.assertEqual([row["provider_model_id"] for row in prices], [openrouter_id, openrouter_id])
                self.assertIn(("price_basis", "model_page_rate"), {(row["condition_key"], row["value"]) for row in conditions})
                self.assertEqual(self.parse(key, body.replace(openrouter_title.encode(), b"Adjacent model"))[0], [])
                self.assertEqual(self.parse(key, body.replace(openrouter_id.replace("/", " /").encode(), b"other /provider"))[0], [])
                self.assertEqual(self.parse(key, body.replace(b"per 1M", b"per 1K"))[0], [])

    def test_vercel_requires_exact_h1_provider_id_and_unit(self):
        for model, title, _, _, vercel_id in BROKER_CATALOG_MODELS:
            key = "vercel_" + model.replace("-", "_").replace(".", "_")
            body = (f"# {title}\n- **Model ID:** `{vercel_id}`\n"
                    "- **Pricing:** $2/1M input tokens, $10/1M output tokens\n"
                    "## Related model\n- **Pricing:** $999/1M input tokens, $999/1M output tokens").encode()
            with self.subTest(model=model):
                prices, conditions = self.parse(key, body)
                self.assertEqual([row["amount"] for row in prices], ["2", "10"])
                self.assertEqual([row["provider_model_id"] for row in prices], [vercel_id, vercel_id])
                self.assertEqual([(row["condition_key"], row["value"]) for row in conditions], [("price_basis", "listed_model_rate")])
                self.assertEqual(self.parse(key, body.replace(("# " + title).encode(), b"# Neighbor"))[0], [])
                self.assertEqual(self.parse(key, body.replace(vercel_id.encode(), b"other/model"))[0], [])
                self.assertEqual(self.parse(key, body.replace(b"/1M input", b"/1K input"))[0], [])

    def test_anthropic_sonnet_5_row_does_not_use_sonnet_55(self):
        body = b"""# Pricing
## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Sonnet 5.5 | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok |
| Claude Sonnet 5 | $2 / MTok<sup>3</sup> | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok<sup>3</sup> |
## Batch processing
| Model | Batch input | Batch output |
| --- | --- | --- |
| Claude Sonnet 5 | $1 / MTok | $5 / MTok |"""
        prices, conditions = self.parse("anthropic_claude_sonnet_5", body)
        self.assertEqual([(row["billing_item"], row["amount"]) for row in prices], [("input", "2"), ("cache_write", "2.50"), ("cache_write", "4"), ("cache_read", "0.20"), ("output", "10")])
        self.assertEqual({row["value"] for row in conditions if row["condition_key"] == "cache_duration"}, {"5m", "1h"})
        self.assertEqual(self.parse("anthropic_claude_sonnet_5", body.replace(b"| Claude Sonnet 5 |", b"| Claude Sonnet 5.4 |"))[0], [])


if __name__ == "__main__":
    unittest.main()
