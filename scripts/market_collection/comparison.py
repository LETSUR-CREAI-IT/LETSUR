"""Conservative read-only comparison of preserved GPT Image 2.5 public unit rates.

Images denotes each supplier's native standalone generation workflow; Responses
requires a documented image generation tool contract. No cost per image is inferred.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from .core import CollectionError, UNIT_SCALE, _validated_run
from .models import SUNBURST_MODEL, FLARE_MODEL

COMPARISON_BASIS = 'public_token_unit_rate'
QUALITY_COST_CLAUSE = 'Changing the quality parameter significantly affects cost; by default we use high'
QUALITY_USAGE_DESCRIPTION = "Quality for the generated image. Higher settings increase detail, latency, and token usage. Use 'auto' to let the model choose."

METERS = {'text_input','text_cached_input','image_input','image_cached_input','image_output'}
DEFAULT_REQUEST = dict(task='text-to-image',quality='high',size='1024x1024',num_images=1,
                       processing='Standard',api_surface='images',billing_item='image_output')


def _value(row):
    try:return json.loads(row['value'])
    except (ValueError, TypeError):return row['value']


def _status(reasons):
    if any(r.startswith('mismatch:') or r.startswith('unsupported:') for r in reasons):return 'not_comparable'
    return 'verification_required' if reasons else 'comparable'


def _decimal(value):
    try:
        n=Decimal(value)
        return n if n.is_finite() and n>=0 else None
    except (InvalidOperation,TypeError):return None


def _format(n):
    return format(n,'f')


def _meter(label, image_only):
    exact={'Text tokens / Input':'text_input','Text tokens / Cached input':'text_cached_input',
           'Image tokens / Input':'image_input','Image tokens / Cached input':'image_cached_input',
           'Image tokens / Output':'image_output','Text tokens / Output':'text_output','Text Input':'text_input','Image Input':'image_input',
           'Image Output':'image_output','imageInputCost':'image_input'}
    if label in exact:return exact[label]
    if label in ('Output','Output Price') and image_only:return 'image_output'
    return None


def _evidence(row, sources):
    s=sources[row['source_id']]
    return dict(source_id=row['source_id'],condition_set_id=row.get('condition_set_id',''),
                key=row.get('condition_key','price'),locator=row['locator'],raw_sha256=s['raw_sha256'],
                URL=s['effective_url'] or s['requested_url'],fetched_at=s['fetched_at_utc'])


def _field(rows,key,scope,reasons,field_evidence,sources):
    selected=[r for r in rows if r['condition_key']==key]
    field_evidence[scope+'.'+key]=[_evidence(r,sources) for r in selected]
    known=[r for r in selected if r['status']=='stated']
    if any(r['status']=='conflicted' for r in selected):
        reasons.append('conflicting:'+scope+'.'+key);return None
    distinct={json.dumps(_value(r),sort_keys=True) for r in known}
    if len(distinct)>1:
        reasons.append('conflicting:'+scope+'.'+key);return None
    if not known:
        reasons.append('unknown:'+scope+'.'+key);return None
    # A scoped unknown cannot be silently replaced by a conflicting applicable claim.
    if any(r['status']=='unknown' for r in selected):
        reasons.append('unknown:'+scope+'.'+key);return None
    return _value(known[0])


def _quality_cost_evidence(price, own, channel, request, meter, scale, sources):
    # Only the retained task-specific usage explanation can defer this warning.
    if price['route']!='fal' or meter not in METERS or not scale or scale[0]!='token' or request['quality']=='auto':return []
    warnings=[r for r in own if r['condition_key']=='quality_price_applicability']
    if not warnings or any(r['status']!='unknown' or _value(r)!='unverified by token rate' or QUALITY_COST_CLAUSE not in r['locator'] for r in warnings):return []
    if any(r['condition_key']=='quality' for r in own):return []
    groups={}
    for row in channel:groups.setdefault((row['source_id'],row['condition_set_id']),[]).append(row)
    schemas=[];quality_options=[]
    for rows in groups.values():
        scopes=[r for r in rows if r['condition_key']=='profile_scope']
        if not scopes or any(r['status']!='stated' or _value(r)!='channel_task' for r in scopes):continue
        group_schemas=[r for r in rows if r['condition_key']=='quality_schema']
        if not group_schemas:continue
        options=[r for r in rows if r['condition_key']=='quality_options']
        if not options:return []
        schemas.extend(group_schemas);quality_options.extend(options)
    if not schemas or any(r['status']!='stated' for r in schemas):return []
    if len({json.dumps(_value(r),sort_keys=True) for r in schemas})!=1:return []
    schema=_value(schemas[0])
    if not isinstance(schema,dict) or schema.get('type')!='string' or schema.get('description')!=QUALITY_USAGE_DESCRIPTION:return []
    enum=schema.get('enum')
    if not isinstance(enum,list) or not enum or not all(isinstance(v,str) and v for v in enum) or len(set(enum))!=len(enum) or request['quality'] not in enum:return []
    if any(r['status']!='stated' for r in quality_options):return []
    if len({json.dumps(_value(r),sort_keys=True) for r in quality_options})!=1:return []
    options=_value(quality_options[0])
    if not isinstance(options,list) or request['quality'] not in options:return []
    return [_evidence(r,sources) for r in warnings+schemas]


def _request_support(request, profiles, sources, meter, failed, processing_price_basis=None):
    reasons=[];field_evidence={}
    reasons.extend(failed)
    if not profiles.get('channel'):
        reasons.append('channel_api_profile_missing')
    if not profiles.get('manufacturer'):
        reasons.append('manufacturer_api_profile_missing')
    for scope,rows in profiles.items():
        if not rows:continue
        def get(key):return _field(rows,key,scope,reasons,field_evidence,sources)
        for field,selection in [('applicable_tasks',request['task']),('quality_options',request['quality']),('api_surface',request['api_surface']),('processing_options',request['processing'])]:
            if field=='processing_options' and processing_price_basis:
                processing_rows=[r for r in rows if r['condition_key']==field]
                if not processing_rows or all(r['status']=='unknown' for r in processing_rows):
                    field_evidence[scope+'.'+field]=[_evidence(r,sources) for r in processing_rows]
                    continue
            options=get(field)
            if options is not None:
                if not isinstance(options,list):reasons.append('malformed:'+scope+'.'+field)
                elif selection not in options:
                    if field=='api_surface' and selection=='responses':reasons.append('unknown:'+scope+'.responses_contract_missing')
                    else:reasons.append('unsupported:'+scope+'.'+field)
        # Exact presets OR explicitly documented joint numeric rules establish support.
        size=request['size'];presets=[_value(r) for r in rows if r['condition_key']=='size_presets' and r['status']=='stated']
        rules=[_value(r) for r in rows if r['condition_key']=='size_rules' and r['status']=='stated']
        size_rows=[r for r in rows if r['condition_key'] in {'size_presets','size_rules'}]
        field_evidence[scope+'.size']=[_evidence(r,sources) for r in size_rows]
        if any(r['status']=='conflicted' for r in size_rows) or len({json.dumps(x,sort_keys=True) for x in rules})>1 or len({json.dumps(x,sort_keys=True) for x in presets})>1:
            reasons.append('conflicting:'+scope+'.size')
        elif size=='auto':reasons.append('unknown:fixed_size_required')
        elif any(isinstance(x,list) and size in x for x in presets):pass
        elif rules:
            m=re.fullmatch(r'([1-9][0-9]*)x([1-9][0-9]*)',size)
            r=rules[0]
            required={'multiple','max_edge','min_pixels','max_pixels','max_aspect_ratio'}
            if not isinstance(r,dict) or not required<=r.keys() or any(type(r[k]) not in (int,float) or r[k]<=0 for k in required):
                reasons.append('malformed:'+scope+'.size_rules')
            elif not m:reasons.append('unsupported:'+scope+'.size')
            else:
                w,h=map(int,m.groups())
                if w%r['multiple'] or h%r['multiple'] or max(w,h)>r['max_edge'] or not r['min_pixels']<=w*h<=r['max_pixels'] or Decimal(max(w,h))/Decimal(min(w,h))>Decimal(str(r['max_aspect_ratio'])):
                    reasons.append('unsupported:'+scope+'.size')
        elif presets:reasons.append('unsupported:'+scope+'.size')
        else:reasons.append('unknown:'+scope+'.size')
        if request['quality']=='auto':reasons.append('unknown:fixed_quality_required')
        # A documented single-image default establishes support for one image only.
        nr=[r for r in rows if r['condition_key']=='num_images_range' and r['status']=='stated']
        nd=[r for r in rows if r['condition_key']=='num_images_default' and r['status']=='stated']
        if nr:
            count=get('num_images_range')
            if count is not None:
                if not isinstance(count,dict) or any(type(count.get(k)) is not int for k in ('min','max')):reasons.append('malformed:'+scope+'.num_images_range')
                elif not count['min']<=request['num_images']<=count['max']:reasons.append('unsupported:'+scope+'.num_images')
        elif nd and request['num_images']==1 and all(str(_value(r))=='1' for r in nd):
            field_evidence[scope+'.num_images_default']=[_evidence(r,sources) for r in nd]
        else:reasons.append('unknown:'+scope+'.num_images_range')
        if meter in {'text_cached_input','image_cached_input'}:
            # Manufacturer restriction governs its direct API only, not proxy cache contracts.
            if scope=='manufacturer' and profiles.get('channel') != profiles.get('manufacturer'):continue
            cache=get('cache_applicability')
            if cache is not None:
                if not isinstance(cache,dict) or type(cache.get(request['api_surface'])) is not bool:reasons.append('unknown:'+scope+'.cache_applicability')
                elif not cache[request['api_surface']]:reasons.append('unsupported:'+scope+'.cache_inapplicable')
    return _status(reasons),sorted(set(reasons)),field_evidence


def compare_run(run_dir, request):
    run_dir=Path(run_dir)
    query=dict(DEFAULT_REQUEST,**request)
    if query.get('model') not in {SUNBURST_MODEL,FLARE_MODEL}:raise CollectionError('comparison supports only GPT Image 2.5 Sunburst/Flare')
    if query['billing_item'] not in METERS:raise CollectionError('unsupported canonical billing selector')
    if type(query['num_images']) is not int or query['num_images']<1:raise CollectionError('num_images must be a positive integer')
    if any(not isinstance(query[k],str) or not query[k] for k in ('task','quality','size','processing','api_surface')):raise CollectionError('empty or invalid request selector')
    manifest,source_rows,prices,conditions=_validated_run(run_dir)
    sources={s['source_id']:s for s in source_rows}
    model_sources={s['source_id']:s for s in source_rows if s['model_id']==query['model']}
    groups={}
    for row in conditions:
        if row['source_id'] in model_sources:groups.setdefault((row['source_id'],row['condition_set_id']),[]).append(row)
    manufacturer=[];channels={};identities=[]
    for (sid,cid),rows in groups.items():
        scopes=[r['value'] for r in rows if r['condition_key']=='profile_scope' and r['status']=='stated']
        if scopes==['manufacturer_model']:manufacturer.extend(rows)
        if scopes and scopes[0] in {'channel_model','channel_task'}:channels.setdefault(sources[sid]['route'],[]).extend(rows)
        if scopes==['manufacturer_identity']:identities.extend(rows)
    endpoint_sources={item['source']['source_id'] for item in manifest['sources']
                      if item['spec']['parser']=='image25_openrouter_endpoints'
                      and item['spec']['source_key']==f"image25_openrouter_{query['model'].rsplit('-',1)[1]}_endpoints"}
    candidates=[];excluded=[]
    for p in prices:
        if p['model_id']!=query['model']:continue
        if p['task']!=query['task']:
            excluded.append(dict(observation_id=p['observation_id'],reason='unrelated_task',task=p['task']));continue
        own=groups.get((p['source_id'],p['condition_set_id']),[]) if p['condition_set_id'] else []
        binding_reasons=[];binding_refs=[]
        channel=manufacturer if p['route']=='openai' else channels.get(p['route'],[])
        if p['source_id'] in endpoint_sources:
            links=[r for r in own if r['condition_key']=='endpoint_profile_id']
            binding_refs=[_evidence(r,sources) for r in links]
            targets={r['value'] for r in links if r['status']=='stated'}
            channel=[]
            if links and len(targets)==1 and all(r['status']=='stated' for r in links):
                target=groups.get((p['source_id'],next(iter(targets))),[])
                scopes=[r for r in target if r['condition_key']=='profile_scope']
                ids=[r for r in target if r['condition_key']=='model_id']
                if scopes and ids and all(r['status']=='stated' and _value(r)=='channel_endpoint' for r in scopes) and all(r['status']=='stated' and _value(r)==query['model'] for r in ids):
                    channel=target
            if not channel:binding_reasons.append('unknown:channel.endpoint_profile_binding')
        joined=manufacturer+channel+identities
        scoped=[r for r in joined if r['condition_key'] in {'architecture','output_modalities'} and r['status']=='stated']
        outputs=[_value(r).get('output_modalities') if isinstance(_value(r),dict) else _value(r) for r in scoped]
        image_only=bool(outputs) and all(o==['image'] for o in outputs)
        meter=_meter(p['billing_item'],image_only)
        if meter is not None and meter!=query['billing_item']:
            excluded.append(dict(observation_id=p['observation_id'],reason='unrelated_meter',raw_meter=p['billing_item'],canonical_meter=meter));continue
        reasons=list(binding_reasons);cost_reasons=['token_usage_not_observed'];field_evidence={key:[_evidence(p,sources)] for key in ('price.amount','price.unit','price.currency','price.raw_meter','price.model_id','price.model_version')}
        if p['source_id'] in endpoint_sources:
            field_evidence['channel.endpoint_profile_id']=binding_refs
            for key in ('provider_name','provider_slug','provider_tag'):
                field_evidence['channel.'+key]=[_evidence(r,sources) for r in channel if r['condition_key']==key]
        for scope, profile in [('manufacturer',manufacturer),('channel',channel)]:
            ids=[r for r in profile if r['condition_key']=='model_id']
            if not ids:reasons.append('unknown:'+scope+'.model_id')
            if any(r['status']!='stated' for r in ids):reasons.append('unknown:'+scope+'.model_id')
            if any(_value(r)!=query['model'] for r in ids if r['status']=='stated'):reasons.append('mismatch:'+scope+'.model_id')
        if sources[p['source_id']]['fetch_status']!='ok' or sources[p['source_id']]['parse_status']!='ok':reasons.append('failed_price_source')
        if meter is None:reasons.append('unknown:billing_item_semantics')
        amount=_decimal(p['amount'])
        if amount is None:reasons.append('unknown:price_amount')
        if p['unknown_reason']:reasons.append('unknown:price_claim')
        if p['evidence_status']=='conflicted' or p['price_kind']=='disputed_claim':reasons.append('conflicting:price_claim')
        if p['evidence_status']!='direct':reasons.append('unknown:price_evidence')
        scale=UNIT_SCALE.get(p['unit'])
        normalized=_format(amount if p['source_id'] in endpoint_sources and scale[1]==1 else amount/scale[1]) if amount is not None and scale and scale[0]=='token' else None
        if p['currency']!='USD':reasons.append('unknown:currency' if not p['currency'] else 'mismatch:currency')
        if scale is None:reasons.append('unknown:unit')
        elif scale[0]!='token':reasons.append('mismatch:unit_dimension')
        expected={query['model'],'openai/'+query['model'],'openai/gpt-image-2.5/'+query['model'].rsplit('-',1)[1]+'/text-to-image'}
        if p['provider_model_id'] not in expected:
            if p['provider_model_id'] in {SUNBURST_MODEL,FLARE_MODEL}:reasons.append('mismatch:model_sku')
            else:reasons.append('unknown:provider_model_identity')
        cost_refs=_quality_cost_evidence(p,own,channel,query,meter,scale,sources)
        if cost_refs:
            cost_reasons.append('unknown:price.quality_price_applicability')
            field_evidence['cost.quality_price_applicability']=cost_refs
        # Price-local declarations remain authoritative; docs never overwrite them.
        processing_price_basis=None
        if not own:reasons.append('unknown:price_processing')
        else:
            local_processing=[r for r in own if r['condition_key']=='processing']
            if not local_processing:reasons.append('unknown:price_processing')
            else:
                proc=_field(own,'processing','price',reasons,field_evidence,sources)
                if proc is not None and proc!=query['processing']:reasons.append('mismatch:price_processing')
                elif isinstance(proc,str) and proc==query['processing'] and all(r['status']=='stated' for r in local_processing):
                    processing_price_basis=[_evidence(r,sources) for r in local_processing]
            for row in own:
                if row['status'] in {'unknown','conflicted'} and not (cost_refs and row['condition_key']=='quality_price_applicability'):
                    reasons.append(('conflicting:' if row['status']=='conflicted' else 'unknown:')+'price.'+row['condition_key'])
                if row['condition_key'] in {'quality','size','num_images','api_surface'} and row['status']=='stated':
                    selected=query[row['condition_key']]
                    if str(_value(row))!=str(selected):reasons.append('mismatch:price.'+row['condition_key'])
        failed=['failed_api_document:'+s['source_key'] for s in model_sources.values() if s['route'] in {'openai',p['route']} and (s['source_key'].startswith('image25_')) and (s['fetch_status']!='ok' or s['parse_status']!='ok')]
        req_status,req_reasons,refs=_request_support(query,{'manufacturer':manufacturer,'channel':channel},sources,meter,failed,
                                                     processing_price_basis if p['route']=='openai' else None)
        if processing_price_basis and p['route']=='openai':
            refs['request.processing_price_basis']=processing_price_basis
        field_evidence.update(refs)
        if p['billing_item'] in {'Output','Output Price'}:
            field_evidence['billing.image_only_output']=[_evidence(r,sources) for r in scoped]
        relevant=own+joined
        evidence=[_evidence(p,sources)]+[_evidence(r,sources) for r in relevant]
        snapshot_rows = channel + own + (identities if p['route']=='openai' else [])
        snapshots=[_value(r) for r in snapshot_rows if r['condition_key']=='model_snapshot' and r['status']=='stated']
        # Generic model_version labels (e.g. Azure 2.5) are not immutable snapshots.
        field_evidence['snapshot_identity']=[_evidence(r,sources) for r in snapshot_rows if r['condition_key'] in {'model_snapshot','model_default_snapshot'}]
        snapshot= snapshots[0] if snapshots and len(set(map(str,snapshots)))==1 else None
        if len(set(map(str,snapshots)))>1:reasons.append('mismatch:model_version')
        price_status=_status(reasons)
        combined=sorted(set(reasons+req_reasons))
        source=sources[p['source_id']]
        hint=re.search(r'gpt-image-2\.5-(?:sunburst|flare)-(\d{8})(?:$|[/?])',source['requested_url'])
        candidates.append(dict(observation_id=p['observation_id'],source_id=p['source_id'],route=p['route'],
              model_id=p['model_id'],provider_model_id=p['provider_model_id'],model_version=p['model_version'],
              canonical_meter=meter,raw_meter=p['billing_item'],amount=p['amount'],currency=p['currency'],unit=p['unit'],
              normalized_amount=normalized,normalized_unit='1 token' if normalized is not None else None,
              condition_set_id=p['condition_set_id'],request=query,price_condition_status=price_status,
              request_condition_status=req_status,overall_status=_status(combined),reason_codes=combined,
              price_reason_codes=sorted(set(reasons)),request_reason_codes=req_reasons,
              snapshot_identity=snapshot,snapshot_match='unverified',channel_version_hint=hint[1] if hint else None,
              pricing_version_id=(re.search(r'pricing_version_id=([^;]+);',p['locator'])[1] if p['route']=='openrouter' and re.search(r'pricing_version_id=([^;]+);',p['locator']) else None),account_eligibility='unknown',actual_request_cost='unknown',
              comparison_basis=COMPARISON_BASIS,actual_request_cost_status='verification_required',
              request_cost_reason_codes=sorted(set(cost_reasons)),
              evidence=evidence,field_evidence=field_evidence,conditions=relevant))
    candidates.sort(key=lambda c:(c['route'],c['source_id'],c['observation_id']))
    pairs=[]
    for a,b in combinations(candidates,2):
        if a['route']==b['route']:continue
        reasons=sorted(set(a['reason_codes']+b['reason_codes']))
        for key in ('model_id','canonical_meter','currency','normalized_unit'):
            if a[key] is None or b[key] is None or a[key]=='' or b[key]=='':reasons.append('unknown:pair.'+key)
            elif a[key]!=b[key]:reasons.append('mismatch:pair.'+key)
        if a['model_version'] and b['model_version'] and a['model_version']!=b['model_version']:reasons.append('mismatch:pair.model_version')
        snapshot_match='unverified'
        if a['snapshot_identity'] and b['snapshot_identity']:
            if a['snapshot_identity']!=b['snapshot_identity']:reasons.append('mismatch:pair.model_version')
            else:snapshot_match='matched_documented_identity'
        price_reasons=sorted(set(a['price_reason_codes']+b['price_reason_codes']+[r for r in reasons if r.startswith(('mismatch:pair.','unknown:pair.'))]))
        request_reasons=sorted(set(a['request_reason_codes']+b['request_reason_codes']))
        pair=dict(left_observation_id=a['observation_id'],right_observation_id=b['observation_id'],
                  price_condition_status=_status(price_reasons),
                  request_condition_status=_status(request_reasons),
                  overall_status=_status(reasons),reason_codes=sorted(set(reasons)),snapshot_match=snapshot_match,
                  comparison_basis=COMPARISON_BASIS,actual_request_cost='unknown',actual_request_cost_status='verification_required',
                  request_cost_reason_codes=sorted(set(a['request_cost_reason_codes']+b['request_cost_reason_codes'])))
        # Delta is a displayed public unit-rate difference, only after both checks.
        if pair['overall_status']=='comparable' and a['normalized_amount'] is not None and b['normalized_amount'] is not None:
            left,right=Decimal(a['normalized_amount']),Decimal(b['normalized_amount'])
            pair['displayed_unit_rate_delta']=_format(right-left)
            pair['displayed_unit_rate_delta_unit']='USD / 1 token'
            pair['displayed_unit_rate_delta_basis']=COMPARISON_BASIS
            if left!=0:pair['displayed_unit_rate_percent']=_format((right-left)/left*100)
        pairs.append(pair)
    return dict(schema_version=1,comparison_basis=COMPARISON_BASIS,run_id=manifest['run_id'],input_run=str(run_dir.resolve()),request=query,
                rate_scope='named_model_public_unit_rate',snapshot_match='unverified unless both claims explicitly pin identity',
                candidates=candidates,pairs=pairs,excluded=excluded,
                limitations=['No account eligibility, execution, billing, output-quality equivalence or cost-per-image claim.'])


def write_comparison(result, output):
    output=Path(output)
    if output.exists():raise CollectionError('comparison destination already exists')
    input_path=Path(result['input_run'])
    if output.resolve().is_relative_to(input_path.resolve()):raise CollectionError('comparison destination must be outside input run')
    output.mkdir(parents=True,exist_ok=False)
    (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    fields=('left_observation_id','right_observation_id','price_condition_status','request_condition_status','overall_status','reason_codes','snapshot_match','comparison_basis','actual_request_cost','actual_request_cost_status','request_cost_reason_codes','displayed_unit_rate_delta_basis','displayed_unit_rate_delta','displayed_unit_rate_delta_unit','displayed_unit_rate_percent')
    with (output/'comparisons.csv').open('x',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in result['pairs']:writer.writerow({**row,'reason_codes':json.dumps(row['reason_codes'],sort_keys=True),'request_cost_reason_codes':json.dumps(row['request_cost_reason_codes'],sort_keys=True)})
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path);parser.add_argument('--model',required=True,choices=[SUNBURST_MODEL,FLARE_MODEL])
    for key,value in DEFAULT_REQUEST.items():parser.add_argument('--'+key.replace('_','-'),default=value,type=int if key=='num_images' else str)
    parser.add_argument('--output',required=True,type=Path)
    args=vars(parser.parse_args(argv));run=args.pop('run');output=args.pop('output')
    try:destination=write_comparison(compare_run(run,args),output)
    except (CollectionError,OSError,ValueError) as exc:parser.exit(2,f'comparison refused: {exc}\n')
    print(destination)

if __name__=='__main__':main()
