"""Retained endpoint JSON and synthetic guard probes; no generation or billing."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
import pytest
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.core import _validate_parse
from scripts.market_collection.sources import source_specs
from tests.market_collection.retired_sources import without_retired

F = Path(__file__).parent/'fixtures'/'image25_openrouter_endpoints'

def payload(variant='sunburst'):
    return json.loads((F/(variant+'.json')).read_bytes())

def parse(data=None, variant='sunburst', body=None):
    spec = next(s for s in source_specs() if s.source_key == f'image25_openrouter_{variant}_endpoints')
    raw = body if body is not None else json.dumps(data if data is not None else payload(variant)).encode()
    return _validate_parse(spec, 'endpoint-source', *parse_source(spec, raw, 'endpoint-source'))

def fields(rows):
    return {r['condition_key']:r for r in rows}

def test_registry_is_additive_and_fixture_hashes_match():
    specs=source_specs()
    old=without_retired(json.loads((F/'registry111.json').read_text()))
    assert [asdict(s) for s in specs[:len(old)]] == old
    assert len(specs)==178 and len({s.model_id for s in specs})==29
    assert len({(s.model_id,s.route) for s in specs})==106
    for p in json.loads((F/'provenance.json').read_text()):
        assert hashlib.sha256((F/(p['variant']+'.json')).read_bytes()).hexdigest()==p['raw_sha256']

@pytest.mark.parametrize('variant',['sunburst','flare'])
def test_genuine_typed_rates_and_exact_profile(variant):
    prices,rows=parse(variant=variant,body=(F/(variant+'.json')).read_bytes())
    assert len(prices)==3 and {p['billing_item'] for p in prices}=={'Text Input','Image Input','Image Output'}
    assert all(p['unit']=='1 token' and p['currency']=='USD' for p in prices)
    profiles=[r for r in rows if r['condition_key']=='profile_scope']
    assert len(profiles)==1 and profiles[0]['value']=='channel_endpoint'
    profile=fields([r for r in rows if r['condition_set_id']==profiles[0]['condition_set_id']])
    assert profile['provider_slug']['value']=='openai' and profile['provider_tag']['value']=='openai'
    assert json.loads(profile['supported_parameters']['value'])==payload(variant)['endpoints'][0]['supported_parameters']
    assert json.loads(profile['num_images_range']['value'])=={'min':1,'max':10}
    for k in ('applicable_tasks','processing_options','size_presets','size_rules','model_snapshot','cache_applicability','quality_default','num_images_default'):
        assert profile[k]['status']=='unknown'
    assert 'source URL=' in profile['api_surface']['locator']
    for p in prices:
        own=fields([r for r in rows if r['condition_set_id']==p['condition_set_id']])
        assert own['endpoint_profile_id']['value']==profiles[0]['condition_set_id']
        assert all(r['status']=='stated' for r in own.values())
        assert own['unit']['value']=='token' and 'pricing[' in p['locator']

@pytest.mark.parametrize('amount,expected',[(0,'0'),(1e-12,'0.000000000001')])
def test_zero_and_scientific_decimal_precision(amount,expected):
    d=payload();d['endpoints'][0]['pricing'][0]['cost_usd']=amount
    prices,_=parse(d);assert prices[0]['amount']==expected
    raw=(F/'sunburst.json').read_text().replace('0.000008','0.00000012345678901234567890123456789')
    prices,_=parse(body=raw.encode());assert prices[0]['amount']=='0.00000012345678901234567890123456789'

@pytest.mark.parametrize('change',['root','list','identity','duplicate_endpoint','tag','null_descriptor','unknown_descriptor','enum','range','streaming','passthrough','bool','negative','nan','infinity','missing_amount','string_amount','duplicate_rate','bad_variant'])
def test_rejects_ambiguous_or_malformed_source(change):
    d=payload();e=d['endpoints'][0];rate=e['pricing'][0]
    if change=='root':d['id']='openai/neighbor'
    if change=='list':d['endpoints']={}
    if change=='identity':e['provider_slug']=''
    if change=='duplicate_endpoint':d['endpoints'].append(e)
    if change=='tag':e['provider_tag']=False
    if change=='null_descriptor':e['supported_parameters']['quality']=None
    if change=='unknown_descriptor':e['supported_parameters']['quality']={'type':'magic'}
    if change=='enum':e['supported_parameters']['quality']['values']=['high','high']
    if change=='range':e['supported_parameters']['n']['min']=True
    if change=='streaming':e['supports_streaming']='true'
    if change=='passthrough':e['allowed_passthrough_parameters']=['moderation','moderation']
    if change=='bool':rate['cost_usd']=True
    if change=='negative':rate['cost_usd']=-1
    if change=='nan':rate['cost_usd']=float('nan')
    if change=='infinity':rate['cost_usd']=float('inf')
    if change=='missing_amount':rate.pop('cost_usd')
    if change=='string_amount':rate['cost_usd']='0.1'
    if change=='duplicate_rate':e['pricing'].append(rate)
    if change=='bad_variant':rate['variant']={}
    with pytest.raises(ValueError):parse(d)

@pytest.mark.parametrize('field,value',[('unit','megapixel'),('billable','future_output')])
def test_unknown_billing_keeps_literal_claim_without_amount(field,value):
    d=payload();d['endpoints'][0]['pricing'][0][field]=value
    prices,rows=parse(d);p=prices[0]
    assert p['amount']==p['unit']==p['currency']=='' and p['unknown_reason']
    own=fields([r for r in rows if r['condition_set_id']==p['condition_set_id']])
    assert json.loads(own['pricing_line']['value'])[field]==value
    assert value in p['locator']

@pytest.mark.parametrize('unit',['image','request'])
def test_declared_non_token_unit_preserved(unit):
    d=payload();d['endpoints'][0]['pricing'][0]['unit']=unit
    prices,_=parse(d);assert prices[0]['unit']=='1 '+unit

def test_empty_endpoints_explicitly_unavailable():
    d=payload();d['endpoints']=[]
    prices,rows=parse(d);assert prices==[] and fields(rows)['endpoint_availability']['value']=='unavailable'

def test_null_tag_variant_boolean_and_other_metadata_preserved():
    d=payload();e=d['endpoints'][0];e['provider_tag']=None
    e['supported_parameters']['watermark']={'type':'boolean'}
    e['pricing'][0]['variant']='premium'
    prices,rows=parse(d);f=fields(rows)
    assert f['provider_tag']['value']=='null' and f['provider_tag']['status']=='unknown'
    assert json.loads(f['watermark_capability']['value'])=={'type':'boolean'}
    own=fields([r for r in rows if r['condition_set_id']==prices[0]['condition_set_id']])
    assert own['variant']['value']=='premium' and own['variant_applicability']['status']=='unknown'


def test_collection_stamps_own_source_links(tmp_path):
    from scripts.market_collection.core import live, _validated_run
    from scripts.market_collection.adapters import image
    specs=[s for s in source_specs() if s.parser=='image25_openrouter_endpoints']
    run=live(tmp_path/'collection',specs=specs,adapters={'image':image},
             fetcher=lambda s:((F/(s.model_id.rsplit('-',1)[1]+'.json')).read_bytes(),s.url,200))
    _,sources,prices,rows=_validated_run(run)
    assert len(sources)==2 and len(prices)==6
    groups={(r['source_id'],r['condition_set_id']) for r in rows}
    for p in prices:
        own=[r for r in rows if r['source_id']==p['source_id'] and r['condition_set_id']==p['condition_set_id']]
        link=next(r for r in own if r['condition_key']=='endpoint_profile_id')
        assert (p['source_id'],link['value']) in groups

def test_provider_reorder_keeps_profile_and_rate_bindings():
    d=payload();second=json.loads(json.dumps(d['endpoints'][0]))
    second.update(provider_name='Other provider',provider_slug='openai',provider_tag=None)
    second['supported_parameters']['quality']['values']=['low']
    second['pricing'][0]['cost_usd']=0
    d['endpoints'].append(second)
    p1,c1=parse(d);d['endpoints'].reverse();p2,c2=parse(d)
    def facts(prices,conditions):
        return sorted([(r['condition_set_id'],r['condition_key'],r['value'],r['status']) for r in conditions]), sorted([(p['condition_set_id'],p['billing_item'],p['amount'],p['unit']) for p in prices])
    assert facts(p1,c1)==facts(p2,c2)
    assert {r['condition_set_id'] for r in c1 if r['condition_key']=='profile_scope'}=={r['condition_set_id'] for r in c2 if r['condition_key']=='profile_scope'}

@pytest.mark.parametrize('fragment,replacement',[
    ('"id":"openai/gpt-image-2.5-sunburst"','"id":"openai/neighbor","id":"openai/gpt-image-2.5-sunburst"'),
    ('"quality":{"type":"enum"','"quality":{"type":"enum","values":["low"]},"quality":{"type":"enum"'),
    ('"cost_usd":0.000008','"cost_usd":1,"cost_usd":0.000008'),
])
def test_duplicate_json_fields_cannot_use_last_value(fragment,replacement):
    raw=(F/'sunburst.json').read_text().replace(fragment,replacement,1)
    with pytest.raises(ValueError,match='duplicate'):parse(body=raw.encode())
