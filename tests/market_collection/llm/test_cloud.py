"""Cloud cards are evidence-scoped; these fixtures exercise semantic cells."""

import unittest

from scripts.market_collection.adapters import llm
from scripts.market_collection.sources import cloud_source_specs
from scripts.market_collection.core import _validate_parse, validate_registry


def aws_openai_card(model="gpt-6-astra", title="GPT-6 Astra", *, header="Input — 30m cache write"):
    def table(input_amount):
        return f"""<table><tr><th>Inference option</th><th>Input</th><th>{header}</th><th>Input — cache read</th><th>Output</th></tr>
<tr><td>In-Region</td><td>${input_amount}</td><td>$13.75</td><td>$1.10</td><td>$55.00</td></tr>
<tr><td>US Geo CRIS</td><td>$10.00</td><td>$12.50</td><td>$1.00</td><td>$50.00</td></tr>
<tr><td>Global CRIS</td><td>$9.00</td><td>$11.25</td><td>$0.90</td><td>$45.00</td></tr></table>"""
    return f"""<h1>{title}</h1><h2>Pricing</h2><p>All prices are in USD per 1 million tokens. The following tables list Standard and Ultrafast prices.</p>
<h3>Standard — Commercial Regions, short context (272K input tokens or fewer)</h3>{table('11.00')}
<h3>Standard — Commercial Regions, long context (more than 272K input tokens)</h3>{table('22.00')}
<h3>Ultrafast — Commercial Regions, short context (272K input tokens or fewer)</h3>{table('999.00')}
<h3>AWS GovCloud (US-East and US-West)</h3>{table('888.00')}
<h2>Call the model</h2><table><tr><th>Endpoint</th><th>Model ID</th><th>In-Region endpoint URL</th><th>Geo inference ID</th><th>Global inference ID</th></tr>
<tr><td>bedrock-mantle</td><td>openai.{model}</td><td>URL</td><td>N/A</td><td>N/A</td></tr></table>
<h2>Neighbor</h2><p>Input $777</p>""".encode()


def aws_anthropic_card(model="claude-opus-5-5", title="Claude Opus 5.5", *, provider_id=None):
    provider_id = provider_id or f"anthropic.{model}"
    return f"""<h1>{title}</h1><h2>Pricing</h2><p>This model is a third-party model offered and billed through AWS Marketplace. Charges appear on your AWS bill.</p>
<p>For pricing, see <a href="https://aws.amazon.com/bedrock/pricing/">Amazon Bedrock Pricing</a>.</p>
<h2>Programmatic Access</h2><table><tr><th>Endpoint</th><th>Model ID</th><th>In-Region endpoint URL</th><th>Geo inference ID</th><th>Global inference ID</th></tr>
<tr><td>bedrock-runtime</td><td>{provider_id}</td><td>N/A</td><td>us.{provider_id}</td><td>global.{provider_id}</td></tr></table>""".encode()


def azure_card(model="gpt-6-sol", link="https://aka.ms/AzureOAIpricing"):
    return f"""<h1>{model}</h1><h2>Pricing</h2><h2>Quick facts</h2>
<dl><dt>Publisher</dt><dd>OpenAI</dd><dt>Hosted on</dt><dd>Azure</dd></dl>
<a href="{link}">View pricing</a><h2>Related models</h2><p>Neighbor $999 per 1M</p>""".encode()


class CloudAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specs = {spec.model_id + ":" + spec.route: spec for spec in cloud_source_specs()}

    def parse(self, model, route, body):
        spec = self.specs[model + ":" + route]
        rows = llm.parse_source(spec, body, "source-1")
        _validate_parse(spec, "source-1", *rows)
        return rows

    def test_registry_exact_21(self):
        specs = cloud_source_specs()
        validate_registry(specs)
        self.assertEqual(len(specs), 21)
        self.assertEqual(len({s.source_key for s in specs}), 21)
        self.assertEqual(len({s.parser for s in specs}), 21)
        self.assertEqual({s.route for s in specs}, {"aws_bedrock", "azure_foundry"})
        self.assertTrue(all(s.modality == "llm" and s.task == "text_generation" for s in specs))
        self.assertNotIn("claude-fable-5-1:aws_bedrock", self.specs)
        self.assertIn("claude-fable-5-1:azure_foundry", self.specs)

    def test_aws_standard_semantic_cells_and_boundaries(self):
        prices, conditions = self.parse("gpt-6-astra", "aws_bedrock", aws_openai_card())
        self.assertEqual(len(prices), 24)
        self.assertEqual({p["unit"] for p in prices}, {"1M tokens"})
        self.assertEqual({p["provider_model_id"] for p in prices}, {"openai.gpt-6-astra"})
        self.assertFalse(any(p["amount"] in {"999.00", "888.00", "777"} for p in prices))
        first = prices[:4]
        self.assertEqual([(p["billing_item"], p["amount"]) for p in first],
                         [("input", "11.00"), ("cache_write", "13.75"), ("cache_read", "1.10"), ("output", "55.00")])
        self.assertTrue(all("Standard" in p["locator"] and "Input" in p["locator"] or p["billing_item"] != "input" for p in prices))
        self.assertEqual({c["value"] for c in conditions if c["condition_key"] == "context_band"},
                         {"272K input tokens or fewer", "more than 272K input tokens"})
        self.assertEqual({c["value"] for c in conditions if c["condition_key"] == "cache_write_ttl"}, {"30m"})
        self.assertEqual({c["value"] for c in conditions if c["condition_key"] == "inference_option"},
                         {"In-Region", "US Geo CRIS", "Global CRIS"})

    def test_aws_changed_header_unit_and_identity_fail_closed(self):
        valid = aws_openai_card()
        for bad in (valid.replace("Input — 30m cache write".encode(), b"Input - maybe cache write"),
                    valid.replace(b"USD per 1 million tokens", b"USD per 1 thousand tokens"),
                    valid.replace(b"<h1>GPT-6 Astra</h1>", b"<h1>GPT-6 Sol</h1>"),
                    valid.replace(b"openai.gpt-6-astra", b"openai.gpt-6-sol")):
            with self.subTest(body=bad[:80]):
                self.assertEqual(self.parse("gpt-6-astra", "aws_bedrock", bad), ([], []))
        missing_cell = valid.replace(b"<td>$13.75</td>", b"<td>Contact sales</td>")
        self.assertEqual(len(self.parse("gpt-6-astra", "aws_bedrock", missing_cell)[0]), 16)

    def test_aws_anthropic_unknown_and_haiku_version(self):
        prices, conditions = self.parse("claude-opus-5-5", "aws_bedrock", aws_anthropic_card())
        self.assertEqual([p["billing_item"] for p in prices], ["input", "output"])
        self.assertTrue(all(not p["amount"] and "separate Bedrock pricing page" in p["unknown_reason"] for p in prices))
        self.assertEqual([(c["condition_key"], c["value"]) for c in conditions], [("billing_channel", "AWS Marketplace")])
        bad = aws_anthropic_card().replace(b"anthropic.claude-opus-5-5", b"anthropic.claude-sonnet-5-5")
        self.assertEqual(self.parse("claude-opus-5-5", "aws_bedrock", bad), ([], []))
        bad = aws_anthropic_card().replace(b"https://aws.amazon.com/bedrock/pricing/", b"https://example.com/pricing")
        self.assertEqual(self.parse("claude-opus-5-5", "aws_bedrock", bad), ([], []))
        haiku = aws_anthropic_card("claude-haiku-4-5", "Claude Haiku 4.5", provider_id="N/A")
        haiku = haiku.replace(b"global.N/A", b"global.anthropic.claude-haiku-4-5-20251001-v1:0")
        prices, _ = self.parse("claude-haiku-4-5", "aws_bedrock", haiku)
        self.assertEqual({p["provider_model_id"] for p in prices}, {"global.anthropic.claude-haiku-4-5-20251001-v1:0"})

    def test_azure_catalog_only_unknown(self):
        prices, conditions = self.parse("gpt-6-sol", "azure_foundry", azure_card())
        self.assertEqual([p["billing_item"] for p in prices], ["input", "output"])
        self.assertTrue(all(p["amount"] == "" and "Azure-specific" in p["unknown_reason"] for p in prices))
        self.assertEqual({c["condition_key"] for c in conditions}, {"price_basis", "publisher", "hosting"})
        for body in (azure_card("gpt-6-luna"), azure_card().replace(b"<h2>Pricing</h2>", b"<h2>Costs</h2>"),
                     azure_card(link="https://example.com/pricing")):
            self.assertEqual(self.parse("gpt-6-sol", "azure_foundry", body), ([], []))


if __name__ == "__main__":
    unittest.main()
