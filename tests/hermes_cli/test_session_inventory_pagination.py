"""The inventory transport must not inherit the sidebar's unbounded pin backfill."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_state import SessionDB
from hermes_cli.web_routers import sessions


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    db = SessionDB(path)
    expected = {}
    for index in range(105):
        sid = f'archive-{index:03}'
        db.create_session(sid, 'gui')
        db.append_message(sid, 'user', 'fixture')
        db.set_session_archived(sid, True)
        expected[sid] = True
    for sid, source, messages, archived, hidden in [
        ('pin', 'gui', 1, False, False),
        ('old-pin', 'gui', 1, True, False),
        ('cron-pin', 'cron', 1, False, False),
        ('empty-pin', 'gui', 0, False, False),
        ('hidden', 'gui', 1, False, True),
    ]:
        db.create_session(sid, source)
        if messages:
            db.append_message(sid, 'user', 'fixture')
        db.set_session_pinned(sid, True)
        db.set_session_archived(sid, archived)
        if hidden:
            db._conn.execute('UPDATE sessions SET hidden = 1 WHERE id = ?', (sid,))
            db._conn.commit()
        if source == 'gui' and messages and not hidden:
            expected[sid] = archived
    db.close()
    monkeypatch.setattr(sessions, '_maybe_auto_archive_for_profile', lambda p: None)
    monkeypatch.setattr(sessions, '_open_session_db_for_profile', lambda p, **kw: SessionDB(path, **kw))
    monkeypatch.setattr(sessions, '_cron_default_profile', lambda: 'default')
    app = FastAPI()
    app.include_router(sessions.list_router)
    with TestClient(app) as client:
        yield client, expected


@pytest.mark.parametrize('mode', ['only', 'include', 'exclude'])
@pytest.mark.parametrize('limit', [1, 100])
def test_inventory_pages_obey_filters_and_count(inventory, mode, limit):
    client, all_rows = inventory
    expected = {sid for sid, archived in all_rows.items()
                if mode == 'include' or archived == (mode == 'only')}
    seen = []
    offset = 0
    for _ in range(110):
        response = client.get('/api/sessions', params={
            'archived': mode, 'limit': limit, 'offset': offset,
            'order': 'recent', 'min_messages': 1, 'exclude_sources': 'cron,subagent,kanban',
            'include_pinned': 'false',
        })
        assert response.status_code == 200
        page = response.json()
        ids = [row['id'] for row in page['sessions']]
        assert len(ids) <= limit
        assert set(ids) <= expected
        assert not (set(ids) & set(seen))
        assert page['total'] == len(expected)
        seen.extend(ids)
        offset += len(ids)
        if offset >= page['total']:
            break
        assert ids, 'nonterminal empty page'
    assert set(seen) == expected
    assert len(seen) == len(expected)


def test_sidebar_default_keeps_legacy_pin_backfill(inventory):
    client, _ = inventory
    page = client.get('/api/sessions', params={'archived': 'exclude', 'limit': 1}).json()
    assert {'pin', 'old-pin'} <= {row['id'] for row in page['sessions']}
