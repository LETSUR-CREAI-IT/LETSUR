"""Retained-source regressions for the explicitly approved additions (Veo 3.1 sources retired 2026-10-22 and 2026-10-28)."""
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
NEW_PARSERS = {"vertex_gemini", "databricks_gemini", "azure_image_retail", "vercel_video"}
NEW = [s for s in source_specs() if s.parser in NEW_PARSERS]
FILES = {s.source_key: (
    'azure.json' if s.parser == 'azure_image_retail' else
    'vertex.html' if s.parser == 'vertex_gemini' else
    'databricks.html' if s.parser == 'databricks_gemini' else
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
        assert [asdict(s) for s in source_specs() if s.modality == modality and s.parser not in NEW_PARSERS and not s.parser.endswith("endpoints_llm") and not s.source_key.startswith("image25_")] == [s for s in old if s['modality'] == modality]
    assert len(NEW) == 5
    assert len(source_specs()) == 146
    assert len({(s.model_id, s.route) for s in source_specs()}) == 86
    assert not any(s.route == 'openrouter' and s.modality == 'video' for s in source_specs())


@pytest.mark.parametrize('key,count', [
    ('azure_sunburst',5), ('azure_flare',5), ('vertex_gemini_38_flash',12), ('databricks_gemini_38_flash',2),
    ('vercel_seedance_25_t2v',3)])
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


@pytest.mark.parametrize('mutation', [lambda d:d['backingModels'][0].update(primaryModel='seedance-2.0'),
    lambda d:d['backingModels'][0].update(provider='google'), lambda d:d['backingModels'][0].update(type='chat'),
    lambda d:d['backingModels'][0]['videoTokenPricing'].pop('costPerMillionTokens'),
    lambda d:d['backingModels'][0]['videoTokenPricing']['tiers'].append(d['backingModels'][0]['videoTokenPricing']['tiers'][0]),
    lambda d:d['backingModels'].append(d['backingModels'][0])])
def test_vercel_identity_unit_capability_duplicate(mutation):
    with pytest.raises(ValueError):parse('vercel_seedance_25_t2v',changed_json('vercel_seedance_25_t2v',mutation))


def test_vercel_no_price_not_zero_or_recommendations():
    key='vercel_seedance_25_t2v';data=json.loads(body(key));model=data['backingModels'][0]
    data['recommendedModels']=[{'backingModels':[dict(model)]}]
    del model['videoTokenPricing']
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


def test_live_partial_parse_failure_and_unknown_preserved(tmp_path):
    specs=[SPECS[k] for k in ('azure_sunburst','databricks_gemini_38_flash','vercel_seedance_25_t2v')]
    _,adapters=core.registry()
    run=core.live(tmp_path,specs=specs,adapters=adapters,fetcher=lambda s:(b'changed' if s.source_key=='vercel_seedance_25_t2v' else body(s.source_key),s.url,200))
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

