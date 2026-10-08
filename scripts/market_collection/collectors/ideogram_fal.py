"""Ideogram fal prices and API conditions scoped to one primary endpoint."""
import json
import re
from .video_common import flight_payloads, objects_with_key
from .ideogram import number, image_price, condition


def parse_source(spec, body, source_id):
    endpoint = 'ideogram/v4.5' + ('' if spec.task == 'text-to-image' else '/edit')
    roots = [obj for payload in flight_payloads(body.decode('utf-8')) for obj in objects_with_key(payload,'modelData')
             if obj.get('endpointId') == endpoint and obj.get('modelData',{}).get('endpointId') == endpoint]
    if len(roots) != 1:
        raise ValueError('fal exact primary endpoint absent or duplicate')
    root = roots[0]
    model = root['model']
    if model.get('id') != endpoint or model.get('modelUrl') != 'https://fal.run/' + endpoint or model.get('status') != 'public' or model.get('category') != ('text-to-image' if spec.task == 'text-to-image' else 'image-to-image'):
        raise ValueError('fal exact model identity absent')
    pricing = model['pricingInfoOverride'].replace('**','')
    qualities = ['low','medium','high'] if spec.task == 'text-to-image' else ['very_low','low','medium','high']
    pattern = r'Your request will cost \$([0-9]+(?:\.[0-9]+)?) per image with ' + ('Low' if len(qualities)==3 else 'Very Low')
    for q in qualities[1:]:
        pattern += (r', and ' if q == 'high' else ', ') + r'\$([0-9]+(?:\.[0-9]+)?) with ' + q.title()
    pattern += r' quality\. The image size does not change the price\.'
    match = re.fullmatch(pattern,pricing)
    if not match:
        raise ValueError('fal exact quality prices/unit/size claim absent')
    schemas = root['modelData']['appData']['metadata']['openapi']['components']['schemas']
    name = 'GenerateInputV45' if spec.task == 'text-to-image' else 'EditInputV45'
    schema = schemas[name]
    props = schema['properties']
    if props['quality']['enum'] != qualities or props['quality'].get('default') not in qualities:
        raise ValueError('fal quality schema mismatch')
    required = ['quality','image_size','num_images'] + (['enable_prompt_expansion'] if len(qualities)==3 else ['image_url','reference_image_urls','mask_url','edit_precision'])
    if not all(k in props for k in required) or not all('default' in props[k] for k in ['image_size','num_images']):
        raise ValueError('fal required scoped API conditions absent')
    if len(qualities)==4 and (props['edit_precision']['enum'] != ['regular','high'] or props['edit_precision'].get('default') not in ['regular','high'] or 'image_url' not in schema['required']):
        raise ValueError('fal edit schema mismatch')
    prices,conditions = [],[]
    for quality,value in zip(qualities,match.groups()):
        number(value)
        cid = f'{spec.source_key}_{quality}'
        locator = f'model.id={endpoint}; model.pricingInfoOverride: {pricing}'
        prices.append(image_price(spec,endpoint,value,cid,locator))
        values = dict(quality=quality,operation='generate' if spec.task != 'precise-edit' else 'precise_edit',
                      source_images='NONE' if spec.task=='text-to-image' else 'INCLUDED',
                      endpoint=endpoint,size_price_applicability='The image size does not change the price.')
        if len(qualities)==4:
            values['edit_precision'] = 'high' if spec.task=='precise-edit' else 'regular'
        conditions.extend(condition(cid,k,v,locator) for k,v in values.items())
        for key in required:
            conditions.append(condition(cid,key+'_schema',json.dumps(props[key],ensure_ascii=False,sort_keys=True),f'modelData.appData.metadata.openapi.components.schemas.{name}.properties.{key}'))
        conditions.append(condition(cid,'quality_default',props['quality']['default'],f'{name}.properties.quality.default'))
        conditions.append(condition(cid,'image_size_default',props['image_size']['default'],f'{name}.properties.image_size.default'))
        if len(qualities)==4 and 'low to very_high for high precision' in model.get('description',''):
            conditions.append(condition(cid,'quality_description_conflict',model['description'],
                                        f'model.description versus {name}.properties.quality.enum', 'conflicted'))
    return prices,conditions
