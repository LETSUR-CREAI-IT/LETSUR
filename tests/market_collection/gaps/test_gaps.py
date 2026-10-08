"""Retained-source regressions for the 14 explicitly approved additions."""
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.sources import source_specs

FIXTURES = Path(__file__).parent / "fixtures"
SPECS = {s.source_key: s for s in source_specs()}
NEW_PARSERS = {"vertex_gemini", "databricks_gemini", "azure_image_retail", "google_veo", "vercel_video", "fal_veo"}
NEW = [s for s in source_specs() if s.parser in NEW_PARSERS]
FILES = {s.source_key: (
    'azure.json' if s.parser == 'azure_image_retail' else
    'vertex.html' if s.parser == 'vertex_gemini' else
    'databricks.html' if s.parser == 'databricks_gemini' else
    'google.html' if s.parser == 'google_veo' else
    'fal_' + s.model_id.split('-')[-1] + '.json' if s.parser == 'fal_veo' else
    s.url.split('/')[-1] + '.json') for s in NEW}


def body(key):
    return (FIXTURES / FILES[key]).read_bytes()


def parse(key, content=None):
    spec = SPECS[key]
    result = parse_source(spec, body(key) if content is None else content, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def changed_json(key, mutate):
    data = json.loads(body(key)); mutate(data)
    return json.dumps(data).encode()


def test_existing_specs_preserved_in_each_modality_order():
    # Immutable pre-change source representation retained in the compact fixture.
    old = json.loads((FIXTURES / 'baseline_specs.json').read_text())
    current = {s.source_key: asdict(s) for s in source_specs()}
    assert [current[s['source_key']] for s in old] == old
    for modality in ('llm', 'image', 'video'):
        assert [asdict(s) for s in source_specs() if s.modality == modality and s.parser not in NEW_PARSERS and s.model_id != "ideogram-4.5" and not s.source_key.startswith("image25_")] == [s for s in old if s['modality'] == modality]
    assert len(NEW) == 14
    assert len(source_specs()) == 125
    assert len({(s.model_id, s.route) for s in source_specs()}) == 97
    assert not any(s.route == 'openrouter' and s.modality == 'video' for s in source_specs())
    assert sum(s.route == 'fal' and s.model_id == 'veo-3.1-lite' for s in source_specs()) == 1


@pytest.mark.parametrize('key,count', [
    ('azure_sunburst',5), ('azure_flare',5), ('vertex_gemini_38_flash',12), ('databricks_gemini_38_flash',2),
    ('google_veo_3_1_standard',3), ('google_veo_3_1_fast',3), ('google_veo_3_1_lite',2),
    ('vercel_veo_3_1_standard',6), ('vercel_veo_3_1_fast',6), ('vercel_veo_3_1_lite',4),
    ('fal_veo_3_1_standard',6), ('fal_veo_3_1_fast',6), ('fal_veo_3_1_lite',4), ('vercel_seedance_25_t2v',3)])
def test_retained_additions_contract(key,count):
    prices, conditions = parse(key)
    assert len(prices) == count
    assert all(p['condition_set_id'] in {c['condition_set_id'] for c in conditions} for p in prices)
    assert all(p['locator'] and p['provider_model_id'] for p in prices)
    assert all(p['amount'] or p['unknown_reason'] for p in prices)


@pytest.mark.parametrize('field,value', [('currencyCode','KRW'), ('armRegionName','eastus'), ('type','Reservation'),
    ('unitOfMeasure','1K'), ('meterName','Image-2.5-sunburst img inp Gl 1K Tokens'),
    ('skuName','Image-2.5-flare img inp Gl'), ('productName','Unrelated Media'), ('retailPrice',-1), ('retailPrice','NaN')])
def test_azure_wrong_conditions_rejected(field,value):
    with pytest.raises(ValueError):
        parse('azure_sunburst', changed_json('azure_sunburst',lambda d: d['Items'][0].update({field:value})))


@pytest.mark.parametrize('mutation', [lambda d: d.update(NextPageLink='https://prices.azure.com/next'),
    lambda d: d['Items'].append(d['Items'][0]), lambda d: d['Items'].pop(0),
    lambda d: d['Items'][0].update(meterName='Image-2.5-other img inp Gl 1M Tokens')])
def test_azure_pagination_duplicate_missing_model(mutation):
    with pytest.raises(ValueError): parse('azure_sunburst', changed_json('azure_sunburst',mutation))


def test_azure_retail_not_unit_price_and_model_isolation():
    prices,_ = parse('azure_sunburst', changed_json('azure_sunburst',lambda d: [r.update(unitPrice=999) for r in d['Items']]))
    assert {p['amount'] for p in prices} == {'8','30','1.25','2','5'}
    assert all('sunburst' in p['locator'] for p in prices)


def test_vertex_semantics_periods_and_adjacent_models():
    prices,conditions = parse('vertex_gemini_38_flash')
    assert {(p['billing_item'],p['amount'],p['effective_until']) for p in prices if p['price_kind']=='published_rate'} == {
        ('input','0.75','2026-12-31'), ('output','3.75','2026-12-31'), ('cache_read','0.075','2026-12-31')}
    assert {p['effective_from'] for p in prices if p['price_kind']=='announced_future_rate'} == {'2027-01-01'}
    assert {c['value'] for c in conditions if c['condition_key']=='input_token_band'} == {'<=200K','>200K'}
    mutated = body('vertex_gemini_38_flash').replace(b'$1.50',b'$999').replace(b'$7.50',b'$888')
    # Current exact-model rows are independent of future and adjacent models.
    altered,_ = parse('vertex_gemini_38_flash',mutated)
    assert [p for p in altered if p['price_kind']=='published_rate'] == [p for p in prices if p['price_kind']=='published_rate']


@pytest.mark.parametrize('old,new', [(b'Price (/1M tokens)&lt;= 200K input tokens',b'Price (/1K tokens)&lt;= 200K input tokens'),
    (b'Prices are listed in US Dollars (USD).',b'Prices are listed in EUR.'),
    (b'Gemini 3.8 Flash*through',b'Gemini 3.8 Flash Cyber*through'),
    (b'$0.75',b'N/A'), (b'Global',b'Other-region')])
def test_vertex_missing_header_currency_identity_rate_region(old,new):
    with pytest.raises(ValueError):parse('vertex_gemini_38_flash',body('vertex_gemini_38_flash').replace(old,new))


def test_vertex_duplicate_table_rejected():
    content=body('vertex_gemini_38_flash')
    with pytest.raises(ValueError,match='duplicate'):parse('vertex_gemini_38_flash',content+content)


def test_databricks_no_price_and_exact_section():
    prices,_ = parse('databricks_gemini_38_flash')
    assert {p['provider_model_id'] for p in prices} == {'databricks-gemini-3-8-flash'}
    assert all(p['amount']=='' and p['currency']=='' and p['unknown_reason'] for p in prices)
    for old,new in ((b'databricks-gemini-3-8-flash',b'databricks-gemini-3-7-flash'),
                    (b'databricks-gemini-3-8-flash',b'databricks-gemini-3-8-flash-preview'),
                    (b'global endpoint',b'regional endpoint'),(b'cross geography routing to be enabled',b'local routing')):
        with pytest.raises(ValueError):parse('databricks_gemini_38_flash',body('databricks_gemini_38_flash').replace(old,new))


@pytest.mark.parametrize('key,expected', [('google_veo_3_1_standard',['0.40','0.40','0.60']),
    ('google_veo_3_1_fast',['0.10','0.12','0.30']),('google_veo_3_1_lite',['0.05','0.08'])])
def test_google_exact_audio_paid_prices(key,expected):
    prices,conditions=parse(key)
    assert [p['amount'] for p in prices]==expected
    assert all(p['unit']=='1 second' for p in prices)
    assert all(c['value']=='on (default)' for c in conditions if c['condition_key']=='audio')


@pytest.mark.parametrize('old,new', [(b'Paid Tier, per second in USD',b'Paid Tier, per minute in USD'),
    (b'Veo 3.1 Fast video with audio',b'Veo 3.0 Fast video with audio'),
    (b'veo-3.1-fast-generate-preview',b'veo-3.0-fast-generate-preview'),(b'$0.12 (1080p)',b'Unknown (1080p)')])
def test_google_wrong_header_identity_price(old,new):
    with pytest.raises(ValueError):parse('google_veo_3_1_fast',body('google_veo_3_1_fast').replace(old,new))


def test_fal_primary_exact_endpoint_not_related_copy():
    key='fal_veo_3_1_fast'
    data=json.loads(body(key));data['relatedModels']=[json.loads(body('fal_veo_3_1_standard'))['model']]
    prices,_=parse(key,json.dumps(data).encode())
    assert [p['amount'] for p in prices]==['0.10','0.15','0.10','0.15','0.30','0.35']
    data['model']['pricingInfoOverride']='No price'
    data['relatedModels'].append(json.loads(body(key))['model'])
    with pytest.raises(ValueError):parse(key,json.dumps(data).encode())


@pytest.mark.parametrize('mutation', [lambda d:d['model'].update(modelUrl='https://fal.run/fal-ai/veo3.1'),
    lambda d:d['modelData'].update(endpointId='fal-ai/veo3.1'),
    lambda d:d['model'].update(category='image-to-video'), lambda d:d['model'].update(status='private'),
    lambda d:d['model'].update(pricingInfoOverride=d['model']['pricingInfoOverride'].replace('every second','every minute'))])
def test_fal_wrong_endpoint_unit(mutation):
    with pytest.raises(ValueError):parse('fal_veo_3_1_fast',changed_json('fal_veo_3_1_fast',mutation))


def test_fal_duplicate_primary():
    content=body('fal_veo_3_1_fast')
    with pytest.raises(ValueError):parse('fal_veo_3_1_fast',content+content)


@pytest.mark.parametrize('mutation', [lambda d:d['backingModels'][0].update(primaryModel='veo-3.1-fast-generate-001'),
    lambda d:d['backingModels'][0].update(provider='google'), lambda d:d['backingModels'][0].update(type='chat'),
    lambda d:d['backingModels'][0]['videoDimensionPricing'][0].update(costUnit='minute'),
    lambda d:d['backingModels'][0]['videoDimensionPricing'][0].update(size='480p'),
    lambda d:d['backingModels'][0]['videoDimensionPricing'][0].update(hasAudio='false'),
    lambda d:d['backingModels'][0]['videoDimensionPricing'][0].update(cost='NaN'),
    lambda d:d['backingModels'].append(d['backingModels'][0]),
    lambda d:d['backingModels'][0]['videoDimensionPricing'].append(d['backingModels'][0]['videoDimensionPricing'][0])])
def test_vercel_identity_unit_capability_duplicate(mutation):
    with pytest.raises(ValueError):parse('vercel_veo_3_1_standard',changed_json('vercel_veo_3_1_standard',mutation))


def test_vercel_no_price_not_zero_or_recommendations():
    key='vercel_veo_3_1_standard';data=json.loads(body(key));model=data['backingModels'][0]
    data['recommendedModels']=[{'backingModels':[dict(model)]}]
    del model['videoDimensionPricing']
    prices,_=parse(key,json.dumps(data).encode())
    assert len(prices)==1 and prices[0]['amount']=='' and prices[0]['unknown_reason']
    data['backingModels']=[]
    with pytest.raises(ValueError):parse(key,json.dumps(data).encode())


def test_vercel_seedance_base_resolution_and_input_video_separation():
    prices,conditions=parse('vercel_seedance_25_t2v')
    by_group={c['condition_set_id']:c['value'] for c in conditions if c['condition_key']=='resolution'}
    assert {(by_group[p['condition_set_id']],p['amount']) for p in prices}=={('480p','10.70'),('720p','10.70'),('1080p','11.7')}
    assert all(p['unit']=='1M tokens' for p in prices)
    assert not ({'6.40','7.0','0'} & {p['amount'] for p in prices})
    assert {c['value'] for c in conditions if c['condition_key']=='input_video'}=={'none'}


def test_vercel_audio_absent_vs_explicit_false():
    key='vercel_veo_3_1_lite';parse(key)
    with pytest.raises(ValueError):parse(key,changed_json(key,lambda d:d['backingModels'][0]['videoCapabilities'].update(generateAudio=False)))


def test_live_partial_parse_failure_and_unknown_preserved(tmp_path):
    specs=[SPECS[k] for k in ('azure_sunburst','databricks_gemini_38_flash','vercel_veo_3_1_standard')]
    _,adapters=core.registry()
    run=core.live(tmp_path,specs=specs,adapters=adapters,fetcher=lambda s:(b'changed' if s.source_key=='vercel_veo_3_1_standard' else body(s.source_key),s.url,200))
    _,sources,prices,_=core._validated_run(run)
    assert [s['parse_status'] for s in sources]==['ok','ok','error']
    assert len(prices)==7
    assert sum(not p['amount'] for p in prices)==2


def test_fetch_accepts_exact_four_mib_boundary():
    from tests.market_collection.core.test_core import Response
    payload=b'x'*core.MAX_BODY_BYTES
    opener=SimpleNamespace(open=lambda *a,**k:Response(payload))
    with patch.object(core.urllib.request,'build_opener',return_value=opener):
        assert core.fetch(NEW[0])[0]==payload
    assert core.MAX_BODY_BYTES==4*1024*1024


def test_fal_lite_exact_resolution_audio_rates_no_four_k():
    key = 'fal_veo_3_1_lite'
    prices, conditions = parse(key)
    groups = {p['condition_set_id']:p['amount'] for p in prices}
    by_group = {}
    for c in conditions:
        by_group.setdefault(c['condition_set_id'], {})[c['condition_key']] = c['value']
    assert {(by_group[g]['resolution'],by_group[g]['audio'],v) for g,v in groups.items()} == {
        ('720p','on','0.05'), ('720p','off','0.03'), ('1080p','on','0.08'), ('1080p','off','0.05')}
    assert all(p['provider_model_id']=='fal-ai/veo3.1/lite' and p['unit']=='1 second' for p in prices)
    for old,new in ((b'1080p without audio',b'4k without audio'), (b'For every second',b'For every minute'),
                    (b'https://fal.run/fal-ai/veo3.1/lite',b'https://fal.run/fal-ai/veo3.1/fast')):
        with pytest.raises(ValueError): parse(key,body(key).replace(old,new))
