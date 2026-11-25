from app.business import measured


def compute(data):
    records, files, deletes, expected = data
    position_rules, equality_rules = {}, {}
    dangling = []
    for row in deletes:
        partition, sequence = row['partition'],row['sequence']
        if row['kind'] == 'equality_delete':
            key = (partition,row['row_key'])
            equality_rules[key] = max(sequence,equality_rules.get(key,-1))
        else:
            key = (row['file'],row['position'])
            if key[0] not in files or files[key[0]][0] != partition or key not in records:
                dangling.append(f'{key[0]}:{key[1]}')
                continue
            position_rules[key] = max(sequence,position_rules.get(key,-1))
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
