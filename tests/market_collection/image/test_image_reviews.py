"""Execute the real report CLI over validated retained fixture runs."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from scripts.market_collection.core import live, SourceSpec, ADAPTER_PRICE_FIELDS
from scripts.market_collection.collectors.image25_api import condition

MODELS = ('gpt-image-2.5-sunburst','gpt-image-2.5-flare')


def run(root, fail=False):
    specs = [SourceSpec('synthetic_'+m,'image',m,'openai','text-to-image','https://example.org/'+m,'synthetic') for m in MODELS]
    if fail:specs.append(SourceSpec('image25_failure','image',MODELS[0],'fal','model-conditions','https://example.org/failure','synthetic'))
    class Adapter:
        def parse_source(self,s,b,i):
            if s.source_key=='image25_failure':raise ValueError('synthetic broken document')
            p=dict.fromkeys(ADAPTER_PRICE_FIELDS,'');p.update(provider_model_id=s.model_id,task='text-to-image',billing_item='Image tokens / Output',amount='30',currency='USD',unit='1M tokens',price_kind='published_rate',locator='synthetic price',evidence_status='direct',condition_set_id='price')
            return [p],[condition('price','processing','Standard','synthetic Standard price')]
    return live(root,specs=specs,adapters={'image':Adapter()},fetcher=lambda s:(b'explicit synthetic public rate fixture',s.url,200))


def cli(root):
    return subprocess.run([sys.executable,'-B','-m','scripts.market_collection.image_reviews','--root',str(root)],capture_output=True,text=True)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_reports_both_models_without_mutating_inputs(tmp_path):
    p=run(tmp_path);before={f.name:digest(f) for f in p.iterdir() if f.is_file()}
    result=cli(tmp_path)
    assert result.returncode==0,result.stderr
    for model in MODELS:
        out=tmp_path/'image_reviews'/p.name/model
        m=json.loads((out/'report_manifest.json').read_text())
        assert m['run_id']==p.name and m['query']['model']==model
        assert m['query']['quality']=='high' and m['query']['num_images']==1
        assert m['status']=='complete'
        assert m['input_run_relative']=='runs/'+p.name
        for name,sha in m['output_files'].items():assert digest(out/name)==sha
        for evidence in m['source_evidence']:
            assert digest(tmp_path/evidence['raw_path_relative'])==evidence['raw_sha256']
        summary=(out/'summary.md').read_text()
        assert '현재 route: 미확인' in summary and '적용 단가: 미확인' in summary and '사용량: 미확인' in summary
        assert '공개 토큰 단가' in summary and '확인 사유' in summary and '다음 확인' in summary
        assert '실제 비용 영향: 미평가' in summary and 'unknown' in summary
        assert '0.00003 USD / 1 token' in summary
        assert (out/'comparisons.csv').is_file()
    assert before=={f.name:digest(f) for f in p.iterdir() if f.is_file()}
    repeated=cli(tmp_path)
    assert repeated.returncode!=0 and 'already exists' in repeated.stderr


def test_partial_run_retains_report_and_nonzero(tmp_path):
    p=run(tmp_path,True);r=cli(tmp_path)
    assert r.returncode!=0
    for model in MODELS:
        out=tmp_path/'image_reviews'/p.name/model
        m=json.loads((out/'report_manifest.json').read_text())
        assert m['status']=='incomplete' and 'image25_failure' in m['failed_source_keys']
        assert 'incomplete' in (out/'summary.md').read_text()
        assert json.loads((out/'comparison.json').read_text())['candidates'][0]['amount']=='30'
    assert (p/'raw').is_dir()


@pytest.mark.parametrize('case',['empty','corrupt','existing','tampered'])
def test_invalid_input_or_destination_is_nonzero(tmp_path,case):
    if case!='empty':
        p=run(tmp_path)
        if case=='corrupt':(p/'manifest.json').write_text('{}')
        if case=='existing':(tmp_path/'image_reviews'/p.name/MODELS[1]).mkdir(parents=True)
        if case=='tampered':next((p/'raw').iterdir()).write_bytes(b'changed')
    r=cli(tmp_path)
    assert r.returncode!=0
    assert not list((tmp_path/'image_reviews').glob('**/report_manifest.json'))


def workflow_script(name):
    # Run the actual step body; assertions concern artifacts/status, not YAML spelling.
    workflow=Path('.github/workflows/market-collection.yml').read_text()
    step=workflow.split('      - name: '+name+'\n',1)[1].split('      - name:',1)[0]
    return '\n'.join(line[10:] for line in step.split('        run: |\n',1)[1].splitlines())+'\n'


def workflow_summary(tmp_path):
    import os
    return subprocess.run(['bash','-c',workflow_script('Summarize collection')],cwd=tmp_path,
        env=dict(os.environ,GITHUB_STEP_SUMMARY=str(tmp_path/'summary.txt')),capture_output=True,text=True)


def test_actions_summary_requires_derived_reports_and_pack_restores_evidence(tmp_path):
    import tarfile
    root=tmp_path/'data/market_collection';p=run(root)
    assert workflow_summary(tmp_path).returncode!=0
    assert cli(root).returncode==0
    summary=workflow_summary(tmp_path)
    assert summary.returncode==0,summary.stderr
    assert 'Image reviews: 2/2' in (tmp_path/'summary.txt').read_text()
    packed=subprocess.run(['bash','-c',workflow_script('Pack collection data')],cwd=tmp_path,capture_output=True,text=True)
    assert packed.returncode==0,packed.stderr
    with tarfile.open(tmp_path/'collection-artifact/market-collection.tar.xz') as archive:
        restored=tmp_path/'restored';archive.extractall(restored,filter='data')
    for model in MODELS:
        out=restored/'market_collection/image_reviews'/p.name/model
        report=json.loads((out/'report_manifest.json').read_text())
        assert digest(restored/'market_collection'/report['source_evidence'][0]['raw_path_relative']) == report['source_evidence'][0]['raw_sha256']
    (root/'image_reviews'/p.name/MODELS[1]/'report_manifest.json').unlink()
    assert workflow_summary(tmp_path).returncode!=0


def test_actions_partial_collection_and_report_remain_failed(tmp_path):
    root=tmp_path/'data/market_collection';run(root,True)
    assert cli(root).returncode==1
    assert workflow_summary(tmp_path).returncode!=0
    summary=(tmp_path/'summary.txt').read_text()
    assert 'image25_failure' in summary and 'Incomplete image reviews: 2' in summary


def test_summary_keeps_standard_batch_and_ambiguous_raw_meter_separate(tmp_path):
    specs=[SourceSpec('prices_'+model,'image',model,'openai','text-to-image','https://example.org/'+model,'synthetic') for model in MODELS]
    class Adapter:
        def parse_source(self,s,b,i):
            prices=[];rows=[]
            for group,amount,meter,processing in [('standard','30','Image tokens / Output','Standard'),('batch','15','Image tokens / Output','Batch'),('ambiguous','9','Input Price',None)]:
                p=dict.fromkeys(ADAPTER_PRICE_FIELDS,'');p.update(provider_model_id=s.model_id,task='text-to-image',billing_item=meter,amount=amount,currency='USD',unit='1M tokens',price_kind='published_rate',locator='synthetic '+group,evidence_status='direct',condition_set_id=group)
                prices.append(p)
                rows.append(condition(group,'processing',processing or 'not documented','synthetic '+group+' processing','stated' if processing else 'unknown'))
            return prices,rows
    p=live(tmp_path,specs=specs,adapters={'image':Adapter()},fetcher=lambda s:(b'explicit synthetic Standard Batch and ambiguous price evidence',s.url,200))
    assert cli(tmp_path).returncode==0
    summary=(tmp_path/'image_reviews'/p.name/MODELS[0]/'summary.md').read_text()
    price_lines=[line for line in summary.splitlines() if line.startswith('- 원문 단가:')]
    assert len(price_lines)==3
    assert any('30 USD / 1M tokens' in line and '처리 조건: Standard' in line for line in price_lines)
    assert any('15 USD / 1M tokens' in line and '처리 조건: Batch' in line and 'not_comparable' in line for line in price_lines)
    assert any('9 USD / 1M tokens' in line and '원문 항목: Input Price' in line and '과금 항목 의미 미확인' in line and '처리 조건: 미확인' in line for line in price_lines)
