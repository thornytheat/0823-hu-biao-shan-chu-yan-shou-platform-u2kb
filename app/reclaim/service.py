from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.business import DomainIssue
from app.reclaim.core import (
    DIGEST_KEYS,
    StaleReclamation,
    build_state,
    evaluate_reclamation,
    relocation_digest,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def assess(*args: Any, **kwargs: Any):
    return evaluate_reclamation(*args, **kwargs)


def create_decision(store, *args: Any, **kwargs: Any):
    plan = evaluate_reclamation(*args, **kwargs)
    decision_id = kwargs.pop('decision_id', None) or plan['token']
    return store.register_reclaim(decision_id, plan, now())


# 语义别名。
plan_reclamation = create_decision
register = create_decision


def _current_digest(store, values: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    # 调用方在确认请求中携带了最新换位记录：以这些记录为准计算摘要。
    candidate = dict(kwargs)
    if isinstance(values, dict):
        candidate.update(values)
    if any(key in candidate for key in ('relocations', 'moves', 'mappings', 'edges', 'upstream')):
        return relocation_digest(build_state(**candidate))

    # 服务端保存了上游换位记录当前版本：直接使用当前摘要。
    if store is not None and hasattr(store, 'current_relocations'):
        current = store.current_relocations()
        if current is not None:
            return current['digest']

    # 调用方显式提交了“当前上游摘要”时才用于新鲜度比较；旧结论自带的 digest
    # 属于 submitted_digests，不能在这里被当成新状态。
    if isinstance(values, dict):
        explicit = values.get('current_relocation_digest') or values.get('current_mapping_digest')
        if explicit:
            return explicit
    return kwargs.get('current_relocation_digest') or kwargs.get('current_mapping_digest')


def confirm(store, decision_id: str, values: dict[str, Any] | None = None, **kwargs: Any):
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise DomainIssue('RECLAIM_FIELDS')

    note = values.get('note', kwargs.get('note', '')) or ''
    if not isinstance(note, str) or len(note) > 200:
        raise DomainIssue('RECLAIM_FIELDS')
    note = note.strip()

    revision = values.get('revision', kwargs.get('revision'))
    if revision is not None and (type(revision) is not int or revision < 0):
        raise DomainIssue('RECLAIM_FIELDS')

    record = store.load_reclaim(decision_id)
    if record['status'] == 'CONFIRMED':
        raise DomainIssue('RECLAIM_FINALIZED')
    if revision is not None and revision != record['revision']:
        raise DomainIssue('STALE_REVIEW')

    current_digest = _current_digest(store, values, kwargs)
    expected_digest = record['digest']
    expected_mapping_digest = record['plan'].get('relocation_digest')

    # 旧结论可能以 token/fingerprint/state_digest/mapping_digest 形式随请求提交。
    submitted_digests = []
    for source in (values, kwargs):
        if isinstance(source, dict):
            submitted_digests.extend(source.get(key) for key in DIGEST_KEYS if source.get(key))
            if source.get('token'):
                submitted_digests.append(source['token'])
            if source.get('relocation_digest'):
                submitted_digests.append(source['relocation_digest'])
            if source.get('mapping_digest'):
                submitted_digests.append(source['mapping_digest'])
    accepted_digests = {
        expected_digest,
        expected_mapping_digest,
        record['plan'].get('token'),
        record['plan'].get('digest'),
        record['plan'].get('fingerprint'),
    }
    submitted_version = values.get('version', kwargs.get('version')) if isinstance(values, dict) else kwargs.get('version')
    record_version = record['plan'].get('upstream_version')
    if submitted_version is not None and record_version is not None and str(submitted_version) != str(record_version):
        raise StaleReclamation('STALE_RELOCATION')
    if submitted_digests and not any(digest in accepted_digests for digest in submitted_digests):
        raise StaleReclamation('STALE_RELOCATION')
    if current_digest is not None and expected_mapping_digest and current_digest != expected_mapping_digest:
        raise StaleReclamation('STALE_RELOCATION')

    expected_revision = revision if revision is not None else record['revision']
    saved = store.confirm_reclaim(decision_id, expected_digest, note, now(), expected_revision)
    if not saved:
        latest = store.load_reclaim(decision_id)
        if revision is not None and revision != latest['revision']:
            raise DomainIssue('STALE_REVIEW')
        if latest['status'] == 'CONFIRMED':
            raise DomainIssue('RECLAIM_FINALIZED')
        raise DomainIssue('STALE_REVIEW')
    return saved


def update_relocations(store, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise DomainIssue('RECLAIM_FIELDS')
    state = build_state(payload)
    digest = relocation_digest(state)
    return store.save_relocations({**payload, 'digest': digest}, now())


class ReclaimService:
    """面向对象调用习惯的持久化服务封装。"""

    def __init__(self, store):
        self.store = store

    def assess(self, *args, **kwargs):
        return assess(*args, **kwargs)

    def create_decision(self, *args, **kwargs):
        return create_decision(self.store, *args, **kwargs)

    create = register = plan = create_decision

    def confirm(self, decision_id, values=None, **kwargs):
        return confirm(self.store, decision_id, values, **kwargs)

    def update_relocations(self, payload):
        return update_relocations(self.store, payload)


# 同义服务名。
ReclamationService = ReclaimService
RecyclingService = ReclaimService


# 对齐 app.review.service 中 execute/review 的命名习惯。
execute = create_decision
review = confirm
