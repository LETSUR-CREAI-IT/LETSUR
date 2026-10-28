"""fal model pages in their Markdown form (fal.ai/models/<endpoint>/llms.txt): the Pricing section of one endpoint.

fal writes each model family's price as its own English sentence, so every registered endpoint names the wording it
must match in full; a changed, added or missing sentence fails closed instead of being read in part.
"""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from .common import _section
from .video_common import conditions, price

PARSERS = ("fal_llms",)
URL = re.compile(r"https://fal\.ai/models/(.+)/llms\.txt")
USD = r"\*\*\$([0-9]+(?:\.[0-9]+)?)\*\*"
RES = r"\*\*([0-9]+p)\*\*"
EXAMPLE = r" For example, a [0-9]+s video(?: with [a-z ]+)? will cost \*\*\$[0-9]+(?:\.[0-9]+)?\*\*\.?"
MORE = "For more details, see [fal.ai pricing](https://fal.ai/pricing)."
FAMILIES = {
    "kling_v3": ("Kling AI", "3.0", re.compile(
        rf"For every second of video you generated, you will be charged {USD} \(audio off\) or {USD} \(audio on\), "
        rf"if voice control is used while generating audio you will be charged {USD}\.{EXAMPLE}")),
    "kling_o3": ("Kling AI", "3.0", re.compile(
        rf"For every second of video you generated, you will be charged {USD} \(audio off\) or {USD} \(audio on\)\.{EXAMPLE}")),
    # fal writes the Pro page of this one without its comma and period.
    "kling_o3_video": ("Kling AI", "3.0", re.compile(rf"For every second of video you generated,? you will be charged {USD}\.?{EXAMPLE}")),
    "kling_v4": ("Kling AI", "4.0", re.compile(rf"Video costs {USD} per second at {RES} and {USD} per second at {RES}\.")),
    "kling_v4_reference": ("Kling AI", "4.0", re.compile(
        rf"Video costs {USD} per second at {RES} and {USD} per second at {RES}\. With video references or video elements, "
        rf"it costs {USD} per second at {RES} and {USD} per second at {RES}, billed on the ([^.]+)\.")),
    "seedance_reference": ("ByteDance", "2.5", re.compile(
        rf"For 480p, your request will cost roughly {USD} per second of generated video, for 720p, you will be charged roughly {USD} "
        rf"per second of generated video, and for 1080p, roughly {USD} per second of generated video\. Your request will cost {USD} "
        rf"per 1,?000 tokens for 480p and 720p video and roughly {USD} per 1,?000 tokens for 1080p video\. The number of tokens is "
        rf"roughly given by (\(.+\) / 1024)\. If video inputs are provided the price is multiplied by ([0-9]+(?:\.[0-9]+)?)\. With "
        rf"video inputs and 720p resolution, the price is roughly {USD} per second of generated video\. (With video references, "
        rf"you will be charged for both input and output videos)\.")),
}
# Each registered endpoint: its Pricing wording and what the endpoint itself fixes (fal's mode, and whether it takes a video).
ENDPOINTS = {
    "fal-ai/kling-video/v3/standard/text-to-video": ("kling_v3", {"mode": "standard"}),
    "fal-ai/kling-video/v3/pro/text-to-video": ("kling_v3", {"mode": "pro"}),
    "fal-ai/kling-video/v3/standard/image-to-video": ("kling_v3", {"mode": "standard"}),
    "fal-ai/kling-video/v3/pro/image-to-video": ("kling_v3", {"mode": "pro"}),
    "fal-ai/kling-video/o3/standard/reference-to-video": ("kling_o3", {"mode": "standard", "input_video": "none"}),
    "fal-ai/kling-video/o3/pro/reference-to-video": ("kling_o3", {"mode": "pro", "input_video": "none"}),
    "fal-ai/kling-video/o3/standard/video-to-video/reference": ("kling_o3_video", {"mode": "standard", "input_video": "yes"}),
    "fal-ai/kling-video/o3/pro/video-to-video/reference": ("kling_o3_video", {"mode": "pro", "input_video": "yes"}),
    "kling/v4/text-to-video": ("kling_v4", {}),
    "kling/v4/image-to-video": ("kling_v4", {}),
    "kling/v4/reference-to-video": ("kling_v4_reference", {}),
    "bytedance/seedance-2.5/reference-to-video": ("seedance_reference", {}),
}
PARAM = re.compile(r"^- \*\*`(\w+)`\*\* \(`[^`]*`, _(required|optional)_\)", re.M)


def _rows(family: str, m: re.Match) -> list[tuple[str, str, str, dict]]:
    """(amount, unit, price kind, stated conditions) of each rate the sentence gives."""
    def rate(amount: str, unit: str = "1 second", kind: str = "published_rate", **stated: str) -> tuple[str, str, str, dict]:
        return amount, unit, kind, stated
    if family == "kling_v3":
        return [rate(m[1], audio="off"), rate(m[2], audio="on", voice_control="off"), rate(m[3], audio="on", voice_control="on")]
    if family == "kling_o3":
        return [rate(m[1], audio="off"), rate(m[2], audio="on")]
    if family == "kling_o3_video":
        return [rate(m[1])]
    if family == "kling_v4":
        return [rate(m[1], resolution=m[2]), rate(m[3], resolution=m[4])]
    if family == "kling_v4_reference":
        return [rate(m[1], resolution=m[2], input_video="none"), rate(m[3], resolution=m[4], input_video="none"),
                rate(m[5], resolution=m[6], input_video="yes", billed_duration=m[9]), rate(m[7], resolution=m[8], input_video="yes", billed_duration=m[9])]
    # Seedance: the stated rates are without video input; fal's multiplier for video input is kept as a term, never applied.
    roughly = "provider_estimate"
    return [rate(m[1], kind=roughly, resolution="480p", input_video="none"), rate(m[2], kind=roughly, resolution="720p", input_video="none"),
            rate(m[3], kind=roughly, resolution="1080p", input_video="none"), rate(m[4], "1000 tokens", resolution="480p/720p", input_video="none"),
            rate(m[5], "1000 tokens", roughly, resolution="1080p", input_video="none"), rate(m[8], kind=roughly, resolution="720p", input_video="yes")]


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    url = URL.fullmatch(spec.url)
    if spec.parser not in PARSERS or not url or url[1] not in ENDPOINTS:
        raise ValueError("unregistered fal endpoint")
    endpoint = url[1]
    family, fixed = ENDPOINTS[endpoint]
    upstream, version, wording = FAMILIES[family]
    text = body.decode("utf-8")
    if re.findall(r"^- \*\*Model ID\*\*: `([^`]+)`\s*$", text, re.M) != [endpoint]:
        raise ValueError(f"fal page is not for the exact endpoint {endpoint}")
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", _section(text, "Pricing", 2))]
    sentence = " ".join(part for part in paragraphs if part and part != MORE)
    m = wording.fullmatch(sentence)
    if not m:
        raise ValueError(f"fal {endpoint} Pricing section absent or worded differently")
    rows = _rows(family, m)
    keys = [(unit, tuple(sorted(conds.items()))) for _, unit, _, conds in rows]
    if len(set(keys)) != len(keys) or any(set(conds) & set(fixed) for *_, conds in rows):
        raise ValueError(f"fal {endpoint} states two rates for one condition")
    # The endpoint's own input schema must agree with the video input its rates are stated for.
    stated = frozenset(conds.get("input_video", fixed.get("input_video")) for *_, conds in rows) - {None}
    if stated:
        inputs = dict(PARAM.findall(_section(text, "Input Schema", 3)))
        videos = [need for name, need in inputs.items() if name in ("video_url", "video_urls")]
        takes = "required" if "required" in videos else "optional" if videos else "no"
        if not inputs or takes != {frozenset({"none"}): "no", frozenset({"yes"}): "required"}.get(stated, "optional"):
            raise ValueError(f"fal {endpoint} input schema contradicts its video-input price")
    prices: list[dict] = []
    own: list[dict] = []
    locator = f"{endpoint} llms.txt > Pricing: {sentence}"
    for number, (amount, unit, kind, conds) in enumerate(rows):
        group = f"{source_id}:fal_{number}"
        row = price(spec, endpoint, upstream, amount, unit, group, locator, version=version)
        row["price_kind"] = kind
        prices.append(row)
        own.extend(conditions(group, locator, **fixed, **conds))
    if family == "seedance_reference":
        own.extend(conditions(f"{source_id}:fal_terms", locator, token_formula=m[6], video_input_multiplier=m[7], video_reference_billing=m[9]))
    return prices, own
