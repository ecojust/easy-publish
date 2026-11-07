"""Per-account video preferences and frozen queue options; no real platform I/O."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from easy_publish.app import create_app
from easy_publish.store import Store, dump
from test_bilibili_integration import BilibiliRuntime, CATEGORIES, bili_api, preflight, video_content
from test_core import TOKEN, completed_batch, ready_account, request_batch, wait_state
from test_mcp_server import INSTANCE, MCP_TOKEN, call, result_value


def preferences(client, account, options, **changes):
    response = client.put(f"/api/accounts/{account['id']}/preferences", json={"videoOptions": options, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(app, batch_id):
    return json.loads(app.state.service.store.one("SELECT snapshot FROM batches WHERE id=?", (batch_id,))["snapshot"])


def saved_task(app, content, account, options_snapshot):
    batch_id, task_id = str(uuid4()), str(uuid4())
    store = app.state.service.store
    store.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", (
        batch_id, str(uuid4()), content['id'], content['title'], 'video', 'publish', 'before',
        dump(options_snapshot), dump([account['id']]),
    ))
    store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence) VALUES (?,?,?,?,?,'failed','Saved task',0)",
                  (task_id, batch_id, account['id'], account['label'], 'bilibili'))
    return batch_id, task_id


def test_schema_five_upgrade_preserves_preferences_and_frozen_batches(tmp_path):
    directory = tmp_path / 'old'
    directory.mkdir()
    original = {'platformOptions': {'bilibili': {'copyright': '1', 'tid': '171'}}}
    db = sqlite3.connect(directory / 'library.sqlite3')
    db.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated TEXT NOT NULL, configured INTEGER NOT NULL);
        CREATE TABLE account_preferences(account_id TEXT PRIMARY KEY, default_selected INTEGER NOT NULL DEFAULT 1, wechat_template_id TEXT NOT NULL DEFAULT 'clean');
        CREATE TABLE batches(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, content_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, created TEXT NOT NULL, snapshot TEXT NOT NULL, selection TEXT NOT NULL);
        INSERT INTO accounts VALUES ('bili','bilibili','Saved account','ready','Preserved connection','before',0);
        INSERT INTO account_preferences VALUES ('bili',0,'forest');
        PRAGMA user_version=5;
    """)
    db.execute("INSERT INTO batches VALUES ('batch','request','content','Saved video','video','publish','before',?,'[]')", (dump(original),))
    db.commit()
    db.close()
    for _ in range(2):
        store = Store(directory)
        try:
            account = store.account(store.one("SELECT * FROM accounts WHERE id='bili'"))
            assert account['videoOptions'] is None and account['defaultSelected'] is False
            assert account['status'] == 'ready' and account['message'] == 'Preserved connection'
            assert store.one("SELECT * FROM account_preferences WHERE account_id='bili'")['wechat_template_id'] == 'forest'
            assert store.one('PRAGMA user_version')['user_version'] == 7
            assert json.loads(store.one("SELECT snapshot FROM batches WHERE id='batch'")['snapshot']) == original
        finally:
            store.close()


def test_video_preferences_preserve_partial_updates_restart_and_reconnection(tmp_path):
    directory = tmp_path / 'data'
    options = {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'}
    app = create_app(directory, TOKEN, runtime=BilibiliRuntime())
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        account = ready_account(client, 'bilibili')
        assert account['videoOptions'] is None
        saved = preferences(client, account, options, defaultSelected=False)
        assert saved['videoOptions'] == options and saved['defaultSelected'] is False
        assert client.put(f"/api/accounts/{account['id']}/preferences", json={'defaultSelected': True}).json()['videoOptions'] == options
        saved = preferences(client, account, {'tid': '171'})
        assert saved['videoOptions'] == {'tid': '171'}  # No invented original-work claim.
        content = video_content(client, tmp_path, videoOptionsSource='account')
        assert not preflight(client, content, [account])['ok']
        preferences(client, account, options)
    app = create_app(directory, TOKEN, runtime=BilibiliRuntime())
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        current = client.get('/api/state').json()['accounts'][0]
        assert current['videoOptions'] == options and current['defaultSelected'] is True
        assert client.post(f"/api/accounts/{account['id']}/login").status_code == 200
        state = wait_state(client, lambda value: value['accounts'][0]['status'] == 'ready')
        assert state['accounts'][0]['videoOptions'] == options
        assert preferences(client, account, {})['videoOptions'] == {}
        assert client.delete(f"/api/accounts/{account['id']}").status_code == 200
        assert app.state.service.store.all('SELECT * FROM account_preferences') == []


@pytest.mark.parametrize('options', [
    None, [], {'tid': 171}, {'tid': True}, {'unknown': 'value'}, {'copyright': '3'},
    {'tid': '999999'}, {'source': 'x' * 2001}, {str(index): 'value' for index in range(11)},
])
def test_invalid_video_preferences_reject_entire_update(bili_api, options):
    client, _, app = bili_api
    account = ready_account(client, 'bilibili')
    preferences(client, account, {'copyright': '1', 'tid': '171'})
    before = app.state.service.store.all('SELECT * FROM account_preferences')
    response = client.put(f"/api/accounts/{account['id']}/preferences", json={'defaultSelected': False, 'videoOptions': options})
    assert response.status_code in {400, 422}, response.text
    assert app.state.service.store.all('SELECT * FROM account_preferences') == before


@pytest.mark.parametrize('platform', ['zhihu', 'douyin'])
def test_platform_cannot_save_another_platform_video_fields(bili_api, platform):
    client, _, app = bili_api
    account = ready_account(client, platform)
    before = app.state.service.store.all('SELECT * FROM account_preferences')
    response = client.put(f"/api/accounts/{account['id']}/preferences", json={'videoOptions': {'tid': '171'}, 'defaultSelected': False})
    assert response.status_code == 400
    assert app.state.service.store.all('SELECT * FROM account_preferences') == before


def test_same_platform_accounts_use_their_own_categories_and_frozen_options(bili_api, tmp_path):
    client, runtime, app = bili_api
    runtime.categories = [CATEGORIES[0]]
    first = ready_account(client, 'bilibili')
    runtime.categories = [CATEGORIES[1]]
    second = ready_account(client, 'bilibili')
    first_options = {'copyright': '1', 'tid': '171'}
    second_options = {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'}
    preferences(client, first, first_options)
    preferences(client, second, second_options)
    # The retained content fields deliberately conflict with one account and
    # must not silently determine a desktop publication after the UI moves them.
    content = video_content(client, tmp_path, videoOptionsSource='account')
    original = copy.deepcopy(content)
    assert preflight(client, content, [first, second])['ok']
    assert preflight(client, content, [second, first])['ok']

    async def hold_first(payload, event, _job):
        if payload['account_id'] == first['id']:
            runtime.started.set()
            while not runtime.release.is_set():
                await asyncio.sleep(.01)
        await event({'event': 'submitting', 'message': 'fixture submission'})
        return {'status': 'submitted', 'message': 'fixture receipt'}

    runtime.publish_hook = hold_first
    request = request_batch(content, [first, second])
    response = client.post('/api/batches', json=request)
    assert response.status_code == 200, response.text
    batch_id = response.json()['id']
    assert runtime.started.wait(3)
    expected = {first['id']: first_options, second['id']: second_options}
    assert snapshot(app, batch_id)['accountVideoOptions'] == expected
    preferences(client, first, {'copyright': '2', 'tid': '171', 'source': 'https://example.test/changed'})
    preferences(client, second, {'copyright': '1', 'tid': '201'})
    replay = client.post('/api/batches', json=request)
    assert replay.status_code == 200 and replay.json()['id'] == batch_id
    runtime.release.set()
    result = completed_batch(client, batch_id)
    assert all(task['status'] == 'submitted' for task in result['tasks'])
    assert {payload['account_id']: payload['options'] for payload in runtime.publish_calls} == expected
    assert snapshot(app, batch_id)['accountVideoOptions'] == expected
    saved = app.state.service.store.content(app.state.service.content_row(content['id']))
    assert saved['platformOptions'] == original['platformOptions'] and saved['videoOptionsSource'] == 'account'


def test_explicit_account_source_never_falls_back_to_hidden_legacy_options(bili_api, tmp_path):
    client, runtime, _ = bili_api
    account = ready_account(client, 'bilibili')
    content = video_content(client, tmp_path, videoOptionsSource='account')
    assert not preflight(client, content, [account])['ok']
    preferences(client, account, {})
    assert not preflight(client, content, [account])['ok']
    assert client.post('/api/batches', json=request_batch(content, [account])).status_code == 400
    assert runtime.publish_calls == []


@pytest.mark.parametrize('source, content_options, expected', [
    ('content', {'copyright': '1', 'tid': '171'}, {'copyright': '1', 'tid': '171'}),
    ('content', {}, {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'}),
    ('account', {'copyright': '1', 'tid': '171'}, {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'}),
])
def test_source_mode_resolves_once_without_mixing_content_and_account_fields(bili_api, tmp_path, source, content_options, expected):
    client, runtime, app = bili_api
    account = ready_account(client, 'bilibili')
    preferences(client, account, {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'})
    content = video_content(client, tmp_path, videoOptionsSource=source, platformOptions={'bilibili': content_options})
    response = client.post('/api/batches', json=request_batch(content, [account]))
    assert response.status_code == 200, response.text
    completed_batch(client, response.json()['id'])
    assert runtime.publish_calls[0]['options'] == expected
    assert snapshot(app, response.json()['id'])['accountVideoOptions'] == {account['id']: expected}


def test_partial_content_options_do_not_borrow_account_fields_or_originality(bili_api, tmp_path):
    client, runtime, _ = bili_api
    account = ready_account(client, 'bilibili')
    preferences(client, account, {'copyright': '2', 'tid': '201', 'source': 'https://example.test/original'})
    content = video_content(client, tmp_path, platformOptions={'bilibili': {'copyright': '1'}})
    check = preflight(client, content, [account])
    assert not check['ok'] and any('分区' in error for error in check['targets'][0]['errors'])
    assert client.post('/api/batches', json=request_batch(content, [account])).status_code == 400
    assert runtime.publish_calls == []


@pytest.mark.parametrize('legacy', [True, False])
def test_retry_keeps_old_or_new_snapshot_options_instead_of_live_preferences(bili_api, tmp_path, legacy):
    client, runtime, app = bili_api
    account = ready_account(client, 'bilibili')
    content = video_content(client, tmp_path, videoOptionsSource='account')
    preferences(client, account, {'copyright': '1', 'tid': '201'})
    frozen = dict(content)
    frozen.pop('videoOptionsSource', None)
    expected = {'copyright': '1', 'tid': '171'}
    if not legacy:
        expected = {'copyright': '2', 'tid': '171', 'source': 'https://example.test/frozen'}
        frozen['accountVideoOptions'] = {account['id']: expected}
    batch_id, task_id = saved_task(app, content, account, frozen)
    response = client.post(f'/api/tasks/{task_id}/retry')
    assert response.status_code == 200, response.text
    result = completed_batch(client, batch_id)
    assert result['tasks'][0]['status'] == 'submitted'
    assert runtime.publish_calls[0]['options'] == expected
    assert snapshot(app, batch_id) == frozen


@pytest.mark.parametrize('options', [None, {}, {'different-account': {'copyright': '1', 'tid': '171'}}])
def test_invalid_frozen_map_does_not_fall_back_to_live_options(bili_api, tmp_path, options):
    client, runtime, app = bili_api
    account = ready_account(client, 'bilibili')
    preferences(client, account, {'copyright': '1', 'tid': '171'})
    content = video_content(client, tmp_path)
    frozen = {**content, 'accountVideoOptions': options}
    _, task_id = saved_task(app, content, account, frozen)
    response = client.post(f'/api/tasks/{task_id}/retry')
    assert response.status_code == 400
    assert runtime.publish_calls == []


def test_mcp_defaults_to_content_compatibility_and_can_select_account_options(tmp_path):
    runtime = BilibiliRuntime()
    app = create_app(tmp_path / 'data', TOKEN, runtime=runtime, mcp_token=MCP_TOKEN, instance_id=INSTANCE)
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        account = ready_account(client, 'bilibili')
        preferences(client, account, {'copyright': '1', 'tid': '201'})
        imported = video_content(client, tmp_path)
        values = {'kind': 'video', 'title': 'MCP video', 'videoAssetId': imported['videoAssetId'], 'tags': ['知识'],
                  'platformOptions': {'bilibili': {'copyright': '1', 'tid': '171'}}}
        saved = result_value(call(client, 'save_content', {'content': values}))['content']
        assert saved['videoOptionsSource'] == 'content'
        published = result_value(call(client, 'publish', {'contentId': saved['id'], 'accountIds': [account['id']], 'requestId': str(uuid4())}))
        batch = published.get('batch', published)
        completed_batch(client, batch['id'])
        assert runtime.publish_calls[-1]['options'] == {'copyright': '1', 'tid': '171'}
        values['videoOptionsSource'] = 'account'
        second = result_value(call(client, 'save_content', {'content': values}))['content']
        published = result_value(call(client, 'publish', {'contentId': second['id'], 'accountIds': [account['id']], 'requestId': str(uuid4())}))
        batch = published.get('batch', published)
        completed_batch(client, batch['id'])
        assert runtime.publish_calls[-1]['options'] == {'copyright': '1', 'tid': '201'}
