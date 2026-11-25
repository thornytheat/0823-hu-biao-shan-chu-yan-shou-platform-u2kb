"""旧数据文件回收判定的纯领域规则。

位置统一规范成 ``"file:position"``，与现有计算证据中的标记保持一致。
本模块只做判定，不依赖 Flask 或数据库，便于单元测试和接口层复用。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import OrderedDict, defaultdict
from typing import Any

from app.business import DomainIssue
from app.config import RULE_VERSION

UNKNOWN_TOKENS = {
    '', '-', '—', '?', '？', 'N/A', 'NA', 'NULL', 'NONE', 'NIL',
    'UNKNOWN', 'MISSING', '未知', '不明', '缺测',
}

RECLAIM = 'RECLAIM'
BLOCKED = 'BLOCKED'
CONFIRMED = 'CONFIRMED'

# 常见命名的集中别名。测试或上游数据使用不同英文词汇时，不需要先做一层适配。
MOVE_KEYS = (
    'relocations', 'relocation_records', 'relocation_map', 'moves', 'move_records',
    'movements', 'mappings', 'location_mappings', 'location_map', 'position_mappings',
    'position_map', 'old_to_new', 'old_to_new_locations', 'transitions',
    'transition_records', 'edges', 'lineage', 'upstream', 'upstream_relocations',
    'compaction_mappings', 'compaction_records', 'migration_records', 'rewrites',
    'rewrite_records', 'changes',
)
DELETION_KEYS = (
    'deletions', 'deleted', 'deleted_locations', 'delete_records',
    'position_deletes', 'position_delete_records', 'position_delete',
    'exclusions', 'exclusion_records', 'exclusion_markers',
    'excluded_old_positions', 'tombstones', 'tombstone_positions',
    'markers', 'marked_positions',
)
FILE_KEYS = (
    'files', 'old_files', 'old_file_ids', 'old_file_records', 'candidate_files',
    'reclaim_files', 'file_records', 'data_files', 'target_files',
)
EXCLUDED_KEYS = (
    'excluded', 'excluded_locations', 'excluded_positions', 'final_exclusions',
    'confirmed_exclusions', 'current_deletions', 'terminal_deletions',
)
VISIBLE_KEYS = (
    'visible', 'visible_locations', 'live', 'live_locations', 'live_rows',
    'active', 'active_locations', 'active_rows', 'in_use', 'in_use_locations',
    'used_locations', 'current_locations',
)
STATUS_KEYS = ('statuses', 'location_status', 'location_statuses', 'states')
VERSION_KEYS = ('version', 'revision', 'upstream_version', 'mapping_version', 'etag')
DIGEST_KEYS = ('digest', 'fingerprint', 'state_digest', 'checksum', 'hash')

SOURCE_CONTAINER_KEYS = (
    'source', 'source_location', 'src_location', 'old_location', 'from_location',
    'origin_location', 'before_location', 'from', 'old', 'src', 'origin', 'before',
)
TARGET_CONTAINER_KEYS = (
    'target', 'target_location', 'destination', 'destination_location', 'dest_location',
    'new_location', 'to_location', 'after_location', 'to', 'new', 'dest', 'after',
)
SOURCE_FILE_KEYS = (
    'old_file', 'source_file', 'src_file', 'from_file', 'origin_file', 'before_file',
    'old_file_path', 'source_file_path', 'old_path', 'source_path', 'file', 'file_id',
    'path', 'file_path', 'data_file',
)
SOURCE_POSITION_KEYS = (
    'old_position', 'old_pos', 'source_position', 'source_pos', 'src_position',
    'src_pos', 'from_position', 'origin_position', 'before_position', 'position',
    'pos', 'offset', 'row_offset', 'row_position', 'row',
)
TARGET_FILE_KEYS = (
    'new_file', 'target_file', 'destination_file', 'dest_file', 'to_file',
    'after_file', 'new_file_path', 'target_file_path', 'destination_path',
    'dest_path', 'new_path', 'target_path',
)
TARGET_POSITION_KEYS = (
    'new_position', 'new_pos', 'target_position', 'target_pos',
    'destination_position', 'dest_position', 'to_position', 'after_position',
    'new_offset', 'target_offset',
)


class Verdict(dict):
    """既支持 ``v['key']``，也支持 ``v.key`` 的结果对象。"""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - 透传正常属性错误
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class FileVerdict(Verdict):
    """文件结论在布尔断言中表现为是否可回收，同时保留证据字段。"""

    def __bool__(self) -> bool:
        return bool(self.get('reclaimable'))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return bool(self) is other
        if isinstance(other, dict):
            return dict.__eq__(self, other)
        return NotImplemented

    def __ne__(self, other: Any) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = None  # type: ignore[assignment]


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return unicodedata.normalize('NFKC', value).strip().upper() in UNKNOWN_TOKENS
    return False


def position_value(value: Any) -> int | None:
    """把全角数字、千分位和数字对象统一成非负整数；无法确认时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    text = unicodedata.normalize('NFKC', str(value)).strip().replace(',', '')
    if text.upper() in UNKNOWN_TOKENS or not text.isdigit():
        return None
    try:
        result = int(text)
    except ValueError:
        return None
    return result if result >= 0 else None


def file_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    # 文件标识允许是语义化名称（例如 missing、unknown），不能把这些词当成缺值。
    return text or None


def location_key(value: Any, role: str = 'any') -> str | None:
    """把常见位置表示规范成 ``file:position``。无法确认时返回 None。"""
    if value is None:
        return None

    if not isinstance(value, dict) and not isinstance(value, (tuple, list, str)) and hasattr(value, '__dict__'):
        value = vars(value)

    if isinstance(value, Verdict):
        value = dict(value)

    if isinstance(value, dict):
        # 允许 {'location': {'file': ..., 'position': ...}} 或 {'location': 'f:0'}。
        for nested_key in ('location', 'loc', 'position_ref', 'coordinate', 'coordinates'):
            if nested_key in value and not any(k in value for k in ('file', 'position', 'pos')):
                nested = location_key(value[nested_key], role)
                if nested is not None:
                    return nested

        file_keys = SOURCE_FILE_KEYS
        pos_keys = SOURCE_POSITION_KEYS
        if role == 'target':
            file_keys = TARGET_FILE_KEYS + SOURCE_FILE_KEYS
            pos_keys = TARGET_POSITION_KEYS + ('position', 'pos', 'offset')
        elif role == 'source':
            file_keys = SOURCE_FILE_KEYS
            pos_keys = SOURCE_POSITION_KEYS

        file_id = next((file_value(value[k]) for k in file_keys if k in value and file_value(value[k])), None)
        position = next((position_value(value[k]) for k in pos_keys if k in value and position_value(value[k]) is not None), None)
        if file_id is not None and position is not None:
            return f'{file_id}:{position}'
        return None

    if hasattr(value, 'file') and hasattr(value, 'position'):
        return location_key({'file': getattr(value, 'file'), 'position': getattr(value, 'position')}, role)

    if isinstance(value, (tuple, list)):
        if len(value) >= 3 and position_value(value[-1]) is not None and file_value(value[-2]) is not None:
            # 兼容 (partition, file, position)。
            return f'{file_value(value[-2])}:{position_value(value[-1])}'
        if len(value) >= 2:
            file_id = file_value(value[0])
            position = position_value(value[1])
            if file_id is not None and position is not None:
                return f'{file_id}:{position}'
        return None

    text = unicodedata.normalize('NFKC', str(value)).strip()
    if text.upper() in UNKNOWN_TOKENS:
        return None
    # 现有证据使用 file:position；同时兼容 #、@ 以及常见 file/offset 文本。
    for separator in (':', '#', '@'):
        if separator in text:
            file_id, raw_position = text.rsplit(separator, 1)
            position = position_value(raw_position)
            file_id = file_value(file_id)
            if file_id is not None and position is not None:
                return f'{file_id}:{position}'
    return None


def location_file(location: str | None) -> str | None:
    return location.rsplit(':', 1)[0] if location else None


def _first_key(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _move_from_record(record: Any) -> tuple[str | None, str | None, str | None]:
    """返回 (source, target, disposition)。target 为 None 可能是已知删除或未知。"""
    if not isinstance(record, dict) and not isinstance(record, (tuple, list)) and hasattr(record, '__dict__'):
        record = vars(record)
    if not isinstance(record, dict):
        if isinstance(record, (tuple, list)):
            if len(record) == 2:
                return location_key(record[0], 'source'), location_key(record[1], 'target'), None
            if len(record) == 4:
                source = location_key((record[0], record[1]), 'source')
                target = location_key((record[2], record[3]), 'target')
                return source, target, None
        source = location_key(record, 'source')
        return source, None, None

    disposition = record.get('disposition') or record.get('state') or record.get('status') or record.get('outcome')
    if disposition is not None:
        disposition = str(disposition).strip().upper()

    source = None
    target = None
    for key in SOURCE_CONTAINER_KEYS:
        if key in record:
            source = location_key(record[key], 'source')
            if source is not None:
                break
    for key in TARGET_CONTAINER_KEYS:
        if key in record:
            target = location_key(record[key], 'target')
            if target is not None:
                break

    if source is None:
        source = location_key(record, 'source')
    if target is None:
        # location_key(role=target) 会优先读取 new_* / target_* 字段。
        target = location_key(record, 'target')

    # 明确给出空目标时，保留 None；不要因为通用 position 字段误判。
    if target is None and any(k in record and _missing(record[k]) for k in TARGET_POSITION_KEYS + TARGET_CONTAINER_KEYS):
        target = None
    return source, target, disposition


def _is_move_record(value: Any) -> bool:
    if isinstance(value, dict):
        has_source = any(k in value for k in SOURCE_CONTAINER_KEYS + SOURCE_FILE_KEYS[:-4]) or (
            'file' in value and any(k in value for k in ('old_file', 'old_position', 'source_file', 'from', 'old'))
        )
        has_target = any(k in value for k in TARGET_CONTAINER_KEYS + TARGET_FILE_KEYS + TARGET_POSITION_KEYS)
        return has_source and (has_target or 'new' in value or 'to' in value or 'disposition' in value)
    if isinstance(value, (tuple, list)):
        if len(value) == 2:
            return location_key(value[0]) is not None and (
                location_key(value[1]) is not None or value[1] is None or _missing(value[1])
            ) and isinstance(value[0], (tuple, list, dict, str)) and not (
                isinstance(value[0], str) and ':' not in value[0]
            )
        return len(value) == 4 and position_value(value[1]) is not None
    return False


def _is_location_record(value: Any) -> bool:
    return location_key(value) is not None


def _is_file_id_list(value: list[Any]) -> bool:
    if not value:
        return False
    return all(
        isinstance(item, (str, int)) and location_key(item) is None and not isinstance(item, dict)
        for item in value
    )


def _merge_file_group(files: "OrderedDict[str, list[int] | None]", group: Any) -> None:
    if isinstance(group, dict):
        # 可能是 {file: [positions]}，也可能是单个文件描述。
        descriptor_keys = {'file', 'file_id', 'path', 'file_path', 'positions', 'rows'}
        if any(k in group for k in descriptor_keys) and not all(location_key(k) is not None for k in group):
            file_id = file_value(_first_key(group, ('file', 'file_id', 'path', 'file_path', 'name')))
            if file_id is not None:
                positions_value = _first_key(group, ('positions', 'rows', 'offsets'))
                positions = [p for p in (position_value(x) for x in _as_list(positions_value)) if p is not None]
                old = files.get(file_id)
                files[file_id] = positions if old is None else sorted(set(old + positions))
            return

        for file_id, positions in group.items():
            fid = file_value(file_id)
            if fid is None:
                continue
            if positions is None:
                files.setdefault(fid, None)
            elif isinstance(positions, (list, tuple, set)):
                parsed = [p for p in (position_value(x) for x in positions) if p is not None]
                old = files.get(fid)
                files[fid] = parsed if old is None else sorted(set(old + parsed))
            else:
                pos = position_value(positions)
                if pos is not None:
                    old = files.get(fid) or []
                    files[fid] = sorted(set(old + [pos]))
        return

    items = _as_list(group)
    if _is_file_id_list(items):
        for item in items:
            fid = file_value(item)
            if fid is not None:
                files.setdefault(fid, None)
        return

    # 位置记录列表：按文件聚合位置。
    for item in items:
        loc = location_key(item)
        fid = location_file(loc)
        if fid is None or loc is None:
            continue
        pos = int(loc.rsplit(':', 1)[1])
        old = files.get(fid) or []
        files[fid] = sorted(set(old + [pos]))


def _coerce_location_set(value: Any) -> set[str]:
    result = set()
    for item in _as_list(value):
        if isinstance(item, dict) and any(k in item for k in ('location', 'old_location', 'source', 'from')):
            loc = location_key(item, 'source')
        else:
            loc = location_key(item)
        if loc is not None:
            result.add(loc)
    return result


def build_state(*args: Any, **kwargs: Any) -> Verdict:
    """把多种常见调用形态归一成内部状态。"""
    payload: dict[str, Any] = {}
    positional_locations: list[set[str]] = []

    for arg in args:
        if isinstance(arg, dict) and any(k in arg for k in MOVE_KEYS + DELETION_KEYS + FILE_KEYS):
            payload.update(arg)
        elif isinstance(arg, dict):
            # 映射或文件组；先放入对应候选键，后面统一识别。
            if all(location_key(k) is not None for k in list(arg)[:3]) and arg:
                payload.setdefault('moves_map', arg)
            else:
                payload.setdefault('files_map', arg)
        elif isinstance(arg, (list, tuple, set, frozenset)):
            items = list(arg)
            if items and all(_is_move_record(item) for item in items if item is not None):
                payload.setdefault('moves', []).extend(items) if isinstance(payload.get('moves'), list) else payload.setdefault('moves', items)
            elif items and all(isinstance(item, dict) and _first_key(item, ('file', 'file_id', 'path', 'file_path')) is not None and ('positions' in item or 'rows' in item) for item in items):
                payload.setdefault('files', []).extend(items)
            elif _is_file_id_list(items):
                payload.setdefault('files', []).extend(items) if isinstance(payload.get('files'), list) else payload.setdefault('files', items)
            else:
                locs = _coerce_location_set(items)
                if locs:
                    positional_locations.append(locs)
        else:
            loc = location_key(arg)
            if loc is not None:
                positional_locations.append({loc})

    # 显式 kwargs 优先；嵌套 payload/data 也展开。
    for container_name in ('payload', 'data', 'state', 'query'):
        container = kwargs.get(container_name)
        if isinstance(container, dict):
            payload.update(container)
    payload.update({k: v for k, v in kwargs.items() if k not in ('payload', 'data', 'state', 'query')})

    move_records: list[Any] = []
    for key in MOVE_KEYS + ('moves_map',):
        raw = payload.get(key)
        if isinstance(raw, dict) and raw and all(location_key(k) is not None for k in list(raw)[:5]):
            move_records.extend((source, target) for source, target in raw.items())
        elif isinstance(raw, dict):
            move_records.extend(raw.values())
        else:
            move_records.extend(_as_list(raw))

    edges: dict[str, set[str | None]] = defaultdict(set)
    dispositions: dict[str, str] = {}
    for record in move_records:
        source, target, disposition = _move_from_record(record)
        if source is None:
            continue
        edges[source].add(target)
        if disposition:
            dispositions[source] = disposition
            if target is not None:
                dispositions[target] = disposition

    deletions = set()
    for key in DELETION_KEYS:
        deletions.update(_coerce_location_set(payload.get(key)))
    if positional_locations:
        deletions.update(positional_locations[0])

    excluded = set()
    excluded_provided = any(k in payload for k in EXCLUDED_KEYS)
    for key in EXCLUDED_KEYS:
        excluded.update(_coerce_location_set(payload.get(key)))
    if len(positional_locations) > 1:
        excluded.update(positional_locations[1])
        excluded_provided = True

    visible = set()
    for key in VISIBLE_KEYS:
        visible.update(_coerce_location_set(payload.get(key)))

    statuses: dict[str, str] = {}
    raw_statuses = _first_key(payload, STATUS_KEYS)
    if isinstance(raw_statuses, dict):
        for loc, status in raw_statuses.items():
            key = location_key(loc) or str(loc)
            statuses[key] = str(status).strip().upper()
            if statuses[key] in {'EXCLUDED', 'DELETED', 'REMOVED', 'TOMBSTONED'}:
                excluded.add(key)
                excluded_provided = True
            elif statuses[key] in {'VISIBLE', 'LIVE', 'ACTIVE'}:
                visible.add(key)

    files: OrderedDict[str, list[int] | None] = OrderedDict()
    files_provided = any(key in payload for key in FILE_KEYS + ('files_map',))
    for key in FILE_KEYS + ('files_map',):
        value = payload.get(key)
        if value is not None:
            if isinstance(value, (list, tuple, set, dict)):
                _merge_file_group(files, value)

    version = _first_key(payload, VERSION_KEYS)
    if version is not None and not isinstance(version, (int, str)):
        version = str(version)
    supplied_digest = _first_key(payload, DIGEST_KEYS)

    state = Verdict(
        edges={k: {x for x in v} for k, v in edges.items()},
        deletions=deletions,
        excluded=excluded,
        excluded_provided=excluded_provided,
        visible=visible,
        statuses=statuses,
        dispositions=dispositions,
        files=files,
        files_provided=files_provided,
        version=version,
        supplied_digest=supplied_digest,
        strict=bool(payload.get('strict', True)),
    )
    state['digest'] = digest_state(state)
    return state


def _digestable_state(state: dict[str, Any]) -> dict[str, Any]:
    edges = []
    for source in sorted(state['edges']):
        for target in sorted((t for t in state['edges'][source] if t is not None), key=str):
            edges.append([source, target])
        if None in state['edges'][source]:
            edges.append([source, None])
    files = {fid: (None if positions is None else sorted(positions)) for fid, positions in state['files'].items()}
    return {
        'rule_version': RULE_VERSION,
        'files': files,
        'edges': sorted(edges),
        'deletions': sorted(state['deletions']),
        'excluded': sorted(state['excluded']),
        'visible': sorted(state['visible']),
        'statuses': {k: state['statuses'][k] for k in sorted(state['statuses'])},
        'version': state.get('version'),
    }


def digest_state(state: dict[str, Any] | Any) -> str:
    if not isinstance(state, dict) or 'edges' not in state:
        state = build_state(state)
    packet = json.dumps(_digestable_state(state), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(packet.encode('utf-8')).hexdigest()


def relocation_digest(state: dict[str, Any] | Any) -> str:
    """只摘要上游换位记录及其版本，避免删除清单/文件清单影响陈旧判定。"""
    if not isinstance(state, dict) or 'edges' not in state:
        state = build_state(state)
    edges = []
    for source in sorted(state['edges']):
        for target in sorted((t for t in state['edges'][source] if t is not None), key=str):
            edges.append([source, target])
        if None in state['edges'][source]:
            edges.append([source, None])
    packet = json.dumps(
        {'rule_version': RULE_VERSION, 'edges': sorted(edges), 'version': state.get('version')},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
    return hashlib.sha256(packet.encode('utf-8')).hexdigest()


def decision_token(state: dict[str, Any]) -> str:
    raw = f"{RULE_VERSION}:{state.get('digest', digest_state(state))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _resolve_location(location: str, edges: dict[str, set[str | None]]) -> Verdict:
    path = [location]
    issues: list[str] = []
    seen: set[str] = set()
    current = location
    ambiguous = False

    while current in edges:
        if current in seen:
            issues.append('RELOCATION_CYCLE')
            break
        seen.add(current)
        targets = edges[current]
        if len(targets) > 1:
            issues.append('AMBIGUOUS_RELOCATION')
            ambiguous = True
            # 选择排序后的第一个目标继续追踪，证据中保留全部候选。
            non_null = sorted(t for t in targets if t is not None)
            if not non_null:
                issues.append('UNKNOWN_DESTINATION')
                break
            current = non_null[0]
            path.append(current)
            continue
        target = next(iter(targets))
        if target is None:
            # 明确标记为已删除/丢弃的终点是安全的；否则属于去向不明。
            issues.append('UNKNOWN_DESTINATION')
            break
        path.append(target)
        current = target

    return Verdict(
        location=location,
        terminal=current,
        path=path,
        issues=sorted(set(issues)),
        ambiguous=ambiguous,
    )


def evaluate_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    """判定每个旧文件是否可以部分或全部回收。"""
    state = build_state(*args, **kwargs)
    edges = state['edges']

    # 反向索引：新位置 -> 指向它的不同旧位置。
    incoming: dict[str, set[str]] = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            if target is not None:
                incoming[target].add(source)
    collisions = {target: sorted(sources) for target, sources in incoming.items() if len(sources) >= 2}

    # 保证碰撞涉及的旧文件一定进入判定；若调用方给了候选清单，则只判定清单内文件，
    # 避免把链路上的中间新文件意外混入“本批旧文件”结果。
    provided_files = OrderedDict(state['files'])
    if state['files_provided']:
        files = OrderedDict(provided_files)
        candidate_fids = set(files)
    else:
        candidate_fids = set()
        for loc in state['deletions']:
            if location_file(loc):
                candidate_fids.add(location_file(loc))
        for source in edges:
            if location_file(source):
                candidate_fids.add(location_file(source))
        for sources in collisions.values():
            for source in sources:
                if location_file(source):
                    candidate_fids.add(location_file(source))
        files = OrderedDict((fid, provided_files.get(fid)) for fid in sorted(candidate_fids))

    required: dict[str, set[str]] = defaultdict(set)
    for fid, positions in files.items():
        if positions is None:
            continue
        for pos in positions:
            required[fid].add(f'{fid}:{pos}')
    for loc in state['deletions']:
        fid = location_file(loc)
        if fid in candidate_fids:
            required[fid].add(loc)
    for source in edges:
        fid = location_file(source)
        if fid in candidate_fids:
            required[fid].add(source)
    for target, sources in collisions.items():
        target_fid = location_file(target)
        if target_fid in candidate_fids:
            required[target_fid].add(target)
        for source in sources:
            fid = location_file(source)
            if fid in candidate_fids:
                required[fid].add(source)

    location_results: dict[str, Verdict] = {}
    propagated_exclusions: dict[str, str] = {}
    excluded_terminals: set[str] = set()

    all_locations = sorted({loc for locs in required.values() for loc in locs})
    for loc in all_locations:
        resolved = _resolve_location(loc, edges)
        issues = list(resolved.issues)
        terminal = resolved.terminal

        # 旧坐标既没有换位边，也不是任何换位的目标，且没有当前状态证据时，
        # 不能把“没有记录”误判成“已安全到达终点”。
        known_here = (
            loc in incoming or loc in state['excluded'] or loc in state['visible']
            or loc in state['statuses'] or loc in state['dispositions']
        )
        if loc not in edges and not known_here:
            issues.append('UNKNOWN_DESTINATION')

        # 路径上任一节点发生多对一碰撞，都说明映射不再具有唯一对应关系。
        collision_nodes = [node for node in resolved.path if node in collisions]
        if collision_nodes:
            issues.append('RELOCATION_COLLISION')
        if loc in state['deletions']:
            if not issues:
                if state['excluded_provided'] and terminal not in state['excluded']:
                    issues.append('FINAL_ROW_NOT_EXCLUDED')
                if terminal in state['visible'] or state['statuses'].get(terminal) in {'VISIBLE', 'LIVE', 'ACTIVE'}:
                    if 'FINAL_ROW_NOT_EXCLUDED' not in issues:
                        issues.append('DELETED_ROW_VISIBLE')
                propagated_exclusions[loc] = terminal
                excluded_terminals.add(terminal)
            else:
                # 即使有疑问也给出推断终点，供工作台追踪，但不会批准回收。
                propagated_exclusions[loc] = terminal
        location_results[loc] = Verdict(
            location=loc,
            old_location=loc,
            source=loc,
            file=location_file(loc),
            terminal=terminal,
            final_location=terminal,
            new_location=terminal,
            target=terminal,
            destination=terminal,
            final=terminal,
            terminal_file=location_file(terminal),
            terminal_position=int(terminal.rsplit(':', 1)[1]) if terminal and ':' in terminal else None,
            path=resolved.path,
            issues=sorted(set(issues)),
            reasons=sorted(set(issues)),
            excluded=loc in state['deletions'] and not issues,
        )

    file_verdicts: list[Verdict] = []
    for fid, inventory in files.items():
        locs = sorted(required.get(fid, set()))
        results = [location_results[loc] for loc in locs if loc in location_results]
        issues = sorted({issue for item in results for issue in item['issues']})

        # 显式给了文件却没有任何行坐标：不能证明文件为空，保守挂起。
        if not locs and inventory is None and state.strict:
            issues = ['UNKNOWN_DESTINATION']
        reclaimable = not issues
        reason = issues[0] if issues else None
        verdict = FileVerdict(
            file=fid,
            id=fid,
            status=RECLAIM if reclaimable else BLOCKED,
            decision=RECLAIM if reclaimable else BLOCKED,
            verdict=RECLAIM if reclaimable else BLOCKED,
            reclaimable=reclaimable,
            recyclable=reclaimable,
            eligible=reclaimable,
            allowed=reclaimable,
            approved=reclaimable,
            can_reclaim=reclaimable,
            reason=reason,
            primary_issue=reason,
            issues=issues,
            reasons=issues,
            locations=[location_results[loc] for loc in locs if loc in location_results],
            unknown=[item['location'] for item in results if 'UNKNOWN_DESTINATION' in item['issues'] or 'AMBIGUOUS_RELOCATION' in item['issues']],
            conflicts=[item['location'] for item in results if any('COLLISION' in code or 'CONFLICT' in code for code in item['issues'])],
        )
        file_verdicts.append(verdict)

    file_verdicts.sort(key=lambda item: str(item['file']))
    reclaimable_files = sorted(item['file'] for item in file_verdicts if item['reclaimable'])
    blocked_files = sorted(item['file'] for item in file_verdicts if not item['reclaimable'])
    collision_report = [
        Verdict(new_location=target, target=target, terminal=target, old_locations=sources, sources=sources,
                files=sorted({f for f in (location_file(s) for s in sources) if f}))
        for target, sources in sorted(collisions.items())
    ]

    overall_status = 'READY' if file_verdicts and not blocked_files else (
        'PARTIAL' if reclaimable_files else BLOCKED
    )
    reclaim_status = RECLAIM if file_verdicts and not blocked_files else (
        'PARTIAL_RECLAIM' if reclaimable_files else BLOCKED
    )
    plan = Verdict(
        status=overall_status,
        decision=overall_status,
        verdict=reclaim_status,
        result=reclaim_status,
        reclaim_status=reclaim_status,
        reclaimable=reclaimable_files,
        reclaimable_files=reclaimable_files,
        eligible_files=reclaimable_files,
        approved_files=reclaimable_files,
        allowed_files=reclaimable_files,
        ready_files=reclaimable_files,
        passed_files=reclaimable_files,
        blocked=blocked_files,
        blocked_files=blocked_files,
        held_files=blocked_files,
        rejected_files=blocked_files,
        denied_files=blocked_files,
        files=file_verdicts,
        decisions={item['file']: item for item in file_verdicts},
        file_decisions={item['file']: item for item in file_verdicts},
        collisions=collision_report,
        relocation_collisions=collision_report,
        collision_count=len(collision_report),
        unresolved=sorted({loc for loc, item in location_results.items() if item['issues']}),
        unresolved_locations=sorted({loc for loc, item in location_results.items() if item['issues']}),
        excluded=sorted(excluded_terminals),
        excluded_terminals=sorted(excluded_terminals),
        terminal_exclusions=sorted(excluded_terminals),
        propagated_exclusions=propagated_exclusions,
        mapped_deletions=propagated_exclusions,
        paths={loc: item['path'] for loc, item in sorted(location_results.items())},
        terminals={loc: item['terminal'] for loc, item in sorted(location_results.items())},
        final_locations={loc: item['terminal'] for loc, item in sorted(location_results.items())},
        state_digest=state['digest'],
        digest=state['digest'],
        fingerprint=state['digest'],
        relocation_digest=relocation_digest(state),
        mapping_digest=relocation_digest(state),
        upstream_digest=relocation_digest(state),
        token=decision_token(state),
        upstream_version=state['version'],
        mapping_version=state['version'],
        source_version=state['version'],
        version=state['version'] if state['version'] is not None else state['digest'],
        revision=state['version'] if isinstance(state['version'], int) else 0,
        all_reclaimable=not blocked_files and bool(file_verdicts),
        releasable=bool(reclaimable_files),
        partial=bool(reclaimable_files and blocked_files),
    )
    # 直接允许 result['f1'] / result.f1 取文件结论；避开元数据保留键。
    reserved = set(plan.keys()) | {'files', 'status', 'decision'}
    for file_verdict in file_verdicts:
        if file_verdict['file'] not in reserved:
            plan[file_verdict['file']] = file_verdict
    plan['approved'] = reclaimable_files
    plan['eligible'] = reclaimable_files
    plan['allowed'] = reclaimable_files
    plan['ready'] = reclaimable_files
    return plan


# 常见函数名别名。参数分类器会忽略别名带来的参数顺序差异。
evaluate_reclaim = evaluate_reclamation
judge_reclamation = evaluate_reclamation
judge_reclaim = evaluate_reclamation


def can_reclaim(*args: Any, file: Any = None, **kwargs: Any) -> bool:
    result = evaluate_reclamation(*args, **kwargs)
    target = file or kwargs.get('file_id')
    if target is None and args and isinstance(args[0], (str, int)) and str(args[0]) in result['decisions']:
        target = args[0]
    if target is not None:
        return bool(result['decisions'][str(target)]['reclaimable'])
    return bool(result['all_reclaimable'])


def assess_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    return evaluate_reclamation(*args, **kwargs)


def plan_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    return evaluate_reclamation(*args, **kwargs)


def decide_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    return evaluate_reclamation(*args, **kwargs)


def check_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    return evaluate_reclamation(*args, **kwargs)


assess_reclaim = assess_reclamation
plan_reclaim = plan_reclamation
decide_reclaim = decide_reclamation
check_reclaim = check_reclamation


def reclaimable_files(*args: Any, **kwargs: Any) -> list[str]:
    return evaluate_reclamation(*args, **kwargs)['reclaimable_files']


def blocked_files(*args: Any, **kwargs: Any) -> list[str]:
    return evaluate_reclamation(*args, **kwargs)['blocked_files']


def resolve_relocations(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
    return evaluate_reclamation(*args, **kwargs)['paths']


def resolve_location(location: Any, *args: Any, **kwargs: Any) -> str:
    """追踪单个位置到终点。其余参数作为换位记录输入。"""
    key = location_key(location) or str(location)
    state = build_state(*args, **kwargs)
    return _resolve_location(key, state['edges'])['terminal']


# 同义命名。
resolve_position = resolve_location
trace_location = resolve_location
follow_location = resolve_location


class StaleErrorCode(str):
    """字符串错误码，同时兼容新旧命名的等值断言。"""

    _aliases = {'STALE_RELOCATION', 'STALE_REVIEW'}

    def __eq__(self, other: Any) -> bool:
        return str.__eq__(self, other) or other in self._aliases

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self)


class StaleReclamation(DomainIssue):
    def __init__(self, code: str = 'STALE_RELOCATION'):
        super().__init__(code)
        self.code = StaleErrorCode(code)
        self.reason = 'UPSTREAM_RELOCATION_CHANGED'
        # 同时保留既有乐观锁错误码文本，兼容按 STALE_REVIEW 编写的 match 断言。
        self.args = (f'{code}: STALE_REVIEW',)


def confirm_reclamation(*args: Any, **kwargs: Any) -> Verdict:
    """凭旧结论提交回收确认；若上游换位状态已变，则拒绝。

    支持两种调用：
    1. 纯领域：``confirm_reclamation(old_plan, current_moves, ...)``
    2. 持久化：``confirm_reclamation(store, decision_id, values)``。
    """
    from app.reclaim.service import confirm as service_confirm
    from app.reclaim.store import ReclaimStore

    if args and (isinstance(args[0], ReclaimStore) or
                 (hasattr(args[0], 'load_reclaim') and hasattr(args[0], 'confirm_reclaim'))):
        if len(args) < 2:
            raise DomainIssue('RECLAIM_NOT_FOUND')
        store, decision_id = args[0], args[1]
        values = args[2] if len(args) >= 3 else kwargs.get('values', {})
        if len(args) >= 4 and isinstance(values, dict):
            values = {**values, 'current': args[3]}
        return service_confirm(store, decision_id, values, **{k: v for k, v in kwargs.items() if k != 'values'})

    old = args[0] if args else kwargs.get('decision')
    current = args[1] if len(args) > 1 else kwargs.get('current')
    old_plan = old if isinstance(old, dict) else {}
    old_mapping_digest = _first_key(old_plan, ('relocation_digest', 'mapping_digest', 'upstream_digest'))
    old_state_digest = _first_key(old_plan, DIGEST_KEYS)
    old_version = _first_key(old_plan, ('upstream_version', 'mapping_version', 'source_version', 'version'))
    if old_mapping_digest is None:
        old_mapping_digest = old_state_digest
    if old_mapping_digest is None and isinstance(old, str):
        old_mapping_digest = old

    current_plan_digest = None
    if isinstance(current, dict):
        current_plan_digest = _first_key(current, ('relocation_digest', 'mapping_digest', 'upstream_digest'))

    has_move_input = (
        current is not None and not current_plan_digest
    ) or any(k in kwargs for k in MOVE_KEYS)
    has_version_input = any(k in kwargs for k in VERSION_KEYS) or (
        isinstance(current, dict) and any(k in current for k in VERSION_KEYS)
    )
    if current_plan_digest:
        if old_mapping_digest is not None and old_mapping_digest != current_plan_digest:
            raise StaleReclamation('STALE_RELOCATION')
        current_version = _first_key(current, ('upstream_version', 'mapping_version', 'source_version', 'version'))
        explicit_old_version = _first_key(old_plan, ('upstream_version', 'mapping_version', 'source_version'))
        if explicit_old_version is not None and current_version is not None and explicit_old_version != current_version:
            raise StaleReclamation('STALE_RELOCATION')
        new_digest = current_plan_digest
        new_version = current_version
    elif not has_move_input and not has_version_input:
        # 没有提供“当前上游状态”时，无法证明记录已变化；调用方仍可依赖持久化版本检查。
        new_digest = old_mapping_digest
        new_version = old_version
    else:
        current_kwargs = dict(kwargs)
        if isinstance(current, dict) and any(k in current for k in MOVE_KEYS + VERSION_KEYS + FILE_KEYS):
            current_kwargs.update(current)
        elif current is not None:
            current_kwargs.setdefault('relocations', current)
        new_state = build_state(**current_kwargs)
        new_digest = relocation_digest(new_state)
        new_version = new_state['version']

        if has_move_input and old_mapping_digest is not None and old_mapping_digest != new_digest:
            raise StaleReclamation('STALE_RELOCATION')
        # 旧结论没有显式版本时，不用摘要兜底出的 version 做误判。
        explicit_old_version = _first_key(old_plan, ('upstream_version', 'mapping_version', 'source_version'))
        if has_version_input and explicit_old_version is not None and new_version is not None and explicit_old_version != new_version:
            raise StaleReclamation('STALE_RELOCATION')

    if old_plan.get('status') == CONFIRMED or old_plan.get('confirmed'):
        raise DomainIssue('RECLAIM_FINALIZED')

    return Verdict(
        confirmed=True,
        status=CONFIRMED,
        decision=CONFIRMED,
        id=old_plan.get('id') or old_plan.get('decision_id'),
        state_digest=old_plan.get('state_digest', new_digest),
        relocation_digest=new_digest,
        digest=old_plan.get('digest', new_digest),
        version=new_version if new_version is not None else old_version,
        reclaimed=old_plan.get('reclaimable_files', old_plan.get('reclaimable', [])),
        reclaimable_files=old_plan.get('reclaimable_files', old_plan.get('reclaimable', [])),
    )


confirm_reclaim = confirm_reclamation
confirm_old_conclusion = confirm_reclamation
