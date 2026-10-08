"""Strict extraction of Ideogram's public GET pricing catalog."""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from scripts.market_collection.core import ADAPTER_PRICE_FIELDS
from .common import llm_condition


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('Ideogram rate must be numeric')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid Ideogram rate') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Ideogram rate must be finite and nonnegative')
    return result


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('catalog timestamp absent')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('catalog timestamp lacks timezone')
    return result


def image_price(spec, provider, value, group, locator, effective=''):
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, '')
    row.update(provider_model_id=provider, model_version='4.5', upstream_provider='Ideogram',
               task=spec.task, billing_item='generated_image', price_kind='published_rate',
               amount=str(value), currency='USD', unit='1 image',
               condition_set_id=group, effective_from=effective, locator=locator, evidence_status='direct')
    return row


def condition(group, key, value, locator, status='stated'):
    return dict(condition_set_id=group, condition_key=key, operator='=', value=str(value), unit='', status=status, locator=locator)


def only(items, key, value):
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        raise ValueError('exact catalog section/group absent or duplicate')
    return matches[0]


def parse_source(spec, body, source_id):
    data = json.loads(body)
    snapshot = data['snapshot_at']
    timestamp(snapshot)
    public = data['public_pricing']
    if public['pricing_unit'] != 'usd_micros':
        raise ValueError('catalog price unit mismatch')
    section = only(public['sections'], 'id', 'image_generation')
    group = only(section['groups'], 'id', 'ideogram_4_5')
    if 'Billed per generated image.' not in section['description'] or 'Billed per generated image,' not in group['description']:
        raise ValueError('per generated image unit absent')
    entries = {}
    expected = {}
    for operation, refs, qualities in [('generate','NONE',('LOW','MEDIUM','HIGH')),
                                     ('generate','INCLUDED',('VERY_LOW','LOW','MEDIUM','HIGH')),
                                     ('precise_edit','INCLUDED',('VERY_LOW','LOW','MEDIUM','HIGH'))]:
        for quality in qualities:
            for resolution in ('1K','2K'):
                expected[(operation,refs,quality,resolution)] = None
    now = datetime.now(timezone.utc)
    for index, row in enumerate(data['entries']):
        identifier = row['billing_identifier']
        if identifier not in ('ideogram_4_5_generate', 'ideogram_4_5_precise_edit'):
            continue
        dims = row['dimensions']
        required_dims = {'rendering_speed','resolution_tier'} | ({'reference_images'} if identifier == 'ideogram_4_5_generate' else set())
        if set(dims) != required_dims:
            raise ValueError('unexpected catalog dimensions')
        key = (identifier.removeprefix('ideogram_4_5_'), dims.get('reference_images','INCLUDED'),dims['rendering_speed'],dims['resolution_tier'])
        if key not in expected or key in entries:
            raise ValueError('unknown or duplicate catalog dimension')
        if row['price']['currency'] != 'USD':
            raise ValueError('catalog currency mismatch')
        value = number(row['price']['amount'])
        if timestamp(row['effective_at']) > now:
            raise ValueError('future catalog effective_at')
        entries[key] = (index,row,value)
    if set(entries) != set(expected):
        raise ValueError('missing exact catalog dimensions')
    offerings = {}
    for key in expected:
        op,refs,quality,resolution = key
        oid = f'ideogram_4_5_{op}_{quality.lower()}_{resolution.lower()}' + ('_with_references' if op == 'generate' and refs == 'INCLUDED' else '')
        offerings[oid] = key
    actual = group['offerings']
    if len(actual) != len(offerings) or {o['id'] for o in actual} != set(offerings):
        raise ValueError('missing duplicate or new public offering')
    matched = {}
    for offering in actual:
        key = offerings[offering['id']]
        op,refs,quality,resolution = key
        endpoint = '/v2/image/' + ('generate' if op == 'generate' else 'precise-edit') + '/ideogram-4-5'
        label = 'Ideogram 4.5 ' + ('Generate' if op == 'generate' else 'Precise Edit') + ' ' + quality.replace('_',' ').title() + ' ' + resolution + (' with source images' if op == 'generate' and refs == 'INCLUDED' else '')
        if offering['label'] != label or offering['endpoint'] != {'method':'POST','path':endpoint} or offering['display_type'] != 'unit_price' or offering['price']['qualifier'] != 'exact':
            raise ValueError('offering label endpoint qualifier or display unit mismatch')
        if number(offering['price']['amount']) / Decimal(1000000) != entries[key][2]:
            raise ValueError('catalog price disagreement')
        matched[key] = offering
    selected = ('generate','NONE') if spec.task == 'text-to-image' else ('generate','INCLUDED') if spec.task == 'image-edit' else ('precise_edit','INCLUDED')
    prices,conditions = [],[]
    for key,(index,row,value) in entries.items():
        op,refs,quality,resolution = key
        if key[:2] != selected:
            continue
        offering = matched[key]
        locator = f"entries[{index}]; public_pricing.sections[image_generation].groups[ideogram_4_5].offerings[{offering['id']}]"
        cid = f'{spec.source_key}_{quality}_{resolution}'
        prices.append(image_price(spec, row['billing_identifier'],row['price']['amount'],cid,locator,row['effective_at']))
        values = dict(quality=quality,resolution_tier=resolution,source_images=refs,operation=op,
                      endpoint=offering['endpoint']['path'],endpoint_method='POST (identity only; not invoked)',
                      price_applicability=group['description'],effective_at=row['effective_at'],snapshot_at=snapshot,
                      snapshot_at_meaning='literal catalog snapshot_at; not collection time, release time, or current-price time')
        conditions.extend(condition(cid,k,v,locator) for k,v in values.items())
        conditions.append(condition(cid,'defaults','unknown: catalog does not state API defaults',locator,'unknown'))
        conditions.append(condition(cid,'pixel_size_rules','unknown: catalog only states resolution tiers',locator,'unknown'))
        if timestamp(snapshot) < timestamp(row['effective_at']):
            conditions.append(condition(cid,'snapshot_effective_relationship','catalog snapshot_at predates row effective_at; snapshot meaning beyond literal field unconfirmed',locator,'unknown'))
    return prices,conditions
