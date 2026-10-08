"""Condition-only GPT Image 2.5 contracts, scoped to exact public documents.

No requests are executed; capability metadata does not establish a pinned version.
"""
from __future__ import annotations
import html
import json
import re
from .video_common import flight_payloads, objects_with_key

PARSERS = {'image25_openai_guide', 'image25_fal_api', 'image25_openrouter_api', 'image25_vercel_api'}
ESSENTIAL = ('applicable_tasks', 'quality_options', 'quality_default', 'size_rules', 'size_presets',
             'num_images_default', 'num_images_range', 'api_surface', 'native_api_surface',
             'processing_options', 'cache_applicability', 'model_snapshot')


def condition(group, key, value, locator, status='stated'):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return dict(condition_set_id=group, condition_key=key, operator='=', value=value,
                unit='', status=status, locator=locator)


def _enum(value):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value) or len(set(value)) != len(value):
        raise ValueError('invalid enum capability')
    return value


def _range(value):
    if not isinstance(value, dict) or any(type(value.get(k)) is not int for k in ('min', 'max')) or value['min'] < 0 or value['max'] < value['min']:
        raise ValueError('invalid range capability')
    return {k: value[k] for k in ('min', 'max')}


def _size_rules(text):
    # Decode only an explicit joint constraint statement, not a neighboring schema.
    if ('multiples of 16' in text and '3840' in text and '655,360' in text and '8,294,400' in text
            and ('between 1:3 and 3:1' in text or 'aspect ratio <= 3:1' in text)):
        return dict(multiple=16, max_edge=3840, min_pixels=655360, max_pixels=8294400, max_aspect_ratio=3)
    return None


def _openai(spec, text, add):
    # Remove code before reading prose; exclude explicitly older-model material.
    text = re.sub(r'```.*?```', '', text, flags=re.S).split('## Earlier GPT Image models')[0]
    if not all(f'`gpt-image-2.5-{v}`' in text for v in ('sunburst', 'flare')):
        raise ValueError('GPT Image 2.5 guide identities absent')
    add('profile_scope', 'manufacturer_model', 'Overview > GPT Image 2.5 identities')
    intro = text.split('## Generate Images')[0]
    if 'generate and edit images' in intro:
        add('applicable_tasks', ['text-to-image', 'image-edit'], 'Overview: generate and edit images')
    if 'Image API' in intro and 'Responses API' in intro:
        add('api_surface', ['images', 'responses'], 'Overview: Image API and Responses API image generation tool')
        add('native_api_surface', ['OpenAI Images API', 'Responses API image_generation tool'], 'Overview: APIs')
    section = re.search(r'### Size and quality options\s*(.*?)(?=\n### |\n## |\Z)', text, re.S)
    if not section or not all(f'`gpt-image-2.5-{v}`' in section[1] for v in ('sunburst', 'flare')):
        raise ValueError('GPT Image 2.5 scoped size/quality section absent')
    s = section[1]
    row = re.search(r'^\| Quality\s*\|([^\n]+)\|', s, re.M)
    if not row:
        raise ValueError('quality options absent')
    add('quality_options', _enum(re.findall(r'`([^`]+)`', row[1])), 'Size and quality options > '+row[0])
    if 'Both default to `auto`' in s:
        add('quality_default', 'auto', 'Size and quality options: Both default to `auto`')
    presets = re.search(r'^\| Recommended sizes\s*\|([^\n]+)\|', s, re.M)
    if presets:
        add('size_presets', re.findall(r'`([0-9]+x[0-9]+)`', presets[1]), 'Size and quality options > '+presets[0])
    rules = _size_rules(s)
    if rules:
        add('size_rules', rules, 'Size and quality options: '+next(line for line in s.splitlines() if 'multiples of 16' in line))
    if 'by default, the API returns a single image' in text:
        add('num_images_default', '1', 'Generate Images: by default, the API returns a single image')
    cached = re.search(r'### Cached input pricing\s*(.*?)(?=\n### |\n## |\Z)', text, re.S)
    if cached and 'For GPT Image 2 and GPT Image 2.5' in cached[1] and 'only to the image generation tool in the Responses API' in cached[1] and "doesn't apply to direct Images API requests" in cached[1]:
        add('cache_applicability', {'images': False, 'responses': True}, 'Cached input pricing: '+cached[1].strip())
    for key, pattern in [('output_format_options', r'The default format is `png`, but you can also request `jpeg` or `webp`'), ('background_options', r'For transparent backgrounds with either model')]:
        if re.search(pattern, text):
            add(key, ['png','jpeg','webp'] if key.startswith('output') else ['auto','transparent','opaque'], 'Customize Image Output > '+key)


def _fal(spec, text, add):
    variant = spec.model_id.rsplit('-',1)[1]
    endpoint = f'openai/gpt-image-2.5/{variant}/text-to-image'
    roots = [o for p in flight_payloads(text) for o in objects_with_key(p, 'modelData') if o.get('endpointId') == endpoint]
    if len(roots) != 1:
        raise ValueError('fal exact primary endpoint absent or duplicate')
    root = roots[0]
    if root.get('model',{}).get('id') != endpoint or root.get('modelData',{}).get('endpointId') != endpoint or root['model'].get('modelUrl') != 'https://fal.run/'+endpoint:
        raise ValueError('fal endpoint identity mismatch')
    try:
        api = root['modelData']['appData']['metadata']['openapi']
        path = '/'+variant+'/text-to-image'
        op = api['paths'][path]['post']
        ref = op['requestBody']['content']['application/json']['schema']['$ref']
        if not ref.startswith('#/components/schemas/'):
            raise ValueError('external input schema ref')
        name = ref.split('/')[-1]
        schema = api['components']['schemas'][name]
        props, required = schema['properties'], schema['required']
        if schema.get('type') != 'object' or not isinstance(required,list) or any(not isinstance(k,str) or k not in props for k in required) or 'prompt' not in required:
            raise ValueError('invalid required input schema')
        expected = {'quality':'string','num_images':'integer','prompt':'string'}
        if any(props[k].get('type') != t for k,t in expected.items()) or 'image_size' not in props:
            raise ValueError('invalid required property type')
        quality = _enum(props['quality']['enum'])
        if props['quality'].get('default') not in quality:
            raise ValueError('invalid quality default')
        count = _range({'min':props['num_images']['minimum'], 'max':props['num_images']['maximum']})
        default = props['num_images'].get('default')
        if type(default) is not int or not count['min'] <= default <= count['max']:
            raise ValueError('invalid count default')
    except (KeyError, TypeError) as exc:
        raise ValueError('fal selected input schema incomplete') from exc
    loc = 'modelData.appData.metadata.openapi'
    add('profile_scope','channel_task', 'model.id='+endpoint)
    add('applicable_tasks',['text-to-image'],loc+'.paths.'+path+'.post')
    add('api_surface',['images'],loc+'.paths.'+path+'.post')
    add('native_api_surface','fal task endpoint', 'model.modelUrl=https://fal.run/'+endpoint)
    add('api_endpoint','https://fal.run/'+endpoint,'model.modelUrl')
    add('api_method','POST',loc+'.paths.'+path+'.post')
    add('quality_options',quality,loc+'.components.schemas.'+name+'.properties.quality.enum')
    add('quality_default',props['quality']['default'],loc+'.components.schemas.'+name+'.properties.quality.default')
    add('num_images_range',count,loc+'.components.schemas.'+name+'.properties.num_images')
    add('num_images_default',str(default),loc+'.components.schemas.'+name+'.properties.num_images.default')
    rules = _size_rules(props['image_size'].get('description',''))
    if rules:
        add('size_rules',rules,loc+'.components.schemas.'+name+'.properties.image_size.description')
    for key, value in props.items():
        add(key+'_schema',value,loc+'.components.schemas.'+name+'.properties.'+key)
    add('required_inputs',required,loc+'.components.schemas.'+name+'.required')
    sync = props.get('sync_mode', {})
    if (sync.get('type') == 'boolean' and sync.get('description') == "If `True`, the media will be returned as a data URI and the output data won't be available in the request history."):
        add('sync_mode_meaning','data URI output; result not retained in request history',loc+'.components.schemas.'+name+'.properties.sync_mode.description')

    # Referenced component is preserved, not promoted over the scoped joint rules.
    for sub in props['image_size'].get('anyOf',[]):
        if '$ref' in sub:
            refname = sub['$ref'].split('/')[-1]
            if refname not in api['components']['schemas']:
                raise ValueError('missing referenced image size schema')
            add('referenced_size_schema',api['components']['schemas'][refname],loc+'.components.schemas.'+refname)
        if 'enum' in sub:
            add('size_presets',_enum(sub['enum']),loc+'.components.schemas.'+name+'.properties.image_size.anyOf.enum')
    add('size_default',props['image_size'].get('default','not documented'),loc+'.components.schemas.'+name+'.properties.image_size.default', 'stated' if 'default' in props['image_size'] else 'unknown')


def _openrouter(spec, text, add):
    data = json.loads(text)
    if not isinstance(data.get('data'),list):
        raise ValueError('image model catalog data absent')
    targets = [x for x in data['data'] if isinstance(x,dict) and x.get('id') == 'openai/'+spec.model_id]
    if len(targets) != 1:
        raise ValueError('exact unique OpenRouter model absent')
    x = targets[0]; loc = 'data[id='+x['id']+']'
    add('profile_scope','channel_model',loc+'.id')
    add('api_surface',['images'],loc+'.endpoints (dedicated Image API catalog)')
    add('native_api_surface','OpenRouter dedicated Image API',loc+'.endpoints')
    if isinstance(x.get('endpoints'),str):
        add('api_endpoint',x['endpoints'],loc+'.endpoints')
    arch = x.get('architecture')
    if arch is not None:
        if not isinstance(arch,dict) or any(not isinstance(arch.get(k),list) for k in ('input_modalities','output_modalities')):
            raise ValueError('malformed architecture')
        add('architecture',arch,loc+'.architecture')
        description = x.get('description', '')
        if arch['output_modalities'] == ['image'] and isinstance(description, str) and 'image generation and editing model' in description:
            add('applicable_tasks',['text-to-image','image-edit'],loc+'.description: '+description)
    parameters = x.get('supported_parameters')
    if parameters is None:
        return
    if not isinstance(parameters,dict):
        raise ValueError('malformed supported_parameters')
    for key,d in parameters.items():
        if d is None:
            add(key+'_capability','null capability is unknown',loc+'.supported_parameters.'+key,'unknown');continue
        if not isinstance(d,dict) or d.get('type') not in {'enum','range','boolean'}:
            raise ValueError('malformed capability descriptor')
        if d['type']=='enum':_enum(d.get('values'))
        elif d['type']=='range':_range(d)
        add(key+'_capability',d,loc+'.supported_parameters.'+key)
        if key=='quality' and d['type']=='enum':add('quality_options',d['values'],loc+'.supported_parameters.quality.values')
        if key=='n' and d['type']=='range':add('num_images_range',_range(d),loc+'.supported_parameters.n')


def _vercel(spec,text,add):
    # The announcement article, never neighboring recommendations or navigation.
    articles = re.findall(r'<article\b[^>]*>(.*?)</article>',text,re.S)
    scoped = [x for x in articles if 'GPT Image 2.5' in x and 'generateImage' in x and 'openai/'+spec.model_id in x]
    if len(scoped)!=1:
        raise ValueError('unique exact Vercel announcement article absent')
    plain = html.unescape(re.sub('<[^>]+>',' ',scoped[0]))
    plain = re.sub(r'\s+',' ',plain)
    if 'Both models accept text prompts and reference images for generation and editing.' not in plain:
        raise ValueError('Vercel scoped generation/edit contract absent')
    add('profile_scope','channel_model','article > GPT Image 2.5 announcement')
    add('applicable_tasks',['text-to-image','image-edit'],'article: Both models accept text prompts and reference images for generation and editing.')
    add('api_surface',['images'],'article > generateImage model binding')
    add('native_api_surface','Vercel AI SDK generateImage','article > generateImage')
    add('provider_model_id','openai/'+spec.model_id,'article > model: openai/'+spec.model_id)
    add('input_modalities',['text','image'],'article: text prompts and reference images')


def parse_source(spec,body,source_id):
    if spec.parser not in PARSERS:
        raise ValueError('unhandled Image 2.5 contract parser')
    rows=[];group='image25_api_'+spec.source_key
    def add(key,value,locator,status='stated'):
        rows.append(condition(group,key,value,locator,status))
    add('model_id',spec.model_id,'exact document model identity')
    {'image25_openai_guide':_openai,'image25_fal_api':_fal,'image25_openrouter_api':_openrouter,'image25_vercel_api':_vercel}[spec.parser](spec,body.decode('utf-8'),add)
    present={r['condition_key'] for r in rows}
    for key in ESSENTIAL:
        if key not in present:
            add(key,'Not documented in the scoped public contract; requires verification',spec.source_key+' > '+key,'unknown')
    return [],rows
