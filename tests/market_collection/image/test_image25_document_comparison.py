"""Mixed retained contracts/rates and synthetic prices test safe documentary joins."""
import json
from pathlib import Path
import pytest
from scripts.market_collection.core import live, SourceSpec, ADAPTER_PRICE_FIELDS
from scripts.market_collection.sources import source_specs
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.collectors.image25_api import condition
from scripts.market_collection.comparison import compare_run

F = Path(__file__).parent/'fixtures'
MODEL = 'gpt-image-2.5-sunburst'
QUERY = dict(model=MODEL,num_images=2)


def run(root, edits=None, fail=None, extra=None):
    edits = edits or {}; fail = fail or set()
    actual = [s for s in source_specs() if s.model_id == MODEL and (s.source_key.endswith('_doc') or s.source_key == 'image25_openai_sunburst_api')]
    actual += [SourceSpec('synthetic_'+route,'image',MODEL,route,'text-to-image','https://example.org/'+route,'synthetic') for route in ('openai','azure_foundry','vercel','fal')]
    class Adapter:
        def parse_source(self,s,b,i):
            if s.source_key in fail: raise ValueError('synthetic selected document failure')
            if s.parser != 'synthetic':
                p,c = parse_source(s,b,i)
                if extra and s.source_key == 'image25_openai_sunburst_api':c.extend(extra)
                return p,c
            p = dict.fromkeys(ADAPTER_PRICE_FIELDS,'');p.update(provider_model_id=MODEL,upstream_provider='OpenAI',task='text-to-image',billing_item='Image tokens / Output',amount='30',currency='USD',unit='1M tokens',price_kind='published_rate',condition_set_id='price',locator='synthetic price',evidence_status='direct')
            return [p],[condition('price','processing','Standard','synthetic price processing')]
    def fetch(s):
        names={'image25_openai_guide':F/'image25_api/openai.md',
               'image25_openai_generate':F/'image25_supplement/openai_image_generate.html',
               'image25_azure_howto':F/'image25_supplement/azure_image_howto.html',
               'image25_azure_regions':F/'image25_supplement/azure_model_regions.html',
               'image25_openrouter_guide':F/'image25_supplement/openrouter_image_guide.html',
               'image25_vercel_ui':F/'image25_supplement/vercel_sunburst_model.html',
               'image25_fal_guide':F/'image25_supplement/fal_image25_guide.html'}
        b = names[s.parser].read_bytes() if s.parser in names else b'synthetic price evidence'
        if s.source_key in edits:b=edits[s.source_key](b)
        return b,s.url,200
    return live(root,specs=actual,adapters={'image':Adapter()},fetcher=fetch)


def candidate(result,route):return next(c for c in result['candidates'] if c['route']==route)


def test_openai_n2_reference_supplements_only_guide_placeholder(tmp_path):
    r = compare_run(run(tmp_path),QUERY)
    c = candidate(r,'openai')
    assert c['request_condition_status']=='comparable'
    refs = c['field_evidence']['manufacturer.num_images_range']
    assert refs and all('openai_generate' in x['locator'] or '(param) n' in x['locator'] for x in refs)
    assert len(r['documentary_profiles']) == 7
    assert all(p['conditions'] and p['source_evidence']['raw_sha256'] for p in r['documentary_profiles'])
    assert c['actual_request_cost']=='unknown' and c['account_eligibility']=='unknown'
    assert c['actual_cost_impact']=='not_evaluated'


@pytest.mark.parametrize('status',['unknown','conflicted','unsupported'])
def test_explicit_n_declaration_blocks_supplement(tmp_path,status):
    extra = [condition('image25_api_image25_openai_sunburst_api','num_images_range',{'min':1,'max':1},'explicit actual declaration',status)]
    c = candidate(compare_run(run(tmp_path,extra=extra),QUERY),'openai')
    assert c['request_condition_status'] != 'comparable'
    assert not any('(param) n' in e['locator'] for e in c['field_evidence'].get('manufacturer.num_images_range',[]))


def test_azure_contract_support_and_unknown_processing(tmp_path):
    c = candidate(compare_run(run(tmp_path),QUERY),'azure_foundry')
    assert 'channel_api_profile_missing' not in c['reason_codes']
    for field in ('quality_options','applicable_tasks','api_surface','num_images_range','size'):
        assert not any(x.startswith(('unknown:channel.'+field,'unsupported:channel.'+field)) for x in c['reason_codes'])
    assert 'unknown:channel.processing_options' in c['reason_codes']
    assert c['snapshot_identity'] is None and c['account_eligibility']=='unknown'


@pytest.mark.parametrize('quality',['unsupported_quality','newwrongquality'])
def test_azure_rejects_quality_absent_exact_column(tmp_path,quality):
    c = candidate(compare_run(run(tmp_path),dict(QUERY,quality=quality)),'azure_foundry')
    assert c['request_condition_status']=='not_comparable'
    assert 'unsupported:channel.quality_options' in c['reason_codes']


def test_reference_failure_is_partial_documentary_not_api_failure(tmp_path):
    r = compare_run(run(tmp_path,fail={'image25_vercel_ui_sunburst_doc','image25_azure_regions_sunburst_doc','image25_fal_guide_sunburst_doc'}),QUERY)
    assert candidate(r,'openai')['request_condition_status']=='comparable'
    assert not any('failed_api_document:image25_azure_regions' in x for x in candidate(r,'azure_foundry')['reason_codes'])
    assert len([p for p in r['documentary_profiles'] if p['parse_status']=='error'])==3


def test_generate_reference_failure_preserves_guide_default_only(tmp_path):
    p = run(tmp_path,fail={'image25_openai_generate_sunburst_doc'})
    assert candidate(compare_run(p,dict(QUERY,num_images=1)),'openai')['request_condition_status']=='comparable'
    assert candidate(compare_run(p,QUERY),'openai')['request_condition_status']=='verification_required'


def test_azure_contract_failure_stays_blocking(tmp_path):
    c = candidate(compare_run(run(tmp_path,fail={'image25_azure_howto_sunburst_doc'}),QUERY),'azure_foundry')
    assert 'failed_api_document:image25_azure_howto_sunburst_doc' in c['reason_codes']


def test_ui_and_claim_profiles_cannot_remove_channel_gap(tmp_path):
    r=compare_run(run(tmp_path),QUERY)
    assert 'channel_api_profile_missing' in candidate(r,'vercel')['reason_codes']
    assert 'channel_api_profile_missing' in candidate(r,'fal')['reason_codes']
    assert {p['scope'] for p in r['documentary_profiles']} >= {'channel_ui','reference','generic_api_reference','manufacturer_supplement'}
