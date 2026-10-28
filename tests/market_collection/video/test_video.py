"""Source-bound video extraction regressions."""

import unittest
import csv
import tempfile
from pathlib import Path

from scripts.market_collection.adapters import video
from scripts.market_collection.models import BYTEPLUS_ID
from scripts.market_collection.core import _validate_parse, live
from tests.market_collection.byteplus_page import BYTEPLUS, FORMULA, WITH_VIDEO, byteplus_page


FAL = b'''# bytedance/seedance-2.5/text-to-video
Text to Video
For 480p, your request will cost roughly $0.2205 per second of generated video, for 720p, you will be charged roughly $0.4730 per second of generated video, and for 1080p, roughly $1.164 per second of generated video. Your request will cost $0.0214 per 1000 tokens for 480p and 720p video and roughly $0.0234 per 1000 tokens for 1080p.
tokens = (output_height * output_width * duration_seconds * 24) / 1024
Per-second figures are approximations for the common 16:9 case.
| `resolution` | string | `"720p"` | `"480p"` or `"720p"` |
| `duration` | string | `"auto"` | `"auto"`, or `"4"` through `"30"` |
| `aspect_ratio` | string | `"auto"` | `"auto"`, `"21:9"`, `"16:9"`, `"9:16"` |
| `generate_audio` | boolean | `true` | Generate synchronized audio. |
'''

CAMPAIGN = b'''Limited-time discount campaign rules for Seedance models
Eligible models: Seedance 2.0 mini and Seedance 2.0 fast.
Seedance 2.0 mini: 60% off the list price. Enterprise users only; token usage cap applies.
'''


class VideoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specs = {spec.source_key: spec for spec in video.source_specs()}

    def parse(self, key, body):
        spec = self.specs[key]
        result = video.parse_source(spec, body, "s1")
        _validate_parse(spec, "s1", *result)
        return result

    @staticmethod
    def scope(conditions, group):
        return {c["condition_key"]: c["value"] for c in conditions if c["condition_set_id"] == f"s1:byteplus_{group}"}

    def test_registry_routes_and_identifiers(self):
        self.assertEqual(len(self.specs), 4)
        self.assertEqual({s.route for s in self.specs.values()}, {"fal", "BytePlus ModelArk", "vercel_ai_gateway"})
        self.assertEqual({s.task for s in self.specs.values()}, {"text-to-video"})

    def test_veo_is_not_collected(self):
        # Google ended the Gemini API Veo 3.1 preview on 2026-10-22; LETSUR's model list has no Veo (2026-10-28).
        self.assertEqual({spec.model_id for spec in self.specs.values()}, {"seedance-2.5"})

    def test_fal_rate_estimates_and_conflict(self):
        prices, conditions = self.parse("fal_seedance_25_t2v", FAL)
        self.assertEqual({(p["price_kind"], p["amount"], p["unit"]) for p in prices}, {
            ("published_rate", "0.0214", "1000 tokens"),
            ("provider_estimate", "0.0234", "1000 tokens"),
            ("provider_estimate", "0.2205", "1 second"),
            ("provider_estimate", "0.4730", "1 second"),
            ("provider_estimate", "1.164", "1 second"),
        })
        self.assertIn("1080p_availability", {c["condition_key"] for c in conditions})
        self.assertIn("token_formula", {c["condition_key"] for c in conditions})
        self.assertTrue({"resolution_schema", "duration", "aspect_ratio", "generate_audio"} <= {c["condition_key"] for c in conditions})
        self.assertEqual(next(c["status"] for c in conditions if c["condition_key"] == "1080p_availability"), "conflicted")
        changed, _ = self.parse("fal_seedance_25_t2v", FAL.replace(b"$0.0214", b"$0.0314"))
        self.assertIn("0.0314", {p["amount"] for p in changed})
        self.assertNotIn("0.0214", {p["amount"] for p in changed})
        with self.assertRaisesRegex(ValueError, "token rate or unit missing"):
            self.parse("fal_seedance_25_t2v", FAL.replace(b"per 1000 tokens for 480p", b"per video for 480p"))

    def test_fal_token_rate_accepts_thousands_separator(self):
        # fal's page now writes "per 1,000 tokens"; the rates and the unit are unchanged.
        separated = FAL.replace(b"per 1000 tokens", b"per 1,000 tokens")
        self.assertNotEqual(separated, FAL)
        prices, conditions = self.parse("fal_seedance_25_t2v", separated)
        resolution = {c["condition_set_id"]: c["value"] for c in conditions if c["condition_key"] == "resolution"}
        self.assertEqual({(resolution[p["condition_set_id"]], p["price_kind"], p["amount"], p["unit"]) for p in prices if p["unit"] == "1000 tokens"}, {
            ("480p/720p", "published_rate", "0.0214", "1000 tokens"),
            ("1080p", "provider_estimate", "0.0234", "1000 tokens"),
        })
        # Only the separator is tolerated: a per-million rate must not be read as per 1000 tokens.
        with self.assertRaisesRegex(ValueError, "token rate or unit missing"):
            self.parse("fal_seedance_25_t2v", FAL.replace(b"per 1000 tokens", b"per 1,000,000 tokens"))

    def test_byteplus_model_unit_scope_and_formula(self):
        prices, conditions = self.parse("byteplus_seedance_25_t2v", BYTEPLUS)
        # Only the "Input without video" tab: 6.40 and 7.0 in the "Input with video" tab are a different price.
        self.assertEqual([(p["amount"], p["unit"]) for p in prices], [("10.70", "1M tokens"), ("11.7", "1M tokens")])
        self.assertTrue(all(p["provider_model_id"] == BYTEPLUS_ID for p in prices))
        self.assertEqual(self.scope(conditions, "480p_720p"), {"resolution": "480p/720p", "input_video": "none", "inference_mode": "online"})
        self.assertEqual(self.scope(conditions, "1080p"), {"resolution": "1080p", "input_video": "none", "inference_mode": "online"})
        self.assertEqual(self.scope(conditions, "formula"), {
            "estimated_token_formula": FORMULA,
            "actual_usage": "usage.completion_tokens",
            "seedance_25_discount_eligibility": "unconfirmed: promotion names Seedance 2.0 mini/fast",
        })
        changed, _ = self.parse("byteplus_seedance_25_t2v", BYTEPLUS.replace(b"$10.70", b"$12.70"))
        self.assertEqual(changed[0]["amount"], "12.70")
        _, no_formula_conditions = self.parse("byteplus_seedance_25_t2v", BYTEPLUS.replace(b"Estimated token consumption:", b"Token count unknown:"))
        self.assertTrue(any(c["condition_key"] == "estimated_token_formula" and c["status"] == "unknown" for c in no_formula_conditions))

    def test_byteplus_unit_is_read_from_the_page_never_assumed(self):
        with self.assertRaisesRegex(ValueError, "unit missing"):
            self.parse("byteplus_seedance_25_t2v", BYTEPLUS.replace(b"USD per million tokens", b"tokens"))

    def test_byteplus_model_row_must_be_in_the_input_without_video_table(self):
        with self.assertRaisesRegex(ValueError, "model row"):
            self.parse("byteplus_seedance_25_t2v", BYTEPLUS.replace(b"dreamina-seedance-2-5-260628", b"dreamina-seedance-2-0-260128"))
        with self.assertRaisesRegex(ValueError, "model row"):
            self.parse("byteplus_seedance_25_t2v", byteplus_page([("Input with video", WITH_VIDEO)]))

    def test_byteplus_rates_that_are_not_plain_dollar_amounts_are_not_guessed(self):
        for cells in ((b"Not supported", b"Not supported"), (b"$10.70 per image", b"$11.7 per image")):
            with self.subTest(cells=cells), self.assertRaisesRegex(ValueError, "rate missing"):
                self.parse("byteplus_seedance_25_t2v", BYTEPLUS.replace(b"$10.70", cells[0]).replace(b"$11.7", cells[1]))

    def test_campaign_does_not_confirm_seedance_25_discount(self):
        prices, conditions = self.parse("byteplus_seedance_promotion_rules", CAMPAIGN)
        self.assertEqual(len(prices), 1)
        self.assertEqual((prices[0]["price_kind"], prices[0]["amount"], prices[0]["evidence_status"]), ("disputed_claim", "", "conflicted"))
        self.assertTrue(any(c["status"] == "conflicted" for c in conditions))
        with self.assertRaisesRegex(ValueError, "campaign"):
            self.parse("byteplus_seedance_promotion_rules", CAMPAIGN.replace(b"60% off", b"offer"))

    def test_js_shell_is_parse_error_with_raw_preserved(self):
        spec = self.specs["byteplus_seedance_25_t2v"]
        shell = b"<html><body>You need to enable JavaScript to run this app. BytePlus</body></html>"
        with tempfile.TemporaryDirectory() as directory:
            run_dir = live(Path(directory), specs=[spec], adapters={"video": video}, fetcher=lambda _: (shell, spec.url, 200))
            with (run_dir / "sources.csv").open(newline="", encoding="utf-8") as stream:
                source = next(csv.DictReader(stream))
            with (run_dir / "price_observations.csv").open(newline="", encoding="utf-8") as stream:
                prices = list(csv.DictReader(stream))
            self.assertEqual(source["fetch_status"], "ok")
            self.assertEqual(source["parse_status"], "error")
            self.assertIn("model row or USD/M token unit missing", source["error"])
            self.assertEqual((run_dir / source["raw_path"]).read_bytes(), shell)
            self.assertEqual(prices, [])


if __name__ == "__main__":
    unittest.main()
