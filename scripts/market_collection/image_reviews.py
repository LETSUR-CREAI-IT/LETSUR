"""Write deterministic Korean GPT Image 2.5 reviews outside validated input runs."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from .comparison import compare_run, write_comparison, DEFAULT_REQUEST
from .core import CollectionError, _validated_run
from .models import SUNBURST_MODEL, FLARE_MODEL

MODELS = (SUNBURST_MODEL, FLARE_MODEL)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(result, status, failed):
    q=result['request']
    lines=[f"# {q['model']} 공개 조건 검토",'',
           f"회차: {result['run_id']} / 보고서 상태: {status}",
           f"요청: {q['task']} / quality={q['quality']} / size={q['size']} / n={q['num_images']} / {q['processing']} / {q['api_surface']} / {q['billing_item']}",'',
           '## 렛서 적용 정보','',
           '- 현재 route: 미확인', '- 적용 단가: 미확인', '- 사용량: 미확인',
           '- 실제 요청 비용: unknown / 계정 자격: unknown / 실제 비용 영향: 미평가','',
           '공개 토큰 단가가 같아도 실제 비용은 사용 토큰·적용 단가·계정 조건 확인이 필요합니다. 이 회차만으로 가격 변화·과거 추이·추천 순위를 생성하지 않습니다.','',
           '## 공개 토큰 단가 및 확인 사유','']
    if failed:
        lines += ['수집 실패 source: '+', '.join(failed)+'. 성공한 근거만 보존한 incomplete 보고서입니다.','']
    if not result['candidates']:
        lines += ['해당 요청에 연결할 공개 단가가 없습니다. 단가/실제 비용은 unknown입니다.','']
    routes={}
    for c in result['candidates']:
        routes.setdefault(c['route'],[]).append(c)
    for route, candidates in sorted(routes.items()):
        reasons={r for c in candidates for r in c['reason_codes']}
        notes=[]
        for token,label in (('size','정확한 크기 지원 확인 필요'), ('processing','처리 방식별 요금/지원 미확인'),
                            ('quality','품질 조건 확인 필요'), ('num_images','장수 조건 확인 필요'),
                            ('api_profile_missing','채널 API 계약 확인 필요'), ('failed_api_document','현재 API 문서 수집/파싱 실패'),
                            ('model_id','모델 범위 확인 필요'), ('billing_item','청구 항목 확인 필요'),
                            ('price_claim','단가 주장 확인 필요')):
            if any(token in reason for reason in reasons):notes.append(label)
        if any(r.startswith(('unsupported:','mismatch:')) for r in reasons):notes.append('선택 요청과 문서 조건 불일치')
        if reasons and not notes:notes.append('일부 공개 조건 확인 필요')
        lines += [f"### {route}",'', '공개 토큰 단가 (원문 항목·처리 조건별):']
        for c in candidates:
            processing_rows=[r for r in c['conditions'] if r['source_id']==c['source_id']
                             and r['condition_set_id']==c['condition_set_id'] and r['condition_key']=='processing']
            values={r['value'] for r in processing_rows if r['status']=='stated'}
            processing=next(iter(values)) if len(values)==1 and all(r['status']=='stated' for r in processing_rows) else '미확인'
            semantics='과금 항목 의미 미확인' if c['canonical_meter'] is None else '과금 항목: '+c['canonical_meter']
            normalized=(f" / 정규화: {c['normalized_amount']} {c['currency']} / {c['normalized_unit']}"
                        if c['canonical_meter'] is not None and c['normalized_amount'] is not None else '')
            lines.append(f"- 원문 단가: {c['amount'] or 'unknown'} {c['currency'] or 'unknown'} / {c['unit'] or 'unknown'} / 원문 항목: {c['raw_meter'] or 'unknown'} / {semantics} / 처리 조건: {processing} / 판정: {c['overall_status']} / 관측: {c['observation_id']}"+normalized)
        lines += ['',
                  '처리 요청: '+q['processing']+' / 관측 '+str(len(candidates))+'개 / 판정: '+', '.join(sorted({c['overall_status'] for c in candidates})),
                  '확인 사유: '+(' · '.join(notes) if notes else '선택 요청의 공개 문서 조건 확인'),
                  '계정 배포 자격: 미확인 / snapshot: '+', '.join(sorted({str(c['snapshot_identity'] or '미확인') for c in candidates}))+' / 실제 요청 비용: unknown / 실제 비용 영향: 미평가',
                  '다음 확인: 현재 route·적용 단가·사용량 및 위 보류 조건을 해당 채널에서 확인합니다.','']
    lines += ['## 문서 scope와 원본 근거','',
              'manufacturer_supplement는 exact-model 장수만 보충합니다. channel_model은 채널 계약입니다. channel_ui는 UI 선택, generic_api_reference는 일반 가이드, reference는 공급자 주장/참고이며 API 지원이나 계정 배포 자격을 확정하지 않습니다.','']
    for p in result['documentary_profiles']:
        e=p['source_evidence']
        lines += [f"- {p['source_key']} / scope={p['scope']} / 수집·파싱={p['fetch_status']}/{p['parse_status']} / {e['URL']}"]
    lines += ['', '전체 판정 코드·조건·locator·source_id·원본 hash는 comparison.json, 비교 행은 comparisons.csv, 회차 내 원본 위치와 파일 hash는 report_manifest.json에서 확인할 수 있습니다.','']
    lines += ['## 다음 확인','',
              '렛서의 현재 route·적용 단가·사용량을 확보한 뒤 선택 요청의 processing, exact pixel capability, 계정·지역 배포, snapshot, 실제 토큰 사용 및 청구 관계를 확인합니다. 참고 문서 실패는 현재 회차 실패로 남고, 이전 raw로 대체하지 않습니다.','']
    return '\n'.join(lines)


def generate(root):
    root=Path(root)
    runs=root/'runs'
    directories=sorted(p for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []
    if not directories:
        raise CollectionError('no collection runs found')
    # Validate all inputs/destinations first, so corrupt runs or existing reports cannot look successful.
    prepared=[]
    for run in directories:
        manifest,sources,_,_=_validated_run(run)
        for model in MODELS:
            output=root/'image_reviews'/manifest['run_id']/model
            if output.exists():
                raise CollectionError('image review destination already exists: '+str(output))
        prepared.append((run,manifest,sources))
    complete=True
    for run,manifest,sources in prepared:
        failed=sorted(s['source_key'] for s in sources if s['fetch_status']!='ok' or s['parse_status']!='ok')
        for model in MODELS:
            result=compare_run(run,dict(DEFAULT_REQUEST,model=model))
            status='incomplete' if failed or not result['candidates'] else 'complete'
            if status=='incomplete':complete=False
            output=write_comparison(result,root/'image_reviews'/manifest['run_id']/model)
            (output/'summary.md').write_text(_summary(result,status,failed),encoding='utf-8')
            source_evidence=[dict(source_id=s['source_id'],source_key=s['source_key'],model_id=s['model_id'],
                URL=s['effective_url'] or s['requested_url'],fetched_at=s['fetched_at_utc'],
                fetch_status=s['fetch_status'],parse_status=s['parse_status'],error=s['error'],raw_sha256=s['raw_sha256'],
                raw_path_relative='runs/'+manifest['run_id']+'/'+s['raw_path'] if s['raw_path'] else '') for s in sources]
            report=dict(schema_version=1,run_id=manifest['run_id'],query=result['request'],status=status,
                failed_source_keys=failed,no_price_candidates=not bool(result['candidates']),
                input_run_relative='runs/'+manifest['run_id'],input_mode=manifest['mode'],
                source_evidence=source_evidence,
                input_files={name:_hash(run/name) for name in ('manifest.json','sources.csv','price_observations.csv','conditions.csv')},
                output_files={name:_hash(output/name) for name in ('comparison.json','comparisons.csv','summary.md')})
            (output/'report_manifest.json').write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
            print(f"{status}: {output}")
    return 0 if complete else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        return generate(args.root)
    except (CollectionError,OSError,ValueError,KeyError,TypeError) as exc:
        print(f'image reviews refused: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
