"""Scoped retained public contracts; fixture provenance.json preserves original hashes."""
import json
from dataclasses import asdict
from pathlib import Path
import pytest
from scripts.market_collection.sources import source_specs
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.core import _validate_parse
from tests.market_collection.retired_sources import without_retired

F=Path(__file__).parent/'fixtures'/'image25_api'

def spec(key):
    return next(s for s in source_specs() if s.source_key==key)

def parse(key,body):
    s=spec(key)
    return _validate_parse(s,'doc-source',*parse_source(s,body,'doc-source'))

def facts(rows):
    return {r['condition_key']:r for r in rows}

@pytest.mark.parametrize('variant',['sunburst','flare'])
@pytest.mark.parametrize('supplier,file',[('openai','openai.md'),('fal',None),('openrouter','openrouter.json'),('vercel','vercel.html')])
def test_separate_condition_only_contracts(variant,supplier,file):
    key=f'image25_{supplier}_{variant}_api'
    b=(F/(file or f'fal_{variant}_api.json')).read_bytes()
    prices,rows=parse(key,b);f=facts(rows)
    assert prices==[] and f['profile_scope']['value']
    assert f['model_id']['value']==f'gpt-image-2.5-{variant}'
    assert f['model_snapshot']['status']=='unknown'
    assert all(r['source_id']=='doc-source' and r['locator'] for r in rows)
    if supplier=='fal':
        assert f['quality_default']['value']=='high'
        assert json.loads(f['num_images_range']['value'])=={'min':1,'max':10}
        assert f['processing_options']['status']=='unknown'
    if supplier=='openai':
        assert f['quality_default']['value']=='auto'
        assert 'max' in json.loads(f['quality_options']['value'])
        assert json.loads(f['cache_applicability']['value'])=={'images':False,'responses':True}

@pytest.mark.parametrize('change',['identity','duplicate','missing_schema','bad_required','bad_type'])
def test_fal_invalid_primary_schema_fails(change):
    d=json.loads((F/'fal_sunburst_api.json').read_bytes())
    if change=='identity':d['model']['id']='neighbor-model'
    if change=='missing_schema':d['modelData']['appData']['metadata']['openapi']['components']['schemas'].pop('GPTImage25TextToImageRequest')
    if change=='bad_required':d['modelData']['appData']['metadata']['openapi']['components']['schemas']['GPTImage25TextToImageRequest']['required']='prompt'
    if change=='bad_type':d['modelData']['appData']['metadata']['openapi']['components']['schemas']['GPTImage25TextToImageRequest']['properties']['num_images']['type']='string'
    b=json.dumps(d).encode()
    if change=='duplicate':b=b+b
    with pytest.raises(ValueError):parse('image25_fal_sunburst_api',b)

@pytest.mark.parametrize('change',['wrong','duplicate','null','bad_descriptor'])
def test_catalog_target_and_descriptor_validation(change):
    d=json.loads((F/'openrouter.json').read_bytes());x=d['data'][0]
    if change=='wrong':x['id']='neighbor'
    if change=='duplicate':d['data'].append(x)
    if change=='null':x['supported_parameters']=None
    if change=='bad_descriptor':x['supported_parameters']['quality']['values']='high'
    if change=='null':
        _,r=parse('image25_openrouter_sunburst_api',json.dumps(d).encode());assert facts(r)['quality_options']['status']=='unknown'
    else:
        with pytest.raises(ValueError):parse('image25_openrouter_sunburst_api',json.dumps(d).encode())

def test_old_ordered_registry_preserved():
    # Immutable complete 103-spec fixture captured before this extension (minus the retired sources).
    old=without_retired(json.loads((F/'baseline103.json').read_text()))
    assert [asdict(s) for s in source_specs()[:len(old)]]==old
    assert len(source_specs())==178

def test_guide_excludes_neighbor_model_and_code_only_claims():
    b=(F/'openai.md').read_bytes()
    b+=b'\n## Earlier GPT Image models\n| Quality | `ultra` |\n'
    _,rows=parse('image25_openai_sunburst_api',b)
    assert 'ultra' not in json.loads(facts(rows)['quality_options']['value'])
    with pytest.raises(ValueError):
        parse('image25_openai_sunburst_api',b'```\n'+(F/'openai.md').read_bytes()+b'\n```')

def test_new_quality_enum_retained_without_whitelist_drop():
    d=json.loads((F/'fal_sunburst_api.json').read_bytes())
    d['modelData']['appData']['metadata']['openapi']['components']['schemas']['GPTImage25TextToImageRequest']['properties']['quality']['enum'].append('future-quality')
    _,rows=parse('image25_fal_sunburst_api',json.dumps(d).encode())
    assert 'future-quality' in json.loads(facts(rows)['quality_options']['value'])

def test_default_snapshot_is_not_pinned_and_old_prices_links_preserved():
    from scripts.market_collection.collectors import openai
    from scripts.market_collection.models import SUNBURST_MODEL
    s=spec('openai_sunburst');b=(Path(__file__).parent/'fixtures'/'openai_sunburst.md').read_bytes()
    old_p,old_c=openai._sun_openai(s,b.decode())
    new_p,new_c=parse('openai_sunburst',b)
    assert [{k:v for k,v in p.items() if k!='normalized_amount' and k!='normalized_unit'} for p in new_p]==[{k:v for k,v in p.items() if k!='normalized_amount' and k!='normalized_unit'} for p in old_p]
    assert all(dict(r,source_id='doc-source') in new_c for r in old_c)
    assert all(p['condition_set_id']!=f'image25_identity_{s.source_key}' for p in new_p)
    assert facts(new_c)['model_snapshot']['status']=='unknown'

def test_catalog_image_inputs_do_not_by_themselves_prove_editing():
    d=json.loads((F/'openrouter.json').read_bytes());d['data'][0]['description']='Image input capability only; operation not specified.'
    _,rows=parse('image25_openrouter_sunburst_api',json.dumps(d).encode())
    assert facts(rows)['applicable_tasks']['status']=='unknown'

@pytest.mark.parametrize('change',['wrong_id','duplicate_article'])
def test_vercel_exact_article_required(change):
    b=(F/'vercel.html').read_bytes()
    if change=='wrong_id':b=b.replace(b'openai/gpt-image-2.5-sunburst',b'openai/neighbor-model')
    else:b=b+b
    with pytest.raises(ValueError):parse('image25_vercel_sunburst_api',b)
