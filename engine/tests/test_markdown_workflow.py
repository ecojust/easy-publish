"""Direct Markdown drafts, retired model routes, and safe one-time upgrades."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from easy_publish.app import create_app
from easy_publish.content import ContentInput, markdown_html
from easy_publish.markdown_migration import literal_markdown
from easy_publish.store import Store, dump
from test_core import TOKEN, FakeRuntime, api, content_input, create_content, ready_account


def legacy_data(**changes):
    data = ContentInput(**content_input()).model_dump()
    data.update(inputFormat="plain", sourceText="最新原文。", body="", formatModel=None, formatSourceHash=None)
    data.update(changes)
    return data


def fingerprint(data):
    return hashlib.sha256(dump([data['sourceText'], data['imageAssetIds']]).encode()).hexdigest()


def seed_version_six(directory, values):
    store = Store(directory)
    for identifier, value in values.items():
        store.execute('INSERT INTO contents VALUES (?,?,?,?,?)', (identifier, dump(value), 'created', 'updated', 9))
    frozen = dump(next(iter(values.values())))
    store.execute("INSERT INTO batches VALUES ('batch','request','content','Old task','article','publish','created',?,'[]')", (frozen,))
    store.execute('PRAGMA user_version=6')
    store.close()
    return frozen


@pytest.mark.parametrize('generated', [False, True])
def test_migration_preserves_current_text_valid_results_and_frozen_batches(tmp_path, generated):
    image_id = str(uuid4())
    original = '## 这是原文符号\n\n保留【图片1】和【图片9】。\n<script>alert(1)</script>\n![不是图片](https://example.test/private.png)'
    previous = legacy_data(sourceText=original, imageAssetIds=[image_id], body='## 以前生成的结果\n\n正文')
    previous['formatSourceHash'] = fingerprint(previous) if generated else 'stale-hash'
    previous['formatModel'] = 'old/model'
    frozen = seed_version_six(tmp_path, {'content': previous})
    first = None
    for _ in range(2):
        store = Store(tmp_path)
        try:
            row = store.one("SELECT * FROM contents WHERE id='content'")
            migrated = json.loads(row['data'])
            assert migrated['inputFormat'] == 'markdown'
            assert migrated['sourceText'] == original
            assert migrated['legacyFormatting'] == {key: previous[key] for key in ('inputFormat','sourceText','body','imageAssetIds','formatSourceHash','formatModel')}
            assert row['revision'] == 10 and row['created'] == 'created' and row['updated'] == 'updated'
            assert store.one('PRAGMA user_version')['user_version'] == 7
            assert store.one("SELECT snapshot FROM batches WHERE id='batch'")['snapshot'] == frozen
            public = store.content(row)
            assert not {'formatReady','formatModel','formatSourceHash','legacyFormatting'} & public.keys()
            assert public['sourceText'] == original
            if generated:
                assert migrated['body'] == previous['body']
            else:
                rendered = markdown_html(migrated['body'])
                assert '<script' not in rendered and '<h2' not in rendered
                assert f'src="easy-asset://{image_id}"' in rendered
                assert rendered.count('<img') == 1 and '【图片9】' in rendered
                assert previous['body'] not in migrated['body']
            if first is None:
                first = row
            else:
                assert row == first
        finally:
            store.close()


def test_migration_without_generated_result_restores_literal_prose(tmp_path):
    source = '# 标题符号\r\n    缩进文本\r\t制表符\n[不是链接](https://example.test)\n`代码符号` & <b>标签</b>'
    previous = legacy_data(sourceText=source)
    seed_version_six(tmp_path, {'content': previous})
    store = Store(tmp_path)
    try:
        value = store.content(store.one("SELECT * FROM contents WHERE id='content'"))
        assert value['sourceText'] == source
        html = markdown_html(value['body'])
        for tag in ('<h1','<pre','<code','<a ','<b>'):
            assert tag not in html
        assert '&lt;b&gt;' in html and '&amp;' in html
    finally:
        store.close()


def test_unknown_and_oversized_image_markers_remain_literal():
    source = '【图片' + '9' * 5000 + '】\n【图片0】\n【图片2】'
    rendered = markdown_html(literal_markdown(source, ['known-id']))
    assert '<img' not in rendered and '【图片2】' in rendered


def test_migration_leaves_existing_markdown_and_videos_byte_identical(tmp_path):
    markdown = ContentInput(**content_input(body='# Existing Markdown')).model_dump()
    video = ContentInput(**content_input(kind='video')).model_dump()
    seed_version_six(tmp_path, {'markdown': markdown, 'video': video})
    store = Store(tmp_path)
    try:
        for identifier, previous in [('markdown',markdown),('video',video)]:
            row = store.one('SELECT * FROM contents WHERE id=?', (identifier,))
            assert row['data'] == dump(previous) and row['revision'] == 9
    finally:
        store.close()


def test_editing_migrated_markdown_preserves_readonly_original_and_archive(tmp_path):
    original = legacy_data(sourceText='不能丢失的旧原文。', body='old stale generated Markdown')
    seed_version_six(tmp_path, {'content': original})
    app = create_app(tmp_path, TOKEN, runtime=FakeRuntime())
    with TestClient(app, headers={'Authorization':'Bearer '+TOKEN}) as client:
        store = app.state.service.store
        archive = json.loads(store.one("SELECT data FROM contents WHERE id='content'")['data'])['legacyFormatting']
        response = client.put('/api/contents/content', json=content_input(body='## 用户编辑后的 Markdown', sourceText='', inputFormat='markdown'))
        assert response.status_code == 200, response.text
        assert response.json()['body'] == '## 用户编辑后的 Markdown'
        assert response.json()['sourceText'] == original['sourceText']
        assert json.loads(store.one("SELECT data FROM contents WHERE id='content'")['data'])['legacyFormatting'] == archive
    app = create_app(tmp_path, TOKEN, runtime=FakeRuntime())
    with TestClient(app, headers={'Authorization':'Bearer '+TOKEN}) as client:
        content = client.get('/api/state').json()['contents'][0]
        assert content['body'] == '## 用户编辑后的 Markdown' and content['sourceText'] == original['sourceText']


def test_markdown_edits_preview_immediately_without_generation(api):
    client, runtime, app = api
    original = '# 标题\n\n这是 **Markdown** 正文。'
    content = create_content(client, body=original)
    assert content['inputFormat'] == 'markdown' and content['body'] == original
    assert not {'formatReady','formatModel'} & content.keys()
    assert 'formatting' not in client.get('/api/state').json()
    preview = client.post('/api/preview/article', json={'body':original,'articleTemplateId':'clean'})
    assert preview.status_code == 200 and '<strong' in preview.json()['html']
    account = ready_account(client, 'zhihu')
    response = client.post('/api/preflight', json={'contentId':content['id'],'accountIds':[account['id']],'mode':'publish'})
    assert response.status_code == 200 and response.json()['ok']
    changed = client.put(f"/api/contents/{content['id']}",json=content_input(body='## 新的 Markdown')).json()
    assert changed['body'] == '## 新的 Markdown'
    assert runtime.publish_calls == []


@pytest.mark.parametrize('method,path', [
    ('POST','/api/formatting/check'), ('POST','/api/contents/content/format'),
    ('GET','/api/format-jobs/request'), ('POST','/api/format-jobs/request/cancel'),
])
def test_retired_model_endpoints_are_absent(api, method, path):
    client, runtime, _ = api
    response = client.request(method,path,json={'revision':1,'requestId':str(uuid4())})
    assert response.status_code == 404
    assert runtime.publish_calls == []


def test_plain_input_is_rejected_instead_of_creating_an_unusable_ai_draft(api):
    client, _, _ = api
    response = client.post('/api/contents',json=content_input(inputFormat='plain',sourceText='text'))
    assert response.status_code == 422
    assert client.get('/api/state').json()['contents'] == []


@pytest.mark.parametrize('character', ['\t', '&'])
def test_maximum_legacy_text_does_not_expand_or_block_library_and_save(tmp_path, character):
    from easy_publish.app import ArticlePreviewInput
    source = character * 500_000
    previous = legacy_data(sourceText=source)
    seed_version_six(tmp_path, {'content': previous})
    app = create_app(tmp_path, TOKEN, runtime=FakeRuntime())
    with TestClient(app, headers={'Authorization':'Bearer '+TOKEN}) as client:
        response = client.get('/api/state')
        assert response.status_code == 200
        assert len(response.content) < 4_000_000
        content = response.json()['contents'][0]
        # A lossless raw-Markdown fallback fits both input and preview limits.
        assert content['body'] == source and content['sourceText'] == source
        assert ArticlePreviewInput(body=content['body']).body == source
        saved = client.put('/api/contents/content',json=content_input(body=content['body'],sourceText=''))
        assert saved.status_code == 200, saved.text[:200]
        assert saved.json()['body'] == source and saved.json()['sourceText'] == source
        raw = json.loads(app.state.service.store.one("SELECT data FROM contents WHERE id='content'")['data'])
        assert raw['legacyFormatting']['sourceText'] == source


@pytest.mark.parametrize('fits', [False, True])
def test_expansion_fallback_maps_images_only_when_complete_result_fits(fits):
    source = '&' * (100_001 if fits else 499_995) + '【图片1】'
    result = literal_markdown(source, ['image-id'])
    assert len(result) <= 500_000
    if fits:
        assert result == '&' * 100_001 + '![正文图片 1](easy-asset://image-id)'
    else:
        assert result == source


def test_new_and_unmigrated_drafts_cannot_fabricate_a_readonly_original(api):
    client, _, app = api
    content = create_content(client, body='## Actual Markdown', sourceText='invented old original')
    assert content['sourceText'] == ''
    changed = client.put(f"/api/contents/{content['id']}",json=content_input(body='Updated',sourceText='another invented original'))
    assert changed.status_code == 200 and changed.json()['sourceText'] == ''
    stored = json.loads(app.state.service.store.one('SELECT data FROM contents WHERE id=?',(content['id'],))['data'])
    assert stored['sourceText'] == '' and 'legacyFormatting' not in stored
