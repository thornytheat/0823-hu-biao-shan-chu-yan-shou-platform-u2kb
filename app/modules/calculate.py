from collections import defaultdict

from app.business import measured
from app.reclaim.core import _resolve_location, build_state


def compute(data):
    records, files, deletes, expected = data[:4]
    relocation = data[4] if len(data) > 4 else None
    move_state = build_state(relocation) if relocation is not None else None
    move_edges = move_state['edges'] if move_state else {}
    incoming = defaultdict(set)
    for source, targets in move_edges.items():
        for target in targets:
            if target is not None:
                incoming[target].add(source)
    collision_nodes = {target for target, sources in incoming.items() if len(sources) >= 2}
    position_rules, equality_rules = {}, {}
    dangling = []
    for row in deletes:
        partition, sequence = row['partition'],row['sequence']
        if row['kind'] == 'equality_delete':
            key = (partition,row['row_key'])
            equality_rules[key] = max(sequence,equality_rules.get(key,-1))
        else:
            key = (row['file'],row['position'])
            if key[0] in files and files[key[0]][0] == partition and key in records:
                position_rules[key] = max(sequence,position_rules.get(key,-1))
                continue

            # 压实后旧位置可能已经连续换位；沿换位链把位置删除落到最终行。
            old_tag = f'{key[0]}:{key[1]}'
            resolved = _resolve_location(old_tag, move_edges) if old_tag in move_edges else None
            has_collision = bool(resolved and any(node in collision_nodes for node in resolved['path']))
            terminal_tag = resolved['terminal'] if resolved and not resolved['issues'] and not has_collision else None
            terminal_key = None
            if terminal_tag and ':' in terminal_tag:
                terminal_file, raw_position = terminal_tag.rsplit(':', 1)
                candidates = [(terminal_file, int(raw_position))]
                if terminal_file.isdigit():
                    candidates.append((int(terminal_file), int(raw_position)))
                terminal_key = next((candidate for candidate in candidates if candidate in records), None)
            if terminal_key is not None and records[terminal_key]['partition'] == partition:
                position_rules[terminal_key] = max(sequence,position_rules.get(terminal_key,-1))
            else:
                dangling.append(old_tag)
    visible = []
    removed = {}
    for key,row in sorted(records.items()):
        tag = f'{key[0]}:{key[1]}'
        if position_rules.get(key,-1) >= row['sequence']:
            removed[tag] = 'POSITION'
        elif equality_rules.get((row['partition'],row['row_key']),-1) > row['sequence']:
            removed[tag] = 'EQUALITY'
        else:
            visible.append(tag)
    return measured(len(visible),expected,'行',{'visible':visible,'removed':removed,'dangling':sorted(set(dangling))},bool(dangling))
