"""A maker's "available today" is not by itself a first model release.

SOURCE-SHAPED SYNTHETIC fixtures in the shape of the registered openai_news RSS
feed, run through the real signals.live -> build path. Each case is its own feed
so one entry's release cannot confirm another's.
"""

from __future__ import annotations

import pytest

from scripts.market_collection import signals
from scripts.market_collection.models import ASTRA_MODEL

from test_catalog_and_diff import ok_build
from test_notice_auto import SPECS, of, rss

DATE = "Tue, 01 Sep 2026 17:00:00 GMT"


def events_for(tmp_path, title: str, text: str) -> dict:
    body = rss(("astra", DATE, title, text))
    run = signals.live(tmp_path / "data", lookback_days=3650, specs=[SPECS["openai_news"]],
                       fetcher=lambda spec: (body, spec.url, 200))
    return ok_build(tmp_path / "out", run)


def test_clear_new_model_release_is_confirmed(tmp_path):
    events = events_for(tmp_path, "GPT-6 Astra", "Today we release GPT-6 Astra, our new model, available today via the API.")
    [release] = of(events, "model_release", ASTRA_MODEL)
    assert release["status"] == "confirmed"
    assert release["dates"]["maker_first_release_date"] == "2026-09-01"


@pytest.mark.parametrize("text", [
    "GPT-6 Astra is available today in the API.",
    "GPT-6 Astra is now available today in the Batch API.",
    "GPT-6 Astra, our new model, is now available today in ChatGPT.",
    "GPT-6 Astra, our new model, is available today on the free tier.",
    "GPT-6 Astra, our new model, is available today in more regions.",
    "GPT-6 Astra, our new model, is not available today.",
    "We are not releasing GPT-6 Astra, our new model, today.",
    "The launch of GPT-6 Astra, our new model, planned for today has been postponed.",
])
def test_availability_without_first_release_is_not_a_confirmed_release(tmp_path, text):
    events = events_for(tmp_path, "API update", text)
    assert not [e for e in of(events, "model_release", ASTRA_MODEL) if e["status"] == "confirmed"]
