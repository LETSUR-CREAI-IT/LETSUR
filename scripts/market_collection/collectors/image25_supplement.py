"""Condition-only official documents with separate contractual and reference scopes."""
from __future__ import annotations
import re
from html.parser import HTMLParser
from .image25_api import condition, _enum, _range, _size_rules

PARSERS = {'image25_openai_generate', 'image25_azure_howto', 'image25_azure_regions',
           'image25_openrouter_guide', 'image25_vercel_ui', 'image25_fal_guide'}


class _Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def nodes(self, tag=None):
        if self.tag in {'script', 'style', 'template'} or 'hidden' in self.attrs or self.attrs.get('aria-hidden') == 'true':
            return
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes(tag)

    def text(self):
        if self.tag in {'script', 'style', 'template'} or 'hidden' in self.attrs or self.attrs.get('aria-hidden') == 'true':
            return ''
        return re.sub(r'\s+', ' ', ' '.join(c.text() if isinstance(c, _Node) else c for c in self.children)).strip()


class _HTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def _one(nodes, label):
    nodes = list(nodes)
    if len(nodes) != 1:
        raise ValueError('exact unique '+label+' absent or duplicate')
    return nodes[0]


def _section(root, heading):
    node = _one((n for n in root.nodes() if n.tag in {'h2','h3'} and n.text().replace('\u200b','').strip() == heading), heading)
    for parent in root.nodes():
        if node in parent.children:
            siblings = parent.children[parent.children.index(node)+1:]
            parts = []
            for child in siblings:
                if isinstance(child, _Node) and child.tag in {'h1','h2','h3'}:
                    break
                parts.append(child.text() if isinstance(child, _Node) else child)
            return re.sub(r'\s+', ' ', ' '.join(parts)).strip()
    raise ValueError('section parent absent')


def _openai(spec, root, add):
    base = '(resource) images > (method) generate > (params) 0.non_streaming > (param) '
    def prop(name):
        return _one((n for n in root.nodes('div') if n.attrs.get('id') == base+name+' > (schema)'), 'Images generate '+name)
    model = prop('model')
    if spec.model_id not in [n.text() for n in model.nodes('code')]:
        raise ValueError('exact GPT Image 2.5 model absent from generation contract')
    n = prop('n'); text = n.text()
    match = re.search(r'The number of images to generate\. Must be between ([0-9]+) and ([0-9]+)\.', text)
    if not match:
        raise ValueError('scoped generation n contract absent or malformed')
    count = _range(dict(zip(('min','max'), map(int, match.groups()))))
    constraints = {}
    for node in n.nodes('div'):
        if 'stldocs-property-constraint' in (node.attrs.get('class') or '').split():
            spans = [x.text() for x in node.nodes('span')]
            if len(spans) != 2 or spans[0] in constraints:
                raise ValueError('malformed n constraint')
            constraints[spans[0]] = spans[1]
    if constraints.get('minimum') != str(count['min']) or constraints.get('maximum') != str(count['max']):
        raise ValueError('n prose/schema constraints disagree')
    add('profile_scope','manufacturer_supplement',base+'model > (schema)')
    add('evidence_kind','stated_api_contract',base+'n > (schema)')
    add('num_images_range',count,base+'n > (schema): '+match[0])


def _azure(spec, root, add):
    main = _one((n for n in root.nodes('main') if n.attrs.get('id') == 'main'), 'Azure main')
    title = spec.model_id.replace('gpt-image', 'GPT-Image').replace('sunburst','Sunburst').replace('flare','Flare')
    tables = [t for t in main.nodes('table') if title in [n.text() for n in t.nodes('th')]]
    table = _one(tables,'Azure exact model comparison table')
    headers = [n.text() for n in table.nodes('th')]
    if headers.count(title) != 1 or not headers or headers[0] != 'Aspect':
        raise ValueError('Azure exact table header malformed')
    index = headers.index(title); cells = {}
    for tr in table.nodes('tr'):
        td = list(tr.nodes('td'))
        if not td:
            continue
        if len(td) != len(headers) or td[0].text() in cells:
            raise ValueError('Azure table columns malformed or duplicate row')
        cells[td[0].text()] = td[index]
    required = ['Quality Options','Image Sizes / Resolutions','Number of Images per Request',
                'Input / Output Modalities & Format','Editing (inpainting / variations)','Availability']
    if any(k not in cells for k in required):
        raise ValueError('Azure exact model contract row absent')
    quality = _enum([n.text() for n in cells['Quality Options'].nodes('code')])
    size = cells['Image Sizes / Resolutions'].text()
    rules = _size_rules(size.replace('3,840','3840'))
    if not rules:
        raise ValueError('Azure scoped size constraints incomplete')
    count_text = cells['Number of Images per Request'].text()
    count = re.fullmatch(r'([0-9]+)[–-]([0-9]+) images per request',count_text)
    if not count:
        raise ValueError('Azure scoped n range malformed')
    modalities = cells['Input / Output Modalities & Format'].text()
    if modalities != 'Accepts text + image inputs; outputs images.' or cells['Editing (inpainting / variations)'].text() != '✅ Supports inpainting':
        raise ValueError('Azure scoped modality/task contract changed')
    def put(key, value, row, status='stated'):
        add(key,value,'Azure image model comparison > column '+title+' > '+row+': '+cells[row].text(),status)
    add('profile_scope','channel_model','Azure image model comparison > exact column '+title)
    add('evidence_kind','stated_api_contract','Azure image model comparison > exact column '+title)
    put('quality_options',quality,'Quality Options')
    put('size_rules',rules,'Image Sizes / Resolutions')
    put('num_images_range',_range(dict(zip(('min','max'), map(int,count.groups())))),'Number of Images per Request')
    put('input_modalities',['text','image'],'Input / Output Modalities & Format')
    put('output_modalities',['image'],'Input / Output Modalities & Format')
    put('applicable_tasks',['text-to-image','image-edit'],'Editing (inpainting / variations)')
    # This is the Azure OpenAI image-generation how-to; the column describes its Images workflow.
    put('api_surface',['images'],'Input / Output Modalities & Format')
    put('model_availability',cells['Availability'].text(),'Availability')
    for key in ('processing_options','region_deployment_eligibility','account_eligibility','model_snapshot'):
        add(key,'Not documented in the exact-model table; requires verification','Azure exact column '+title+' > '+key,'unknown')


def _regions(spec, root, add):
    main = _one((n for n in root.nodes('main') if n.attrs.get('id') == 'main'), 'Azure region main')
    found = any(spec.model_id in [c.text().lower() for c in tr.nodes('td')] for t in main.nodes('table') for tr in t.nodes('tr'))
    add('profile_scope','reference','Azure region availability tables')
    add('evidence_kind','document_reference','Azure region availability tables')
    add('exact_model_row_found',found,'Azure region availability tables > exact row cell '+spec.model_id)
    add('region_deployment_eligibility','Exact row presence or absence does not establish account deployment eligibility','Azure region availability tables','unknown')


def _openrouter(spec, root, add):
    main = _one((n for n in root.nodes('main') if n.attrs.get('id') == 'content-container'), 'OpenRouter guide main')
    text = _section(main,'Resolution and Aspect Ratio')
    required = ('size : convenience shorthand.', 'A tier size is equivalent to setting resolution',
                'An explicit pixel size is authoritative.', 'rejected with a 400.', 'supported_parameters')
    if any(t not in text for t in required):
        raise ValueError('OpenRouter generic size alias contract changed')
    absent = _one((n for n in main.nodes('span') if n.attrs.get('data-as') == 'p' and n.text() == 'An absent key means the parameter is unsupported by that endpoint.'), 'endpoint capability explanation')
    add('profile_scope','generic_api_reference','Image generation guide > Resolution and Aspect Ratio')
    add('evidence_kind','generic_api_contract','Image generation guide')
    add('size_alias_semantics',dict(tier_alias='resolution',explicit_pixel_size='authoritative when supported',conflicting_resolution_or_aspect_ratio='HTTP400'),'Resolution and Aspect Ratio: '+text)
    add('endpoint_capability_absent_key',absent.text(),'Endpoint capability descriptors: '+absent.text())
    add('exact_pixel_support','Requires the exact endpoint capability; generic aliases do not establish supported pixel dimensions','Resolution and Aspect Ratio','unknown')


def _vercel(spec, root, add):
    article = _one((n for n in root.nodes('article') if n.attrs.get('id') == 'overview'), 'Vercel model overview')
    title = 'GPT Image 2.5 '+spec.model_id.rsplit('-',1)[1].capitalize()
    _one((n for n in article.nodes('h1') if n.text()==title), 'Vercel exact model title')
    choices = {}
    for key,label in (('n','Images to generate'),('size','Size'),('quality','Quality')):
        select = _one((n for n in article.nodes('select') if n.attrs.get('aria-label') == label), 'visible '+label+' select')
        values = _enum(['auto' if n.attrs.get('value') == '' and n.text() == 'Auto' else n.attrs.get('value') for n in select.nodes('option') if n.text()])
        if key == 'n':
            if any(not v.isdecimal() or int(v)<1 for v in values):
                raise ValueError('invalid UI image counts')
            values = [int(v) for v in values]
        choices[key] = values
    add('profile_scope','channel_ui','article#overview > '+spec.model_id)
    add('evidence_kind','official_ui_choices','article#overview > visible select options')
    add('model_page_ui_choices',choices,'article#overview > select[aria-label=Images to generate / Size / Quality]')
    add('exact_model_sdk_contract','UI choices do not establish SDK/API parameter contract','article#overview > visible select options','unknown')


def is_unrendered_fal_guide(body: bytes) -> bool:
    """Recognize the retained Next.js shell without exposing hidden content."""
    root = _HTML(body.decode('utf-8')).root
    articles = []

    def visit(node, streamed=False):
        if not isinstance(node, _Node) or node.tag in {'script', 'style', 'template'}:
            return
        streamed = streamed or (node.tag == 'div' and 'hidden' in node.attrs
                                and re.fullmatch(r'S:\d+', node.attrs.get('id', '')) is not None)
        if node.tag == 'article':
            articles.append((node, streamed))
        for child in node.children:
            visit(child, streamed)

    visit(root)
    if len(articles) != 1 or list(root.nodes('article')):
        return False
    article, streamed = articles[0]
    children = [child for child in article.children if isinstance(child, _Node) or child.strip()]
    return (streamed and 'learn-article-prose' in article.attrs.get('class', '').split()
            and len(children) == 1 and isinstance(children[0], _Node)
            and children[0].tag == 'template' and not children[0].children
            and re.fullmatch(r'P:\d+', children[0].attrs.get('id', '')) is not None)


def _fal(spec, root, add):
    article = _one(root.nodes('article'), 'fal learn article')
    text = _section(article,'How much does GPT Image 2.5 cost on fal?')
    claim = 'Every GPT Image 2.5 request is priced per image returned, with the quality level and the output size setting the rate, and Flare and Sunburst costing the same at every combination.'
    if claim not in text:
        raise ValueError('fal scoped pricing claim changed')
    add('profile_scope','reference','fal learn article > GPT Image 2.5 pricing')
    add('evidence_kind','supplier_claim','fal learn article > GPT Image 2.5 pricing')
    add('marketing_pricing_claim',dict(billing_description='per returned image; quality and size set rate',variant_equivalence='same price at corresponding quality/size combination'),'How much does GPT Image 2.5 cost on fal?: '+claim)
    add('per_image_token_rate_relationship','Supplier per-image description and task token-rate billing relationship requires verification','How much does GPT Image 2.5 cost on fal?: '+claim,'unknown')


def parse_source(spec, body, source_id):
    rows = []; group = 'image25_doc_'+spec.source_key
    def add(key,value,locator,status='stated'):
        rows.append(condition(group,key,value,locator,status))
    add('model_id',spec.model_id,'document registry exact target; profile_scope governs applicability')
    root = _HTML(body.decode('utf-8')).root
    {'image25_openai_generate':_openai,'image25_azure_howto':_azure,'image25_azure_regions':_regions,
     'image25_openrouter_guide':_openrouter,'image25_vercel_ui':_vercel,'image25_fal_guide':_fal}[spec.parser](spec,root,add)
    return [],rows
