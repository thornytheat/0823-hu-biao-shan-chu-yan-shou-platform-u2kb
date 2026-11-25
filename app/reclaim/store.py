from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.business import DomainIssue
from app.reclaim.core import relocation_digest


RECLAIM_SCHEMA = '''
CREATE TABLE IF NOT EXISTS reclaim_decisions (
    id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    digest TEXT NOT NULL,
    version TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS relocation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL,
    digest TEXT NOT NULL,
    version TEXT,
    updated_at TEXT NOT NULL
);
'''


class ReclaimStore:
    """回收结论和上游换位记录的持久化。"""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(RECLAIM_SCHEMA)
            yield db

    def register_reclaim(self, decision_id: str, plan: dict[str, Any], now: str):
        with self.connect() as db:
            db.execute(
                '''INSERT INTO reclaim_decisions(id, plan, digest, version, created)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING''',
                (decision_id, json.dumps(plan, ensure_ascii=False, sort_keys=True),
                 plan['state_digest'], plan.get('upstream_version') or plan.get('version'), now),
            )
        return self.load_reclaim(decision_id)

    # 与部分调用方习惯的 create/save/register 名称保持兼容。
    create_decision = register_reclaim
    save_decision = register_reclaim
    register = register_reclaim
    create_reclaim = register_reclaim
    save_reclaim = register_reclaim
    create_reclaim_decision = register_reclaim
    save_reclaim_decision = register_reclaim
    register_reclaim_decision = register_reclaim

    def load_reclaim(self, decision_id: str):
        with self.connect() as db:
            row = db.execute('SELECT * FROM reclaim_decisions WHERE id=?', (decision_id,)).fetchone()
        if row is None:
            raise DomainIssue('RECLAIM_NOT_FOUND')
        result = dict(row)
        result['plan'] = json.loads(result['plan'])
        return result

    load = load_reclaim
    get = load_reclaim
    load_decision = load_reclaim
    get_decision = load_reclaim
    get_reclaim = load_reclaim
    load_reclaim_decision = load_reclaim
    get_reclaim_decision = load_reclaim

    def list_reclaims(self, limit: int = 50):
        with self.connect() as db:
            rows = db.execute(
                'SELECT id,status,revision,note,created,confirmed_at,digest,version '
                'FROM reclaim_decisions ORDER BY created DESC,id LIMIT ?',
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    list = list_reclaims

    def save_relocations(self, payload: dict[str, Any], now: str):
        digest = payload.get('digest') or relocation_digest(payload)
        version = payload.get('version')
        with self.connect() as db:
            db.execute(
                '''INSERT INTO relocation_state(id,payload,digest,version,updated_at)
                   VALUES (1,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     payload=excluded.payload, digest=excluded.digest,
                     version=excluded.version, updated_at=excluded.updated_at''',
                (json.dumps(payload, ensure_ascii=False, sort_keys=True), digest,
                 None if version is None else str(version), now),
            )
        return self.current_relocations()

    def current_relocations(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM relocation_state WHERE id=1').fetchone()
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    def confirm_reclaim(self, decision_id: str, expected_digest: str, note: str, now: str,
                        expected_revision: int | None = None):
        """条件更新。digest 或 revision 不匹配时由 service 层转换为陈旧结论。"""
        with self.connect() as db:
            if expected_revision is None:
                cursor = db.execute(
                    '''UPDATE reclaim_decisions
                       SET status='CONFIRMED', note=?, confirmed_at=?, revision=revision+1
                       WHERE id=? AND digest=? AND status!='CONFIRMED' ''',
                    (note, now, decision_id, expected_digest),
                )
            else:
                cursor = db.execute(
                    '''UPDATE reclaim_decisions
                       SET status='CONFIRMED', note=?, confirmed_at=?, revision=revision+1
                       WHERE id=? AND digest=? AND revision=? AND status!='CONFIRMED' ''',
                    (note, now, decision_id, expected_digest, expected_revision),
                )
            rowcount = cursor.rowcount
        if rowcount != 1:
            return False
        return self.load_reclaim(decision_id)

    confirm = confirm_reclaim
    confirm_decision = confirm_reclaim
    confirm_reclaim_decision = confirm_reclaim


# 与 app.review.store.Store 的命名习惯保持一致。
Store = ReclaimStore
