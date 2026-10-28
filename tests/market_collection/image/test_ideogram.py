"""Retained Ideogram 4.5 evidence, fail-closed scopes and regression checks."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection.collectors import parse_source
from scripts.market_collection.core import _validate_parse, validate_registry
from scripts.market_collection.models import SUCCESSOR_MODELS
from scripts.market_collection.sources import source_specs, ideogram_source_specs
from tests.market_collection.retired_sources import without_retired

FIXTURES = Path(__file__).parent / 'fixtures' / 'ideogram45'
SPECS = {s.source_key:s for s in ideogram_source_specs()}


def fixture(key):
    return 'catalog.json' if key.startswith('ideogram_') else 'fal.json' if key.endswith('t2i') else 'fal-edit.json'


def data(key):
    return json.loads((FIXTURES / fixture(key)).read_bytes())


def parse(key, changed=None):
    spec = SPECS[key]
    body = json.dumps(changed).encode() if changed is not None else (FIXTURES / fixture(key)).read_bytes()
    return _validate_parse(spec,'retained',*parse_source(spec,body,'retained'))


@pytest.mark.parametrize('key,count', [('ideogram_45_t2i',6),('ideogram_45_edit',8),('ideogram_45_precise_edit',8),
                                      ('fal_ideogram_45_t2i',3),('fal_ideogram_45_edit',4),('fal_ideogram_45_precise_edit',4)])
def test_exact_rows_and_preserved_conditions(key,count):
    prices,conditions = parse(key)
    assert len(prices)==count
    assert all(p['unit']=='1 image' and p['currency']=='USD' and p['locator'] for p in prices)
    assert all(p['task']==SPECS[key].task for p in prices)
    by_group={p['condition_set_id']:{c['condition_key']:c for c in conditions if c['condition_set_id']==p['condition_set_id']} for p in prices}
    assert all({'quality','source_images','operation','endpoint'} <= set(c) for c in by_group.values())
    if key.startswith('ideogram_'):
        assert {c['snapshot_at']['value'] for c in by_group.values()}=={'2026-09-03T15:00:00+00:00'}
        assert all(c['snapshot_effective_relationship']['status']=='unknown' for c in by_group.values())
        assert {c['resolution_tier']['value'] for c in by_group.values()}=={'1K','2K'}
        assert all(c['defaults']['status']=='unknown' and c['pixel_size_rules']['status']=='unknown' for c in by_group.values())
    else:
        assert all(c['quality_default']['value']=='medium' for c in by_group.values())
        assert all('size_price_applicability' in c for c in by_group.values())
        if not key.endswith('t2i'):
            assert all(c['quality_description_conflict']['status']=='conflicted' and 'low to very_high for high precision' in c['quality_description_conflict']['value'] for c in by_group.values())
            assert {c['edit_precision']['value'] for c in by_group.values()}==({'high'} if key.endswith('precise_edit') else {'regular'})


def test_observed_rates_and_source_time_preserved():
    p,_=parse('ideogram_45_t2i');assert [x['amount'] for x in p]==['0.1','0.1','0.03','0.03','0.06','0.06']
    assert all(x['effective_from']=='2026-09-28T21:00:00+00:00' for x in p)
    p,_=parse('fal_ideogram_45_t2i');assert [x['amount'] for x in p]==['0.03','0.06','0.22']


@pytest.mark.parametrize('value', [True,False,'NaN','Infinity',-1,'-0.01',None])
def test_invalid_catalog_prices(value):
    d=data('ideogram_45_t2i');d['entries'][0]['price']['amount']=value
    with pytest.raises(ValueError):parse('ideogram_45_t2i',d)


@pytest.mark.parametrize('mutation', [
    lambda d:d['entries'].append(d['entries'][0]),
    lambda d:d['entries'].pop(0),
    lambda d:d['entries'][0]['dimensions'].update(rendering_speed='VERY_HIGH'),
    lambda d:d['entries'][0]['dimensions'].update(resolution_tier='4K'),
    lambda d:d['entries'][0]['dimensions'].update(new_dimension='UNKNOWN'),
    lambda d:d['entries'][0]['price'].update(currency='EUR'),
    lambda d:d['entries'][0].update(effective_at='2999-01-01T00:00:00Z'),
    lambda d:d['entries'][0].update(effective_at='2026-01-01'),
    lambda d:d.update(snapshot_at=None),
    lambda d:d['public_pricing'].update(pricing_unit='usd'),
    lambda d:d['public_pricing']['sections'][0].update(description='per minute'),
    lambda d:d['public_pricing']['sections'][0]['groups'][0].update(id='ideogram_4_0'),
])
def test_invalid_catalog_scope(mutation):
    d=data('ideogram_45_t2i');mutation(d)
    with pytest.raises((ValueError,KeyError)):parse('ideogram_45_t2i',d)


@pytest.mark.parametrize('mutation', [
    lambda o:o[0]['price'].update(amount=999999),
    lambda o:o[0]['price'].update(amount=True),
    lambda o:o[0]['price'].update(qualifier='estimated'),
    lambda o:o[0]['endpoint'].update(path='/v2/image/generate/ideogram-4-0'),
    lambda o:o[0]['endpoint'].update(method='GET'),
    lambda o:o[0].update(display_type='per_second'),
    lambda o:o[0].update(label='Neighbor model price'),
    lambda o:o.append(o[0]),lambda o:o.pop(0),
])
def test_public_crosscheck_rejects(mutation):
    d=data('ideogram_45_t2i');mutation(d['public_pricing']['sections'][0]['groups'][0]['offerings'])
    with pytest.raises(ValueError):parse('ideogram_45_t2i',d)


def test_valid_zero_and_neighbor_isolation():
    d=data('ideogram_45_edit');r=d['entries'][0];r['price']['amount']='0'
    oid='ideogram_4_5_generate_high_1k_with_references'
    next(o for o in d['public_pricing']['sections'][0]['groups'][0]['offerings'] if o['id']==oid)['price']['amount']=0
    d['entries'].append({'billing_identifier':'api_ideogram_v4_generation','price':{'amount':999}})
    prices,_=parse('ideogram_45_edit',d);assert '0' in {p['amount'] for p in prices}


def properties(d,edit=False):
    return d['modelData']['appData']['metadata']['openapi']['components']['schemas']['EditInputV45' if edit else 'GenerateInputV45']['properties']


@pytest.mark.parametrize('mutation', [
    lambda d:d['model'].update(id='ideogram/v4.0'),
    lambda d:d['model'].update(modelUrl='https://fal.run/ideogram/v4.0'),
    lambda d:d['modelData'].update(endpointId='ideogram/v4.0'),
    lambda d:d['model'].update(status='private'),
    lambda d:d['model'].update(category='image-to-image'),
    lambda d:d['model'].update(pricingInfoOverride=d['model']['pricingInfoOverride'].replace('per image','per second')),
    lambda d:d['model'].update(pricingInfoOverride=d['model']['pricingInfoOverride'].replace('$0.03','$NaN')),
    lambda d:d['model'].update(pricingInfoOverride=d['model']['pricingInfoOverride'].replace('$0.03','$-1')),
    lambda d:d['model'].update(pricingInfoOverride=d['model']['pricingInfoOverride'].replace('size does not change','size changes')),
    lambda d:properties(d)['quality'].update(enum=['low','medium','high','very_high']),
    lambda d:properties(d)['quality'].update(default='unknown'),
    lambda d:properties(d).pop('image_size'),
])
def test_fal_wrong_identity_prices_schema(mutation):
    d=data('fal_ideogram_45_t2i');mutation(d)
    with pytest.raises((ValueError,KeyError)):parse('fal_ideogram_45_t2i',d)


def test_fal_neighbor_only_duplicate_and_actual_flight():
    key='fal_ideogram_45_t2i';d=data(key)
    with pytest.raises(ValueError):parse(key,{'relatedModels':[d['model']]})
    with pytest.raises(ValueError):parse(key,{'a':d,'b':d})
    payload='1:'+json.dumps(d)+'\n'
    body=('self.__next_f.push('+json.dumps([1,payload])+')').encode()
    assert len(parse_source(SPECS[key],body,'retained')[0])==3
    d['relatedModels']=[data('fal_ideogram_45_edit')['model']]
    assert len(parse(key,d)[0])==3


def test_fal_zero_and_edit_schema_failures():
    d=data('fal_ideogram_45_t2i');d['model']['pricingInfoOverride']=d['model']['pricingInfoOverride'].replace('$0.03','$0')
    assert parse('fal_ideogram_45_t2i',d)[0][0]['amount']=='0'
    d=data('fal_ideogram_45_edit');properties(d,True)['edit_precision']['enum'].append('ultra')
    with pytest.raises(ValueError):parse('fal_ideogram_45_edit',d)


def test_old_registry_specs_byte_equal_to_baseline():
    old=without_retired(json.loads((FIXTURES / 'baseline-specs.json').read_text()))
    current=[asdict(s) for s in source_specs() if s.model_id!='ideogram-4.5' and not s.source_key.startswith('image25_') and not s.parser.endswith('endpoints_llm') and s.model_id not in SUCCESSOR_MODELS]
    assert current==old and len(old)==94
    validate_registry(source_specs())
    assert len(source_specs())==178
    assert len({(s.model_id,s.route) for s in source_specs()})==106
