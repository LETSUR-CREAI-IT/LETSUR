"""Explicitly synthetic fully documented contracts exercise decisions, not live claims."""
import json
import shutil
from pathlib import Path
import pytest
from scripts.market_collection.core import live, SourceSpec, ADAPTER_PRICE_FIELDS, CollectionError
from scripts.market_collection.collectors.image25_api import condition
from scripts.market_collection.comparison import compare_run, write_comparison

MODEL='gpt-image-2.5-sunburst'
QUERY=dict(model=MODEL,task='text-to-image',quality='high',size='1024x1024',num_images=1,processing='Standard',api_surface='images',billing_item='image_output')

class Synthetic:
    def __init__(self,changes=None):self.changes=changes or {}
    def parse_source(self,s,b,i):
        changes=self.changes.get(s.source_key,{})
        vals=dict(profile_scope='manufacturer_model' if s.route=='openai' else 'channel_model',model_id=MODEL,applicable_tasks=['text-to-image','image-edit'],quality_options=['high','low'],size_presets=['1024x1024'],num_images_range={'min':1,'max':10},api_surface=['images','responses'],processing_options=['Standard','Batch'],cache_applicability={'images':False,'responses':True},model_snapshot='pinned-test-snapshot',architecture={'output_modalities':['image'],'input_modalities':['text','image']})
        vals.update(changes.get('facts',{}));rows=[condition('contract',k,v,'synthetic documented contract '+k,changes.get('statuses',{}).get(k,'stated')) for k,v in vals.items() if v is not None]
        if changes.get('conflict'):
            rows.extend([condition('second','profile_scope',vals['profile_scope'],'synthetic second scope'),condition('second', 'quality_options',['low'],'synthetic second claim')])
        if s.task=='model-conditions':return [],rows
        p=dict.fromkeys(ADAPTER_PRICE_FIELDS,'');p.update(provider_model_id=MODEL,upstream_provider='OpenAI',task=s.task,billing_item='Image tokens / Output',price_kind='published_rate',amount='30' if s.route=='openai' else '20',currency='USD',unit='1M tokens',condition_set_id='price',locator='synthetic price',evidence_status='direct');p.update(changes.get('price',{}))
        if not changes.get('omit_processing'):
            rows.extend([condition('price','processing',changes.get('processing','Standard'),'synthetic price processing')])
        rows.extend(changes.get('extra',[]))
        return [p],rows

def run(tmp_path,changes=None,route='fal'):
    specs=[SourceSpec('manufacturer','image',MODEL,'openai','model-conditions','https://example.org/manufacturer','synthetic'),SourceSpec('direct','image',MODEL,'openai','text-to-image','https://example.org/direct','synthetic'),SourceSpec('channel','image',MODEL,route,'text-to-image','https://example.org/channel','synthetic')]
    return live(tmp_path/'evidence',specs=specs,adapters={'image':Synthetic(changes)},fetcher=lambda s:(b'Explicit synthetic fully documented test contract',s.url,200))

def result(tmp_path,changes=None,q=None,route='fal'):
    return compare_run(run(tmp_path,changes,route),dict(QUERY,**(q or {})))

def test_comparable_decimal_and_evidence(tmp_path):
    r=result(tmp_path);assert len(r['candidates'])==2
    assert r['pairs'][0]['overall_status']=='comparable'
    assert r['pairs'][0]['displayed_unit_rate_delta']=='0.00001'
    assert all(c['evidence'] and c['field_evidence'] for c in r['candidates'])
    assert r['pairs'][0]['snapshot_match']=='matched_documented_identity'

@pytest.mark.parametrize(('q_processing','price_processing','api_state'), [
    ('Standard','Standard','absent'),('Batch','Batch','absent'),('Standard','Standard','unknown'),
])
def test_direct_openai_price_processing_fills_unknown_api_gap_with_provenance(tmp_path,q_processing,price_processing,api_state):
    api_changes={'facts':{'processing_options':None}}
    if api_state=='unknown':
        api_changes={'facts':{'processing_options':['Standard']},'statuses':{'processing_options':'unknown'}}
    changes={
        'manufacturer':api_changes,
        'direct':{**api_changes,'processing':price_processing},
        'channel':api_changes,
    }
    r=result(tmp_path,changes,q={'processing':q_processing})
    direct=next(c for c in r['candidates'] if c['route']=='openai')
    proxy=next(c for c in r['candidates'] if c['route']=='fal')
    assert direct['request_condition_status']=='comparable'
    assert direct['field_evidence']['request.processing_price_basis']
    assert {ref['source_id'] for ref in direct['field_evidence']['request.processing_price_basis']}=={direct['source_id']}
    assert direct['field_evidence']['channel.processing_options']==direct['field_evidence']['manufacturer.processing_options']
    assert proxy['request_condition_status']=='verification_required'
    assert 'request.processing_price_basis' not in proxy['field_evidence']

@pytest.mark.parametrize(('changes','q_processing','price_reason'), [
    ({'manufacturer':{'facts':{'processing_options':None}},'direct':{'facts':{'processing_options':None},'processing':'Standard'},'channel':{'facts':{'processing_options':None}},},'Batch','mismatch:price_processing'),
    ({'manufacturer':{'facts':{'processing_options':None}},'direct':{'facts':{'processing_options':None},'omit_processing':True,'extra':[condition('price','quality','high','synthetic price quality')]},'channel':{'facts':{'processing_options':None}},},'Standard',None),
])
def test_direct_openai_price_processing_bypass_requires_matching_stated_price(tmp_path,changes,q_processing,price_reason):
    direct=next(c for c in result(tmp_path,changes,q={'processing':q_processing})['candidates'] if c['route']=='openai')
    assert direct['request_condition_status']=='verification_required'
    assert 'request.processing_price_basis' not in direct['field_evidence']
    assert any(reason.endswith('.processing_options') and reason.startswith('unknown:') for reason in direct['request_reason_codes'])
    if price_reason:
        assert price_reason in direct['price_reason_codes']

@pytest.mark.parametrize('changes,request_reason,request_status', [
    ({'manufacturer':{'facts':{'processing_options':['Batch']}},'direct':{'facts':{'processing_options':None},'processing':'Standard'},'channel':{'facts':{'processing_options':None}}},'unsupported:channel.processing_options','not_comparable'),
    ({'manufacturer':{'facts':{'processing_options':['Standard']}},'direct':{'facts':{'processing_options':['Standard']},'statuses':{'processing_options':'unknown'},'processing':'Standard'},'channel':{'facts':{'processing_options':None}}},'unknown:channel.processing_options','verification_required'),
])
def test_direct_openai_price_processing_does_not_override_explicit_api_declarations(tmp_path,changes,request_reason,request_status):
    direct=next(c for c in result(tmp_path,changes)['candidates'] if c['route']=='openai')
    assert direct['request_condition_status']==request_status
    assert request_reason in direct['request_reason_codes']

@pytest.mark.parametrize('q',[dict(quality='ultra'),dict(size='999x999'),dict(num_images=11),dict(task='precise-edit'),dict(api_surface='chat')])
def test_explicit_unsupported_requests(tmp_path,q):
    assert all(c['request_condition_status']=='not_comparable' for c in result(tmp_path,q=q)['candidates'])

@pytest.mark.parametrize('changes',[
 {'channel':{'facts':{'quality_options':None}}},
 {'channel':{'facts':{'processing_options':None}}},
 {'channel':{'conflict':True}},
 {'channel':{'extra':[condition('price','quality_price_applicability','not verified','synthetic','unknown')]}},
 {'channel':{'price':{'amount':'','currency':'','unit':'','unknown_reason':'not documented'}}},
 {'channel':{'price':{'billing_item':'Input Price'}}},
])
def test_missing_conflicting_and_generic_prices_stay_visible(tmp_path,changes):
    c=next(c for c in result(tmp_path,changes)['candidates'] if c['route']=='fal')
    assert c['overall_status']=='verification_required' and c['reason_codes']

@pytest.mark.parametrize('changes',[
 {'channel':{'processing':'Batch'}},
 {'channel':{'price':{'provider_model_id':'gpt-image-2.5-flare'}}},
 {'channel':{'facts':{'model_snapshot':'different'}}},
 {'channel':{'price':{'unit':'1 image'}}},
])
def test_pair_mismatches(tmp_path,changes):
    r=result(tmp_path,changes);assert r['pairs'][0]['overall_status']=='not_comparable'
    assert 'displayed_unit_rate_delta' not in r['pairs'][0]

def test_cache_images_vs_responses_only_direct_applicability(tmp_path):
    changes={k:{'price':{'billing_item':'Image tokens / Cached input'}} for k in ('direct','channel')}
    r=result(tmp_path,changes,q={'billing_item':'image_cached_input'})
    assert r['candidates'][0]['request_condition_status']=='not_comparable'
    assert r['candidates'][1]['request_condition_status']=='not_comparable' # synthetic channel explicitly documents same rule
    shutil.rmtree(tmp_path/'evidence')
    changes['channel']['facts']={'cache_applicability':None}
    r=result(tmp_path,changes,q={'billing_item':'image_cached_input','api_surface':'responses'})
    assert next(c for c in r['candidates'] if c['route']=='openai')['request_condition_status']=='comparable'
    assert next(c for c in r['candidates'] if c['route']=='fal')['request_condition_status']=='verification_required'

def test_azure_without_contract_visible(tmp_path):
    r=result(tmp_path,{'channel':{'facts':{k:None for k in ('profile_scope','quality_options','applicable_tasks','size_presets','api_surface','processing_options','num_images_range')}}},route='azure_foundry')
    assert len(r['candidates'])==2 and next(c for c in r['candidates'] if c['route']=='azure_foundry')['overall_status']=='verification_required'
    assert 'channel_api_profile_missing' in next(c for c in r['candidates'] if c['route']=='azure_foundry')['reason_codes']

def test_zero_and_blank_differ(tmp_path):
    r=result(tmp_path,{'channel':{'price':{'amount':'0'}}});assert next(c for c in r['candidates'] if c['route']=='fal')['amount']=='0'
    assert r['pairs'][0]['displayed_unit_rate_delta']=='0.00003'
    assert 'displayed_unit_rate_percent' not in r['pairs'][0]

def test_tampered_raw_and_links_rejected(tmp_path):
    p=run(tmp_path);manifest=json.loads((p/'manifest.json').read_text());raw=p/manifest['sources'][0]['source']['raw_path'];raw.write_bytes(b'tampered')
    with pytest.raises(CollectionError,match='integrity'):compare_run(p,QUERY)

def test_output_never_overwrites_input_or_existing_dir(tmp_path):
    p=run(tmp_path);r=compare_run(p,QUERY)
    with pytest.raises(CollectionError):write_comparison(r,p)
    out=tmp_path/'comparison';write_comparison(r,out)
    assert (out/'comparison.json').exists() and (out/'comparisons.csv').exists()
    with pytest.raises(CollectionError):write_comparison(r,out)

def test_narrower_channel_subset_not_conflicting(tmp_path):
    r=result(tmp_path,{'channel':{'facts':{'quality_options':['high'],'num_images_range':{'min':1,'max':1}}}})
    assert r['pairs'][0]['overall_status']=='comparable'

def test_missing_proxy_cache_not_borrowed_from_manufacturer(tmp_path):
    changes={k:{'price':{'billing_item':'Image tokens / Cached input'}} for k in ('direct','channel')}
    changes['channel']['facts']={'cache_applicability':None}
    r=result(tmp_path,changes,q={'billing_item':'image_cached_input'})
    assert next(c for c in r['candidates'] if c['route']=='fal')['request_condition_status']=='verification_required'
    assert next(c for c in r['candidates'] if c['route']=='openai')['request_condition_status']=='not_comparable'

def test_auto_cannot_prove_fixed_condition(tmp_path):
    r=result(tmp_path,{'channel':{'facts':{'quality_options':['auto']}}},q={'quality':'auto'})
    assert all(c['overall_status']!='comparable' for c in r['candidates'])

def test_snapshot_unknown_does_not_pin_channel(tmp_path):
    r=result(tmp_path,{'channel':{'facts':{'model_snapshot':None}}})
    assert r['pairs'][0]['snapshot_match']=='unverified'
    assert next(c for c in r['candidates'] if c['route']=='fal')['snapshot_identity'] is None

def test_generic_output_requires_image_only_evidence(tmp_path):
    r=result(tmp_path,{'channel':{'price':{'billing_item':'Output Price'}}})
    c=next(c for c in r['candidates'] if c['route']=='fal')
    assert c['canonical_meter']=='image_output' and c['field_evidence']['billing.image_only_output']
    shutil.rmtree(tmp_path/'evidence')
    r=result(tmp_path,{'manufacturer':{'facts':{'architecture':None}},'direct':{'facts':{'architecture':None}},'channel':{'facts':{'architecture':None},'price':{'billing_item':'Output Price'}}})
    assert next(c for c in r['candidates'] if c['route']=='fal')['canonical_meter'] is None

def test_versioned_url_hint_is_never_pricing_version_or_pin(tmp_path):
    # Source URL date is channel-specific context, independent of named SKU/API catalog.
    from scripts.market_collection.core import SOURCE_FIELDS
    import csv
    p=run(tmp_path,{'channel':{'facts':{'model_snapshot':None}}},route='openrouter')
    manifest=json.loads((p/'manifest.json').read_text());manifest['sources'][2]['source']['requested_url']='https://openrouter.ai/openai/gpt-image-2.5-sunburst-20260908'
    rows=[m['source'] for m in manifest['sources']]
    with (p/'sources.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=SOURCE_FIELDS);w.writeheader();w.writerows(rows)
    (p/'manifest.json').write_text(json.dumps(manifest))
    c=next(c for c in compare_run(p,QUERY)['candidates'] if c['route']=='openrouter')
    assert c['channel_version_hint']=='20260908' and c['pricing_version_id'] is None and c['snapshot_match']=='unverified' and c['model_id']==MODEL

def test_preserved_currency_mismatch_and_condition_link_tamper(tmp_path):
    import csv
    from scripts.market_collection.core import PRICE_FIELDS
    p=run(tmp_path)
    with (p/'price_observations.csv').open() as f:rows=list(csv.DictReader(f))
    rows[-1]['currency']='EUR'
    with (p/'price_observations.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=PRICE_FIELDS);w.writeheader();w.writerows(rows)
    r=compare_run(p,QUERY);assert r['pairs'][0]['overall_status']=='not_comparable'
    rows[-1]['condition_set_id']='missing'
    with (p/'price_observations.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=PRICE_FIELDS);w.writeheader();w.writerows(rows)
    with pytest.raises(CollectionError,match='condition reference'):compare_run(p,QUERY)

def test_failed_api_document_remains_critical(tmp_path):
    specs=[SourceSpec('image25_manufacturer_api','image',MODEL,'openai','model-conditions','https://example.org/doc','synthetic'),SourceSpec('direct','image',MODEL,'openai','text-to-image','https://example.org/direct','synthetic'),SourceSpec('channel','image',MODEL,'fal','text-to-image','https://example.org/channel','synthetic')]
    class FailedDocument(Synthetic):
        def parse_source(self,s,b,i):
            if s.task=='model-conditions':raise ValueError('synthetic missing exact contract')
            return super().parse_source(s,b,i)
    p=live(tmp_path/'evidence',specs=specs,adapters={'image':FailedDocument()},fetcher=lambda s:(b'explicit synthetic contract',s.url,200))
    r=compare_run(p,QUERY)
    assert len(r['candidates'])==2
    assert all(c['overall_status']=='verification_required' and 'failed_api_document:image25_manufacturer_api' in c['reason_codes'] for c in r['candidates'])

def test_explicit_model_version_mismatch(tmp_path):
    r=result(tmp_path,{'direct':{'price':{'model_version':'snapshot-A'}},'channel':{'price':{'model_version':'snapshot-B'}}})
    assert r['pairs'][0]['overall_status']=='not_comparable'
    assert 'mismatch:pair.model_version' in r['pairs'][0]['reason_codes']

def test_unrelated_task_and_meter_excluded_with_reason(tmp_path):
    r=result(tmp_path,q={'task':'precise-edit'})
    assert r['candidates']==[] and {x['reason'] for x in r['excluded']}=={'unrelated_task'}
    shutil.rmtree(tmp_path/'evidence')
    r=result(tmp_path,q={'billing_item':'text_input'})
    assert r['candidates']==[] and {x['reason'] for x in r['excluded']}=={'unrelated_meter'}

def test_explicit_text_output_is_unrelated_to_image_output(tmp_path):
    r=result(tmp_path,{'channel':{'price':{'billing_item':'Text tokens / Output'}}})
    assert len(r['candidates'])==1
    assert r['excluded'][0]['reason']=='unrelated_meter' and r['excluded'][0]['canonical_meter']=='text_output'

def test_responses_absent_tool_contract_is_unknown(tmp_path):
    r=result(tmp_path,{'channel':{'facts':{'api_surface':['images']}}},q={'api_surface':'responses'})
    c=next(c for c in r['candidates'] if c['route']=='fal')
    assert c['request_condition_status']=='verification_required'
    assert 'unknown:channel.responses_contract_missing' in c['reason_codes']

def test_ambiguous_meter_or_missing_unit_is_not_a_known_pair_mismatch(tmp_path):
    r=result(tmp_path,{'channel':{'price':{'billing_item':'Input Price'}}})
    assert r['pairs'][0]['overall_status']=='verification_required'
    assert 'unknown:pair.canonical_meter' in r['pairs'][0]['reason_codes']
    assert not any(x.startswith('mismatch:') for x in r['pairs'][0]['reason_codes'])


QUALITY_USAGE_DESCRIPTION="Quality for the generated image. Higher settings increase detail, latency, and token usage. Use 'auto' to let the model choose."
QUALITY_COST_LOCATOR='fal task Pricing | Changing the quality parameter significantly affects cost; by default we use high'


def cost_scope_changes():
    return {'channel':{'facts':{'profile_scope':'channel_task','quality_schema':{'type':'string','description':QUALITY_USAGE_DESCRIPTION,'enum':['auto','low','high']}},
                       'extra':[condition('price','quality_price_applicability','unverified by token rate',QUALITY_COST_LOCATOR,'unknown')]}}


@pytest.mark.parametrize('quality',['high','low'])
def test_exact_fal_usage_warning_is_cost_reason_with_both_refs(tmp_path,quality):
    r=result(tmp_path,cost_scope_changes(),q={'quality':quality})
    c=next(c for c in r['candidates'] if c['route']=='fal')
    assert c['price_condition_status']=='comparable'
    assert 'unknown:price.quality_price_applicability' not in c['reason_codes']
    assert c['request_cost_reason_codes']==['token_usage_not_observed','unknown:price.quality_price_applicability']
    refs=c['field_evidence']['cost.quality_price_applicability']
    assert {ref['key'] for ref in refs}=={'quality_price_applicability','quality_schema'}
    assert {ref['source_id'] for ref in refs}=={c['source_id']}
    assert len(refs)==2 and all(ref['raw_sha256'] and ref['locator'] for ref in refs)
    assert any(row['condition_key']=='quality_price_applicability' and row['status']=='unknown' for row in c['conditions'])
    assert r['pairs'][0]['overall_status']=='comparable'
    assert r['pairs'][0]['displayed_unit_rate_delta_basis']=='public_token_unit_rate'


@pytest.mark.parametrize('case',['missing','conflicted','mixed','changed','malformed','unsupported_enum','bad_enum','missing_type','wrong_type','empty_enum','duplicate_enum','empty_enum_value','unknown_options','conflicted_options','mixed_options','conflicting_options','generic_scope','unrelated_source','warning_changed','warning_locator','warning_conflicted','warning_mixed','auto','non_token','unknown_meter','explicit_quality'])
def test_cost_warning_reclassification_guard(tmp_path,case):
    changes=cost_scope_changes();ch=changes['channel'];q={}
    if case=='missing':ch['facts']['quality_schema']=None
    elif case=='conflicted':ch['statuses']={'quality_schema':'conflicted'}
    elif case=='mixed':ch['extra'].append(condition('contract','quality_schema',ch['facts']['quality_schema'],'synthetic unknown schema','unknown'))
    elif case=='changed':ch['facts']['quality_schema']['description']='Higher settings may affect price.'
    elif case=='malformed':ch['facts']['quality_schema']='not a dictionary'
    elif case=='unsupported_enum':ch['facts']['quality_schema']['enum']=['low']
    elif case=='bad_enum':ch['facts']['quality_schema']['enum']='high'
    elif case=='missing_type':ch['facts']['quality_schema'].pop('type')
    elif case=='wrong_type':ch['facts']['quality_schema']['type']='number'
    elif case=='empty_enum':ch['facts']['quality_schema']['enum']=[]
    elif case=='duplicate_enum':ch['facts']['quality_schema']['enum']=['high','high']
    elif case=='empty_enum_value':ch['facts']['quality_schema']['enum']=['high','']
    elif case=='unknown_options':ch['statuses']={'quality_options':'unknown'}
    elif case=='conflicted_options':ch['statuses']={'quality_options':'conflicted'}
    elif case=='mixed_options':ch['extra'].append(condition('contract','quality_options',['high'],'synthetic unknown options','unknown'))
    elif case=='conflicting_options':ch['extra'].append(condition('contract','quality_options',['low'],'synthetic conflicting options'))
    elif case=='generic_scope':ch['facts']['profile_scope']='channel_model'
    elif case=='unrelated_source':
        changes['manufacturer']={'facts':{'quality_schema':ch['facts'].pop('quality_schema')}}
    elif case=='warning_changed':ch['extra']=[condition('price','quality_price_applicability','not verified',QUALITY_COST_LOCATOR,'unknown')]
    elif case=='warning_locator':ch['extra']=[condition('price','quality_price_applicability','unverified by token rate','generic warning','unknown')]
    elif case=='warning_conflicted':ch['extra']=[condition('price','quality_price_applicability','unverified by token rate',QUALITY_COST_LOCATOR,'conflicted')]
    elif case=='warning_mixed':ch['extra'].append(condition('price','quality_price_applicability','different warning',QUALITY_COST_LOCATOR,'stated'))
    elif case=='auto':q={'quality':'auto'}
    elif case=='non_token':ch['price']={'unit':'1 image'}
    elif case=='unknown_meter':ch['price']={'billing_item':'Input Price'}
    elif case=='explicit_quality':ch['extra'].append(condition('price','quality','low','synthetic explicit quality restriction'))
    c=next(c for c in result(tmp_path,changes,q=q)['candidates'] if c['route']=='fal')
    assert any(reason.endswith('price.quality_price_applicability') for reason in c['price_reason_codes'])
    assert c['overall_status']!='comparable'
    assert c['request_cost_reason_codes']==['token_usage_not_observed']
    assert 'cost.quality_price_applicability' not in c['field_evidence']


@pytest.mark.parametrize('guard',['processing','cache','quality'])
def test_cost_reclassification_preserves_other_guards(tmp_path,guard):
    changes=cost_scope_changes();q={}
    if guard=='processing':changes['channel']['processing']='Batch'
    elif guard=='cache':
        for key in ('direct','channel'):changes.setdefault(key,{})['price']={'billing_item':'Image tokens / Cached input'}
        q={'billing_item':'image_cached_input'}
    else:changes['channel']['facts']['quality_options']=['low']
    r=result(tmp_path,changes,q=q)
    assert r['pairs'][0]['overall_status']=='not_comparable'
    assert 'displayed_unit_rate_delta' not in r['pairs'][0]


def test_comparison_purpose_and_cost_fields_agree_in_csv(tmp_path):
    r=result(tmp_path,cost_scope_changes());out=tmp_path/'comparison';write_comparison(r,out)
    import csv
    rows=list(csv.DictReader((out/'comparisons.csv').open()))
    assert r['comparison_basis']=='public_token_unit_rate'
    for item in r['candidates']+r['pairs']:
        assert item['comparison_basis']=='public_token_unit_rate'
        assert item['actual_request_cost']=='unknown'
        assert item['actual_request_cost_status']=='verification_required'
        assert 'token_usage_not_observed' in item['request_cost_reason_codes']
    for row,pair in zip(rows,r['pairs']):
        for key in ('comparison_basis','actual_request_cost','actual_request_cost_status','displayed_unit_rate_delta_basis'):
            assert row[key]==pair[key]
        assert json.loads(row['request_cost_reason_codes'])==pair['request_cost_reason_codes']
    assert 'cost_per_image' not in json.dumps(r) and 'image_cost_delta' not in json.dumps(r)

class EndpointSynthetic(Synthetic):
    """Complete synthetic endpoints isolate binding, independently of live gaps."""
    def __init__(self, binding='valid'):
        self.binding=binding
        super().__init__()
    def parse_source(self,s,b,i):
        if s.source_key!='image25_openrouter_sunburst_endpoints':return super().parse_source(s,b,i)
        prices=[];rows=[]
        for idx,quality in enumerate(['high','low']):
            profile=f'endpoint-{idx}';pricegroup=f'rate-{idx}'
            _,facts=super().parse_source(s,b,i)
            facts=[dict(r,condition_set_id=profile) for r in facts if r['condition_set_id']=='contract']
            for r in facts:
                if r['condition_key']=='profile_scope':r['value']='channel_model' if self.binding=='wrong_scope' else 'channel_endpoint'
                if r['condition_key']=='quality_options':r['value']=json.dumps([quality])
                if self.binding=='wrong_model' and r['condition_key']=='model_id':r['value']='gpt-image-2.5-flare'
            rows+=facts+[condition(profile,'provider_slug',f'provider-{idx}','synthetic endpoint provider')]
            p=dict.fromkeys(ADAPTER_PRICE_FIELDS,'');p.update(provider_model_id=MODEL,upstream_provider='OpenAI',task=s.task,billing_item='Image Output',price_kind='published_rate',amount=str(idx+1),currency='USD',unit='1 token',condition_set_id=pricegroup,locator='synthetic endpoint rate',evidence_status='direct')
            prices.append(p);rows.append(condition(pricegroup,'processing','Standard','synthetic selected price'))
            if self.binding!='missing':
                rows.append(condition(pricegroup,'endpoint_profile_id','contract' if self.binding=='foreign' else 'absent' if self.binding=='broken' else profile,'synthetic explicit binding','conflicted' if self.binding=='conflicted' else 'stated'))
            if self.binding=='duplicate':rows.append(condition(pricegroup,'endpoint_profile_id',f'endpoint-{1-idx}','synthetic conflicting binding'))
        return prices,rows

def endpoint_run(tmp_path,binding='valid'):
    specs=[SourceSpec('manufacturer','image',MODEL,'openai','model-conditions','https://example.org/manufacturer','synthetic'),SourceSpec('catalog','image',MODEL,'openrouter','model-conditions','https://example.org/catalog','synthetic'),SourceSpec('image25_openrouter_sunburst_endpoints','image',MODEL,'openrouter','text-to-image','https://example.org/endpoints','image25_openrouter_endpoints')]
    return live(tmp_path/'evidence',specs=specs,adapters={'image':EndpointSynthetic(binding)},fetcher=lambda s:(b'explicit synthetic endpoint profiles',s.url,200))

def test_endpoint_prices_use_own_profiles_not_union_or_neighbor(tmp_path):
    r=compare_run(endpoint_run(tmp_path),QUERY);a,b=sorted(r['candidates'],key=lambda c:c['amount'])
    assert a['overall_status']=='comparable'
    assert b['request_condition_status']=='not_comparable' and 'unsupported:channel.quality_options' in b['reason_codes']
    for idx,c in enumerate([a,b]):
        provider=[r for r in c['conditions'] if r['condition_key']=='provider_slug']
        assert len(provider)==1 and provider[0]['value']==f'provider-{idx}'
        assert c['field_evidence']['channel.provider_slug'][0]['source_id']==c['source_id']
        assert all(r['condition_set_id']!=f'endpoint-{1-idx}' for r in c['conditions'])

@pytest.mark.parametrize('binding',['missing','broken','conflicted','duplicate','wrong_model','wrong_scope','foreign'])
def test_invalid_endpoint_link_never_falls_back_to_catalog(tmp_path,binding):
    r=compare_run(endpoint_run(tmp_path,binding),QUERY)
    assert all(c['overall_status']=='verification_required' for c in r['candidates'])
    assert all('unknown:channel.endpoint_profile_binding' in c['reason_codes'] for c in r['candidates'])
    assert all(c['field_evidence']['channel.endpoint_profile_id'] or binding=='missing' for c in r['candidates'])
    assert all(not any(x['source_id']!=c['source_id'] for x in c['field_evidence']['channel.endpoint_profile_id']) for c in r['candidates'])

def test_endpoint_single_token_rate_retains_decimal_precision(tmp_path):
    class PreciseEndpoint(EndpointSynthetic):
        def parse_source(self,s,b,i):
            prices,rows=super().parse_source(s,b,i)
            if s.parser=='image25_openrouter_endpoints':
                for p in prices:p['amount']='0.00000012345678901234567890123456789'
            return prices,rows
    specs=[SourceSpec('manufacturer','image',MODEL,'openai','model-conditions','https://example.org/manufacturer','synthetic'),SourceSpec('image25_openrouter_sunburst_endpoints','image',MODEL,'openrouter','text-to-image','https://example.org/endpoints','image25_openrouter_endpoints')]
    run=live(tmp_path/'evidence',specs=specs,adapters={'image':PreciseEndpoint()},fetcher=lambda s:(b'synthetic precise rate',s.url,200))
    assert all(c['normalized_amount']==c['amount'] for c in compare_run(run,QUERY)['candidates'])
