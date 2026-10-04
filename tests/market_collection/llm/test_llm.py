"""Source-scoped LLM extraction checks; fixtures contain no network data."""

import unittest

from scripts.market_collection.adapters import llm
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
        self.assertEqual(set(self.specs), {"google", "openrouter", "vercel"})
        self.assertEqual(len({spec.route for spec in self.specs.values()}), 3)
        self.assertTrue(all(spec.model_id == "gemini-3.8-flash" for spec in self.specs.values()))

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


if __name__ == "__main__":
    unittest.main()
