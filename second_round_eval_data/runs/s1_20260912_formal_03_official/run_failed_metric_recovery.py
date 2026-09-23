import asyncio, hashlib, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.rag.preflight import build_source_snapshot, inspect_scoring_models
from evaluation.rag.scoring import incomplete_score_metrics, read_jsonl, recover_incomplete_score_metrics, scores_are_complete
from evaluation.rag.report import write_report
from public_kb.config import Settings
root=Path('.').resolve(); data=root/'second_round_eval_data'; run=data/'runs'/'s1_20260912_formal_03_official'
manifest_path=run/'manifest.json'; results_path=run/'results.jsonl'; scores_path=run/'scores.jsonl'; usage_path=run/'scoring_usage.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
if manifest.get('stage')!='s1' or manifest.get('run_id')!=run.name: raise SystemExit('run identity mismatch')
if manifest.get('status')!='scoring_incomplete': raise SystemExit('run is not scoring_incomplete')
results=read_jsonl(results_path); scores=read_jsonl(scores_path); failures=incomplete_score_metrics(scores)
if len(results)!=200 or len(scores)!=200 or len(failures)!=4: raise SystemExit(f'unexpected counts results={len(results)} scores={len(scores)} failures={len(failures)}')
if any(x.get('status')!='success' for x in results): raise SystemExit('collection contains non-success row')
src=json.loads((data/'s1_official_source_snapshot.json').read_text(encoding='utf-8'))
if src!=build_source_snapshot(root,stage='s1'): raise SystemExit('source changed before recovery')
if not inspect_scoring_models(Settings()).get('passed'): raise SystemExit('scoring model gate failed')
recovery_id=datetime.now(timezone.utc).strftime('recovery_%Y%m%dT%H%M%SZ')
recovery_dir=run/'scoring_recovery_attempts'/recovery_id
recovery_dir.mkdir(parents=True,exist_ok=False)
for name in ['manifest.json','scores.jsonl','scoring_usage.json','summary.json','report.md']:
 p=run/name
 if p.exists(): shutil.copy2(p,recovery_dir/name)
event={'recovery_id':recovery_id,'started_at':datetime.now(timezone.utc).isoformat(),'method':'failed_metrics_only','original_failed_metric_count':len(failures),'original_failures':failures,'metric_timeout_s':600.0,'concurrency':1,'inter_metric_delay_s':2.0,'backup_directory':str(recovery_dir),'audit_path':str(recovery_dir/'attempts.jsonl')}
manifest['status']='scoring_recovery'; manifest.setdefault('scoring_recovery_events',[]).append(event)
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
try:
 recovered,recovery=asyncio.run(recover_incomplete_score_metrics(results_path,scores_path,usage_path,recovery_dir/'attempts.jsonl',metric_timeout_s=600.0,inter_metric_delay_s=2.0,show_progress=True))
except BaseException:
 event['status']='interrupted'; event['interrupted_at']=datetime.now(timezone.utc).isoformat(); manifest['status']='scoring_recovery_interrupted'; manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); raise
complete=scores_are_complete(recovered,200)
event.update(recovery); event['status']='complete' if complete else 'incomplete'; event['completed_at']=datetime.now(timezone.utc).isoformat()
manifest['status']='scoring_complete' if complete else 'scoring_incomplete'; manifest['completed_scores']=len(recovered); manifest['scoring_completed_at']=event['completed_at']; manifest['final_metric_timeout_s']=600.0
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
write_report(run)
print(json.dumps({'status':manifest['status'],**recovery,'recovery_directory':str(recovery_dir)},ensure_ascii=False,indent=2))
raise SystemExit(0 if complete else 2)
