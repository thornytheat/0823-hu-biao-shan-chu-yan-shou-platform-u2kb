from app.business import DomainIssue, by_kind, prepared, single


def prepare(rows):
    target = single(rows,'snapshot')
    at = target['sequence']
    records = {}
    files = {}
    for row in by_kind(rows,'data'):
        if row['sequence'] > at:
            continue
        file_id, position = row['file'],row['position']
        descriptor = (row['partition'],row['sequence'])
        if files.setdefault(file_id,descriptor) != descriptor or (file_id,position) in records:
            raise DomainIssue('DATA_POSITION_CONFLICT')
        records[file_id,position] = row
    deletes = [r for r in rows if r['kind'].endswith('_delete') and r['sequence'] <= at]
    return prepared(len(records),{'data_files':len(files),'delete_rows':len(deletes),'as_of_sequence':at},(records, files, deletes, target['position']))
