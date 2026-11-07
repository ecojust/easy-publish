"""One shared draft is projected separately before each target reads assets."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from easy_publish.providers import social
from easy_publish.providers.base import PublishPayload
from easy_publish.target_projection import project_payload
from test_bilibili_integration import bili_api, preflight, video_content
from test_core import create_content, ready_account, request_batch, completed_batch


def image_asset(client, tmp_path, name):
    path = tmp_path / (name + '.png')
    Image.new('RGB', (12, 12), 'blue').save(path)
    response = client.post('/api/assets/import', json={'path': str(path), 'kind': 'image'})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_one_filled_video_projects_each_queue_target_and_preserves_shared_snapshot(bili_api, tmp_path):
    client, runtime, app = bili_api
    accounts = [ready_account(client, platform) for platform in ('douyin', 'bilibili', 'kuaishou', 'xiaohongshu', 'channels')]
    cover = image_asset(client, tmp_path, 'cover')
    unused_image = image_asset(client, tmp_path, 'article-only')
    options = {'bilibili': {'copyright': '1', 'tid': '171', 'source': 'not a URL, unused for original', 'other': 'unused'},
               'kuaishou': {'copyright': '2'}, 'douyin': {'source': 'unused'}}
    content = video_content(client, tmp_path, title='跨平台视频测试用例', coverAssetId=cover,
                            body='![article-only](https://example.invalid/unused.png)', author='unused author',
                            imageAssetIds=[unused_image], platformOptions=options)
    original = copy.deepcopy(content)
    service = app.state.service
    # A field used only by article publishing must not block this video batch.
    Path(service.asset_row(unused_image)['path']).unlink()
    assert preflight(client, content, accounts)['ok']
    assert preflight(client, content, list(reversed(accounts)))['ok']
    response = client.post('/api/batches', json=request_batch(content, accounts))
    assert response.status_code == 200, response.text
    batch = completed_batch(client, response.json()['id'])
    assert all(task['status'] == 'submitted' for task in batch['tasks'])
    assert len(runtime.publish_calls) == len(accounts)
    for payload in runtime.publish_calls:
        assert payload['body_html'] == '' and payload['images'] == {} and payload['author'] == ''
        assert payload['video_path'] and payload['description'] == content['description']
        if payload['platform_id'] == 'douyin':
            assert payload['cover_path'] is None
        else:
            assert payload['cover_path'] == service.asset_row(cover)['path']
        assert payload['options'] == ({'copyright': '1', 'tid': '171'} if payload['platform_id'] == 'bilibili' else {})
    saved = service.store.content(service.content_row(content['id']))
    snapshot = json.loads(service.store.one('SELECT snapshot FROM batches WHERE id=?', (batch['id'],))['snapshot'])
    assert snapshot == original and content == original
    assert {key: value for key, value in saved.items() if key != 'platformPublicationStates'} == {
        key: value for key, value in original.items() if key != 'platformPublicationStates'}


@pytest.mark.parametrize('using_platform', ['bilibili', 'kuaishou', 'xiaohongshu', 'channels'])
def test_missing_cover_only_blocks_targets_that_use_it(bili_api, tmp_path, using_platform):
    client, _, app = bili_api
    douyin = ready_account(client, 'douyin')
    other = ready_account(client, using_platform)
    cover = image_asset(client, tmp_path, 'removed-cover')
    content = video_content(client, tmp_path, title='跨平台视频测试用例', coverAssetId=cover)
    service = app.state.service
    Path(service.asset_row(cover)['path']).unlink()
    for accounts in ([douyin, other], [other, douyin]):
        result = preflight(client, content, accounts)
        by_account = {target['accountId']: target for target in result['targets']}
        assert by_account[douyin['id']]['errors'] == []
        assert any('素材' in error for error in by_account[other['id']]['errors'])
    # Even a disappeared asset row is irrelevant to the Douyin target.
    service.store.execute('DELETE FROM assets WHERE id=?', (cover,))
    with patch.object(service, 'asset_row', wraps=service.asset_row) as read:
        payload = asyncio.run(service.payload(content, {'platform': 'douyin', 'account_id': douyin['id']}, 'publish'))
    assert payload['cover_path'] is None
    assert all(call.args != (cover,) for call in read.call_args_list)
    assert preflight(client, content, [douyin])['ok']
    Path(service.asset_row(content['videoAssetId'])['path']).unlink()
    assert not preflight(client, content, [douyin])['ok']  # the actual video remains required


def test_article_targets_ignore_video_and_unused_cover_but_keep_real_requirements(bili_api, tmp_path):
    client, _, app = bili_api
    dayu = ready_account(client, 'dayu')
    zhihu = ready_account(client, 'zhihu')
    xueqiu = ready_account(client, 'xueqiu')
    cover = image_asset(client, tmp_path, 'unused-dayu-cover')
    video = video_content(client, tmp_path)
    options = {'dayu': {'source': '个人观点', 'source_url': 'not a URL'},
               'xueqiu': {'declaration': '不添加创作声明'}}
    content = create_content(client, title='文章共享字段测试', body='实际文章正文', author='作者', tags=['unused tag'],
                             videoAssetId=video['videoAssetId'], coverAssetId=cover, platformOptions=options)
    service = app.state.service
    Path(service.asset_row(video['videoAssetId'])['path']).unlink()
    Path(service.asset_row(cover)['path']).unlink()
    assert preflight(client, content, [dayu, zhihu])['ok']
    assert not preflight(client, content, [xueqiu])['ok']  # Xueqiu consumes this cover.
    task = {'platform': 'dayu', 'account_id': dayu['id']}
    payload = asyncio.run(service.payload(content, task, 'publish'))
    assert payload['video_path'] is None and payload['cover_path'] is None and payload['tags'] == []
    assert payload['author'] == '作者' and payload['options'] == {'source': '个人观点'}
    original = copy.deepcopy(content)
    changed = copy.deepcopy(content)
    changed['platformOptions']['dayu']['source'] = '转载'
    assert not service.preflight(changed, [dayu['id']], 'publish')['ok']
    assert content == original


def test_direct_article_adapter_projects_conditional_options_without_mutating_payload():
    payload = PublishPayload('dayu', 'fixture', 'article', 'publish', '文章测试标题',
                             options={'source': '个人观点', 'source_url': 'unused invalid url', 'cover_indices': [0]})
    social._validate_options(payload)
    projected = project_payload(payload)
    assert projected.options == {'source': '个人观点', 'cover_indices': [0]}
    assert payload.options['source_url'] == 'unused invalid url'
    payload.options['source'] = '转载'
    with pytest.raises(ValueError, match='原文链接'):
        social._validate_options(payload)
