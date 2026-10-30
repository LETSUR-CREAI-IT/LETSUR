"""Retained public HTML exercises scope; mutations must not borrow neighbors."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
import pytest
from scripts.market_collection.sources import source_specs
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.core import _validate_parse
from tests.market_collection.retired_sources import without_retired

F = Path(__file__).parent / 'fixtures' / 'image25_supplement'


def parse(kind, variant='sunburst', body=None):
    s = next(s for s in source_specs() if s.source_key == f'image25_{kind}_{variant}_doc')
    filenames = {'openai_generate':'openai_image_generate','azure_howto':'azure_image_howto',
                 'azure_regions':'azure_model_regions','openrouter_guide':'openrouter_image_guide',
                 'vercel_ui':f'vercel_{variant}_model','fal_guide':'fal_image25_guide'}
    b = body if body is not None else (F / (filenames[kind]+'.html')).read_bytes()
    prices, rows = _validate_parse(s, 'doc-source', *parse_source(s, b, 'doc-source'))
    assert prices == []
    return {r['condition_key']:r for r in rows}


def val(f, key):
    try: return json.loads(f[key]['value'])
    except ValueError: return f[key]['value']


def test_ordered_registry_append_and_raw_provenance():
    old = without_retired(json.loads((F/'registry113.json').read_text()))
    specs = source_specs()
    assert len(specs) == 415
    assert [asdict(s) for s in specs[:len(old)]] == old
    for p in json.loads((F/'provenance.json').read_text()):
        assert hashlib.sha256((F/p['fixture']).read_bytes()).hexdigest() == p['raw_sha256'] == p['fixture_sha256']


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
def test_exact_openai_reference_range(variant):
    f = parse('openai_generate', variant)
    assert val(f,'profile_scope') == 'manufacturer_supplement'
    assert val(f,'num_images_range') == {'min':1,'max':10}
    assert val(f,'model_id') == 'gpt-image-2.5-'+variant


@pytest.mark.parametrize('change', ['old_model', 'n_removed', 'n_malformed', 'n_neighbor'])
def test_reference_does_not_borrow_old_or_neighbor_n(change):
    b = (F/'openai_image_generate.html').read_bytes()
    if change == 'old_model': b = b.replace(b'gpt-image-2.5-sunburst', b'gpt-image-1.5')
    if change == 'n_removed': b = b.replace(b'(param) n &gt; (schema)', b'(param) missing &gt; (schema)')
    if change == 'n_malformed': b = b.replace(b'Must be between 1 and 10.', b'Must be between unknown and 10.')
    if change == 'n_neighbor': b = b.replace(b'The number of images to generate.', b'The number of partial images to generate.')
    with pytest.raises(ValueError): parse('openai_generate', body=b)


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
def test_azure_exact_column_contract(variant):
    f = parse('azure_howto', variant)
    assert val(f,'profile_scope') == 'channel_model'
    assert val(f,'quality_options') == ['low','medium','high','xhigh','max','auto']
    assert val(f,'num_images_range') == {'min':1,'max':10}
    assert val(f,'size_rules') == {'multiple':16,'max_edge':3840,'min_pixels':655360,'max_pixels':8294400,'max_aspect_ratio':3}
    assert val(f,'applicable_tasks') == ['text-to-image','image-edit']
    assert val(f,'input_modalities') == ['text','image']
    assert val(f,'api_surface') == ['images']
    for key in ('processing_options','model_snapshot','region_deployment_eligibility','account_eligibility'):
        assert f[key]['status'] == 'unknown'


@pytest.mark.parametrize('change',['identity','duplicate_header','missing_quality','malformed_count','wrong_modality','missing_size_rule'])
def test_azure_malformed_exact_contract_fails(change):
    b = (F/'azure_image_howto.html').read_bytes()
    if change == 'identity': b = b.replace(b'GPT-Image-2.5-Sunburst', b'GPT-Image-OLD')
    if change == 'duplicate_header': b = b.replace(b'GPT-Image-2.5-Flare', b'GPT-Image-2.5-Sunburst')
    if change == 'missing_quality': b = b.replace(b'Quality Options', b'Quality removed')
    if change == 'malformed_count': b = b.replace('1–10 images per request'.encode(), b'unknown count')
    if change == 'wrong_modality': b = b.replace(b'outputs images.', b'outputs text.')
    if change == 'missing_size_rule': b = b.replace(b'3,840 px', b'unknown px')
    with pytest.raises(ValueError): parse('azure_howto', body=b)


def test_azure_selected_new_quality_does_not_borrow_neighbor_column():
    b = (F/'azure_image_howto.html').read_bytes()
    # Only the first model column receives a changed enum; Flare must stay intact.
    b = b.replace(b'<code>xhigh</code>', b'<code>newwrongquality</code>', 1)
    assert 'newwrongquality' in val(parse('azure_howto',body=b),'quality_options')
    assert 'newwrongquality' not in val(parse('azure_howto','flare',b),'quality_options')


def test_regions_reference_does_not_establish_account_deployment():
    f = parse('azure_regions')
    assert val(f,'profile_scope') == 'reference'
    assert val(f,'exact_model_row_found') is False
    assert f['region_deployment_eligibility']['status'] == 'unknown'


def test_generic_guide_remains_documentary():
    f = parse('openrouter_guide')
    assert val(f,'profile_scope') == 'generic_api_reference'
    assert val(f,'size_alias_semantics')['explicit_pixel_size'] == 'authoritative when supported'
    assert 'size_presets' not in f and 'size_rules' not in f
    assert f['exact_pixel_support']['status'] == 'unknown'


@pytest.mark.parametrize('variant', ['sunburst','flare'])
def test_visible_ui_select_choices_only(variant):
    f = parse('vercel_ui',variant)
    assert val(f,'profile_scope') == 'channel_ui'
    assert val(f,'evidence_kind') == 'official_ui_choices'
    assert val(f,'model_page_ui_choices')['n'] == [1,2,3,4]
    assert '1024x1024' in val(f,'model_page_ui_choices')['size']
    assert val(f,'model_page_ui_choices')['quality'] == ['auto','low','medium','high','xhigh','max']
    assert 'quality_options' not in f and 'api_surface' not in f


def test_ui_missing_select_does_not_read_embedded_rsc():
    b = (F/'vercel_sunburst_model.html').read_bytes().replace(b'aria-label="Quality"', b'aria-label="Removed"')
    with pytest.raises(ValueError): parse('vercel_ui', body=b)


def test_fal_claim_preserves_corresponding_combination_and_unknown_billing():
    f = parse('fal_guide')
    assert val(f,'profile_scope') == 'reference'
    assert val(f,'evidence_kind') == 'supplier_claim'
    claim = val(f,'marketing_pricing_claim')
    assert claim['variant_equivalence'] == 'same price at corresponding quality/size combination'
    assert f['per_image_token_rate_relationship']['status'] == 'unknown'
    assert 'size_rules' not in f and 'processing_options' not in f


def test_fal_altered_claim_cannot_reuse_rsc_copy():
    b = (F/'fal_image25_guide.html').read_bytes().replace(b'Every GPT Image 2.5 request is priced per image returned,', b'Every GPT Image 2.5 request is priced per token,', 1)
    with pytest.raises(ValueError): parse('fal_guide', body=b)


def test_fal_sync_mode_describes_delivery_only():
    s = next(s for s in source_specs() if s.source_key == 'image25_fal_sunburst_api')
    b = (F.parent/'image25_api/fal_sunburst_api.json').read_bytes()
    _, rows = _validate_parse(s,'doc-source',*parse_source(s,b,'doc-source'))
    f={r['condition_key']:r for r in rows}
    assert val(f,'sync_mode_meaning') == 'data URI output; result not retained in request history'
    assert f['processing_options']['status']=='unknown'


@pytest.mark.parametrize('wrapper',['hidden','aria-hidden','template'])
@pytest.mark.parametrize('kind',['azure_howto','vercel_ui','openai_generate'])
def test_hidden_contract_descendants_cannot_supply_visible_scope(kind,wrapper):
    import re
    filename={'azure_howto':'azure_image_howto','vercel_ui':'vercel_sunburst_model','openai_generate':'openai_image_generate'}[kind]
    b=(F/(filename+'.html')).read_bytes()
    if kind=='azure_howto':
        target=next(t for t in re.findall(rb'<table\b.*?</table>',b,re.S) if b'GPT-Image-2.5-Sunburst' in t)
    elif kind=='vercel_ui':
        target=next(t for t in re.findall(rb'<select\b.*?</select>',b,re.S) if b'aria-label="Quality"' in t)
    else:
        # Hide the n property subtree by wrapping the entire visible document body.
        target=next(t for t in re.findall(rb'<body\b.*?</body>',b,re.S))
    open_tag={'hidden':b'<div hidden>','aria-hidden':b'<div aria-hidden="true">','template':b'<template>'}[wrapper]
    close_tag=b'</template>' if wrapper=='template' else b'</div>'
    b=b.replace(target,open_tag+target+close_tag,1)
    with pytest.raises(ValueError):parse(kind,body=b)


def _openai_with_constraint_class(cls):
    b = (F/'openai_image_generate.html').read_bytes()
    return b.replace(b'class="stldocs-property-constraint"', b'class="'+cls.encode()+b'"')


def test_openai_n_constraint_accepts_extra_class_tokens():
    f = parse('openai_generate', body=_openai_with_constraint_class('stldocs-property-constraint _constraint_1yfc0_93'))
    assert val(f,'num_images_range') == {'min':1,'max':10}


@pytest.mark.parametrize('cls', ['x-stldocs-property-constraint', 'stldocs-property-constraint-name', 'stldocs-property-constraints'])
def test_openai_n_constraint_rejects_similar_class_names(cls):
    with pytest.raises(ValueError): parse('openai_generate', body=_openai_with_constraint_class(cls))


def test_openai_n_constraint_conflict_still_fails_with_extra_class_tokens():
    b = _openai_with_constraint_class('stldocs-property-constraint _constraint_1yfc0_93')
    with pytest.raises(ValueError): parse('openai_generate', body=b.replace(b'Must be between 1 and 10.', b'Must be between 1 and 8.'))
