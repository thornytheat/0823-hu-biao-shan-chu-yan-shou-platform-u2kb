from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.modules.pipeline import run
from app.reclaim.core import evaluate_reclamation


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def execute(store, data):
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data)
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic()-start)*1000,3), bytes=len(data), run_id=run_id)
    return store.register(run_id,report)


def review(store, run_id, values):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision','status','note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED','HELD','RECLAIM','RECLAIMED') or not isinstance(note,str) or not (3<=len(note.strip())<=200):
        raise DomainIssue('REVIEW_FIELDS')
    current = store.load(run_id)
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')
    if status == 'APPROVED' and not current['report']['balance']['releasable']:
        raise DomainIssue('UNRESOLVED_ISSUES')
    if status in ('RECLAIM','RECLAIMED'):
        _validate_reclaim_confirmation(current, values)
    return store.revise(run_id,revision,status,note.strip(),now())


def _validate_reclaim_confirmation(current, values):
    """允许已核清的旧文件部分回收，但 blocked 文件不得夹带放行。"""
    requested = values.get('reclaim_files') or values.get('files') or values.get('reclaimable_files')
    if isinstance(requested, str):
        requested = [requested]
    requested = list(requested or [])
    if not requested:
        raise DomainIssue('UNRESOLVED_ISSUES')

    payload = values.get('reclaim') if isinstance(values.get('reclaim'), dict) else values
    plan = evaluate_reclamation(payload)
    allowed = set(plan['reclaimable_files'])
    if any(str(file_id) not in allowed for file_id in requested):
        raise DomainIssue('UNRESOLVED_ISSUES')

    old_reclaim = current['report'].get('reclaim') if isinstance(current['report'], dict) else None
    if isinstance(old_reclaim, dict):
        old_digest = old_reclaim.get('relocation_digest') or old_reclaim.get('digest')
        new_digest = plan.get('relocation_digest') or plan.get('digest')
        submitted_digest = values.get('relocation_digest') or values.get('digest')
        if (old_digest and submitted_digest and submitted_digest != old_digest) or \
           (old_digest and new_digest and old_digest != new_digest and
                any(key in values for key in ('relocations','moves','mappings','edges'))):
            raise DomainIssue('STALE_RELOCATION')
