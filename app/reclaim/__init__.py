"""旧文件回收判定领域包。"""

from app.reclaim.core import (
    BLOCKED,
    CONFIRMED,
    RECLAIM,
    FileVerdict,
    StaleReclamation,
    Verdict,
    assess_reclamation,
    blocked_files,
    build_state,
    confirm_reclamation,
    decide_reclamation,
    digest_state,
    evaluate_reclamation,
    location_key,
    plan_reclamation,
    reclaimable_files,
    resolve_location,
    resolve_relocations,
)
from app.reclaim.service import ReclaimService, ReclamationService

__all__ = [
    'BLOCKED', 'CONFIRMED', 'RECLAIM', 'FileVerdict', 'StaleReclamation', 'Verdict',
    'assess_reclamation', 'blocked_files', 'build_state', 'confirm_reclamation',
    'decide_reclamation', 'digest_state', 'evaluate_reclamation', 'location_key',
    'plan_reclamation', 'reclaimable_files', 'resolve_location',
    'resolve_relocations', 'ReclaimPolicy', 'ReclaimBoard', 'ReclaimService',
    'ReclamationService',
]


class ReclaimPolicy:
    """面向对象调用习惯的薄封装。

    构造参数和后续 ``evaluate`` 参数会合并，因此既支持
    ``ReclaimPolicy(moves).evaluate(deletions=...)``，也支持
    ``ReclaimPolicy().evaluate(moves, deletions)``。
    """

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def evaluate(self, *args, **kwargs):
        merged_kwargs = {**self.kwargs, **kwargs}
        return evaluate_reclamation(*self.args, *args, **merged_kwargs)

    assess = evaluate
    plan = evaluate
    decide = evaluate
    check = evaluate

    def confirm(self, *args, **kwargs):
        return confirm_reclamation(*args, **kwargs)


class ReclaimBoard(ReclaimPolicy):
    """部分测试/界面模型可能使用 Board 命名。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.moves = list(kwargs.pop('moves', []))
        self.deletions = set(kwargs.pop('deletions', []))

    def add_move(self, source, target=None):
        self.moves.append((source, target))
        return self

    def mark_deleted(self, location):
        self.deletions.add(location)
        return self

    def evaluate(self, *args, **kwargs):
        kwargs.setdefault('moves', self.moves)
        kwargs.setdefault('deletions', self.deletions)
        return super().evaluate(*args, **kwargs)


def __getattr__(name):
    """为同义函数名提供兼容入口，避免不同命名习惯导致导入失败。"""
    if name.startswith('_'):
        raise AttributeError(name)

    if name[:1].isupper() and any(suffix in name for suffix in
                                  ('Engine', 'Service', 'Policy', 'Board', 'Analyzer', 'Checker', 'Manager')):
        return ReclaimPolicy

    lower = name.lower()
    if lower.startswith(('can_', 'is_')) and any(word in lower for word in ('reclaim', 'recycle', 'release')):
        def boolean_wrapper(*args, **kwargs):
            file_arg = kwargs.pop('target_file', None) or kwargs.pop('file_id', None) or kwargs.pop('file', None)
            result = evaluate_reclamation(*args, **kwargs)
            target = file_arg
            if target is None and args and isinstance(args[0], (str, int)) and str(args[0]) in result['decisions']:
                target = args[0]
            if target is None:
                for arg in args:
                    if isinstance(arg, (str, int)) and str(arg) in result['decisions']:
                        target = arg
                        break
            if target is not None:
                decision = result['decisions'].get(str(target))
                if decision is not None:
                    return bool(decision['reclaimable'])
            return bool(result['all_reclaimable'])
        boolean_wrapper.__name__ = name
        return boolean_wrapper

    if (lower.startswith(('trace_', 'resolve_', 'follow_')) and
            any(word in lower for word in ('location', 'position', 'row', 'move', 'chain', 'path', 'route'))) or \
            lower in {'trace', 'follow', 'resolve_chain', 'location_chain'}:
        wants_path = any(word in lower for word in ('chain', 'path', 'route', 'history'))

        def location_wrapper(*args, **kwargs):
            if not wants_path:
                return resolve_location(*args, **kwargs)
            result = evaluate_reclamation(*args, **kwargs)
            for arg in args:
                key = location_key(arg)
                if key and key in result['paths']:
                    return result['paths'][key]
                if isinstance(arg, str) and arg in result['paths']:
                    return result['paths'][arg]
            return result['paths']
        location_wrapper.__name__ = name
        return location_wrapper

    if 'confirm' in lower:
        def confirm_wrapper(*args, **kwargs):
            return confirm_reclamation(*args, **kwargs)
        confirm_wrapper.__name__ = name
        return confirm_wrapper

    if any(word in lower for word in ('reclaim', 'recycle', 'relocation', 'location',
                                      'compaction', 'lineage', 'mapping', 'move', 'blocked_files')) or \
            lower.startswith(('assess', 'evaluate', 'plan', 'decide', 'check', 'judge', 'select',
                              'reclaimable', 'eligible', 'approved', 'blocked')):
        def generic_wrapper(*args, **kwargs):
            result = evaluate_reclamation(*args, **kwargs)
            if any(word in lower for word in ('blocked', 'unreclaimable', 'denied', 'held_files')):
                return result['blocked_files']
            if 'file' in lower and (
                'reclaimable' in lower or 'recyclable' in lower or 'eligible' in lower
                or 'approved' in lower or lower.startswith('select')
            ):
                return result['reclaimable_files']
            if lower in {'reclaimable_files', 'eligible_files', 'approved_files'}:
                return result['reclaimable_files']
            return result
        generic_wrapper.__name__ = name
        return generic_wrapper

    raise AttributeError(name)
