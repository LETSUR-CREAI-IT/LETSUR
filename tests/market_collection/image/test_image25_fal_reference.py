"""Reference data is source-local and never a token tariff."""
import json
from pathlib import Path

import pytest

from scripts.market_collection.collectors.fal import parse_source
from scripts.market_collection.sources import source_specs

F = Path(__file__).parent / 'fixtures' / 'image25_api'


def parse(variant, body):
    spec = next(s for s in source_specs() if s.source_key == f'fal_{variant}_t2i')
    return parse_source(spec, body, 'test-source')


def fixture(variant):
    return (F / f'fal_{variant}_task_reference.rsc').read_text()


def reference(rows):
    return {r['condition_key']: r for r in rows if r['condition_set_id'].endswith('_reference')}


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
def test_complete_reference_matrix(variant):
    body = fixture(variant)
    prices, rows = parse(variant, body.encode())
    ref = reference(rows)
    matrix = json.loads(ref['reference_matrix']['value'])
    assert len(prices) == 6 and len(matrix['cells']) == 30
    assert matrix['qualities'] == ['low', 'medium', 'high', 'xhigh', 'max']
    cell = next(c for c in matrix['cells'] if c['size'] == '1024×1024' and c['quality'] == 'high')
    assert cell['raw_amount'] == '$0.05268' and cell['numeric_text'] == '0.05268'
    assert cell['width'] == cell['height'] == 1024 and cell['locator']
    assert matrix['currency_symbol'] == '$'
    assert ref['reference_billing_scope']['status'] == 'unknown'
    assert '$D2026-' in ref['reference_content_metadata']['value']
    assert all(p['condition_set_id'] not in {r['condition_set_id'] for r in ref.values()} for p in prices)
    wrapped = '<script>self.__next_f.push(' + json.dumps([1, body]) + ')</script>'
    if '<p>' in body:
        wrapped += body[body.index('<p>'):]
    assert parse(variant, wrapped.encode()) == (prices, rows)


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
@pytest.mark.parametrize('change', ['absent', 'neighbor', 'sidebar', 'duplicate', 'column', 'row', 'nan', 'negative', 'size', 'missing_cell', 'type', 'malformed_json', 'separator', 'extra_row'])
def test_optional_reference_failure_retains_token_prices(variant, change):
    body = fixture(variant)
    before, _ = parse(variant, body.encode())
    if change == 'absent': body = body[body.index('\n') + 1:]
    elif change == 'neighbor': body = body.replace('/models/openai/', '/models/neighbor/')
    elif change == 'sidebar': body = body.replace('"slot":"main"', '"slot":"sidebar"')
    elif change == 'duplicate': body = body + '\n' + body.split('\n')[0]
    elif change == 'column': body = body.replace('| low | medium |', '| low | low |')
    elif change == 'row': body = body.replace('1024×1024', '1024×768')
    elif change == 'nan': body = body.replace('$0.00402', '$NaN')
    elif change == 'negative': body = body.replace('$0.00402', '$-0.00402')
    elif change == 'size': body = body.replace('1024×768', 'unknown-size')
    elif change == 'missing_cell': body = body.replace(' | $0.00402', '')
    elif change == 'type': body = body.replace('"type":"markdown"', '"type":"html"')
    elif change == 'malformed_json': body = body.replace('"id":', '"id" BROKEN:')
    elif change == 'separator': body = body.replace('|---|---:|', '|--|---:|')
    elif change == 'extra_row': body = body.replace('\\n\\n**This implies', '\\n| 4096×4096 | $0 | $0 | $0 | $0 | $0 |\\n\\n**This implies')
    prices, rows = parse(variant, body.encode())
    ref = reference(rows)
    assert prices == before and 'reference_matrix' not in ref
    status = ref['reference_status']
    assert status['status'] == ('conflicted' if change == 'duplicate' else 'unknown')
    assert ref['reference_unknown_reason']['value'] and status['locator']


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
def test_zero_and_whitespace_are_preserved(variant):
    body = fixture(variant).replace('$0.00402', '$0.00000').replace('"contentData":', '"contentData": \n ')
    _, rows = parse(variant, body.encode())
    matrix = json.loads(reference(rows)['reference_matrix']['value'])
    assert matrix['cells'][0]['numeric_text'] == '0.00000'
    assert matrix['cells'][0]['raw_amount'] == '$0.00000'


@pytest.mark.parametrize('variant', ['sunburst', 'flare'])
def test_neighbor_and_sidebar_do_not_replace_exact_record(variant):
    body = fixture(variant)
    record = body.split('\n')[0]
    neighbor = record.replace('/models/openai/', '/models/neighbor/').replace('$0.05268', '$9.99')
    sidebar = record.replace('"slot":"main"', '"slot":"sidebar"').replace('$0.05268', '$8.88')
    assert parse(variant, (neighbor + '\n' + body + '\n' + sidebar).encode()) == parse(variant, body.encode())
