from flask import Blueprint, Response, current_app, jsonify, request

from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.reclaim.core import evaluate_reclamation
from app.reclaim.service import confirm as confirm_reclaim_record
from app.reclaim.service import create_decision, update_relocations
from app.review.service import execute, review

api = Blueprint('api',__name__,url_prefix='/api')
STAGES = {'intake':1,'prepare':2,'calculate':3,'policy':4,'balance':5}


@api.post('/stages/<stage>')
def preview(stage):
    if stage == 'reclaim':
        payload = reclaim_payload()
        return jsonify(evaluate_reclamation(payload))
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    return jsonify(run(read_input(),STAGES[stage]))


@api.route('/runs',methods=['GET','POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    return jsonify(execute(store,read_input()))


@api.route('/runs/<run_id>', methods=['GET','PATCH'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.load(run_id))
    return jsonify(review(store,run_id,request.get_json(silent=True)))


def json_payload():
    if request.is_json:
        return request.get_json(silent=True) or {}
    payload = request.form.to_dict(flat=True) if request.form else {}
    if 'payload' in payload:
        import json
        try:
            parsed = json.loads(payload['payload'])
        except (TypeError, ValueError):
            raise DomainIssue('RECLAIM_FIELDS')
        payload = parsed if isinstance(parsed, dict) else {'moves': parsed}

    upload = request.files.get('file') or request.files.get('relocations')
    if upload is not None and upload.filename:
        import csv
        import io
        text = upload.read().decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text, newline='')))
        move_kinds = {'move', 'moves', 'relocation', 'relocations', 'compaction', 'rewrite', 'transition'}
        delete_kinds = {'position_delete', 'deletion', 'delete', 'exclusion', 'tombstone'}
        moves, deletions = [], []
        for row in rows:
            kind = str(row.get('kind', '')).strip().lower()
            if kind in delete_kinds:
                deletions.append(row)
            elif not kind or kind in move_kinds:
                moves.append(row)
        payload.setdefault('moves', moves)
        if deletions:
            payload.setdefault('deletions', deletions)
    return payload


# 阶段接口和 /api/reclaim* 共用同一输入适配。
reclaim_payload = json_payload


@api.post('/reclaim/evaluate')
@api.post('/reclaim/assess')
@api.post('/reclaim/plan')
@api.post('/reclaim/decide')
@api.post('/reclaims/evaluate')
@api.post('/reclaims/assess')
@api.post('/reclaims/plan')
def reclaim_evaluate():
    payload = json_payload()
    return jsonify(evaluate_reclamation(payload) if isinstance(payload, dict) else evaluate_reclamation(payload))


@api.route('/reclaim', methods=['GET', 'POST'])
@api.route('/reclaims', methods=['GET', 'POST'])
def reclaims():
    store = current_app.extensions['reclaims']
    if request.method == 'GET':
        return jsonify(items=store.list_reclaims())
    payload = json_payload()
    return jsonify(create_decision(store, payload))


@api.get('/reclaim/<decision_id>')
@api.get('/reclaims/<decision_id>')
def reclaim_detail(decision_id):
    return jsonify(current_app.extensions['reclaims'].load_reclaim(decision_id))


@api.patch('/reclaim/<decision_id>')
@api.patch('/reclaims/<decision_id>')
@api.post('/reclaim/<decision_id>/confirm')
@api.post('/reclaims/<decision_id>/confirm')
def reclaim_confirm(decision_id):
    store = current_app.extensions['reclaims']
    values = request.get_json(silent=True) if request.is_json else json_payload()
    return jsonify(confirm_reclaim_record(store, decision_id, values))


@api.post('/reclaims/confirm')
@api.post('/reclaim/confirm')
def reclaim_confirm_by_body():
    store = current_app.extensions['reclaims']
    values = json_payload()
    decision_id = values.get('id') or values.get('decision_id')
    if not decision_id:
        raise DomainIssue('RECLAIM_NOT_FOUND')
    return jsonify(confirm_reclaim_record(store, decision_id, values))


@api.route('/relocations', methods=['GET', 'PUT', 'POST'])
def relocations():
    store = current_app.extensions['reclaims']
    if request.method == 'GET':
        current = store.current_relocations()
        return jsonify(current or {'payload': None, 'digest': None, 'version': None})
    return jsonify(update_relocations(store, json_payload()))


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report),mimetype='application/json', headers={
        'Content-Disposition':f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control':'no-store',
    })
