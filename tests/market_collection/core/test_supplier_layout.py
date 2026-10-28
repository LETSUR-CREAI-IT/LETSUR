"""Fixed supplier routing and modality facade contracts."""

from dataclasses import replace

import pytest

from scripts.market_collection.adapters import image, llm, video
from scripts.market_collection.collectors import SUPPLIERS, parse_source
from scripts.market_collection.core import registry
from scripts.market_collection.models import MODEL_MODALITIES


def test_registered_model_modality_and_fixed_suppliers():
    specs, adapters = registry()
    assert len(specs) == 386
    assert len(MODEL_MODALITIES) == 123
    assert all(MODEL_MODALITIES[spec.model_id] == spec.modality for spec in specs)
    assert {spec.route for spec in specs} == set(SUPPLIERS)
    assert set(adapters) == {"llm", "image", "video"}


def test_exact_spec_and_modality_facade_rejection():
    by_modality = {name: next(spec for spec in registry()[0] if spec.modality == name)
                   for name in ("llm", "image", "video")}
    for facade, foreign in ((llm, by_modality["image"]),
                            (image, by_modality["video"]),
                            (video, by_modality["llm"])):
        with pytest.raises(ValueError):
            facade.parse_source(foreign, b"", "source-1")
    altered = replace(by_modality["llm"], route="unregistered-route")
    with pytest.raises(ValueError, match="fixed registry"):
        parse_source(altered, b"", "source-1")
