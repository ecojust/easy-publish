"""Real official-theme rendering plus isolated subprocess lifecycle failures."""
import asyncio
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time

import pytest

from easy_publish import wenyan_runtime as runtime
from easy_publish.article_templates import WENYAN_ARTICLE_TEMPLATES, markdown_html, render_article
from easy_publish.content import image_sources
from easy_publish.providers import wechat
from test_account_templates import article_with_cover, set_template, wechat_account
from test_core import api, completed_batch, request_batch


SOURCE = """## 文言排版

保留 **重点** 和 *语气*，这是一篇中文文章。

> 引用的内容。

1. 第一项
2. 第二项

![本地图片](easy-asset://image-a)

```python
if left < right:
    print("保留代码")
```

| 项目 | 内容 |
| --- | --- |
| 示例 | 不改变事实 |
"""


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_real_official_themes_share_one_process_preserve_images_and_cache():
    async def scenario():
        renderer = runtime.WenyanRenderer()
        outputs = []
        process = None
        try:
            for template in WENYAN_ARTICLE_TEMPLATES:
                html = await renderer.render(SOURCE, template['id'].removeprefix('wenyan-'))
                outputs.append(html)
                assert 'style=' in html and '文言排版' in html and '不改变事实' in html
                assert '<script' not in html and '<style' not in html
                assert image_sources(html) == ['easy-asset://image-a']
                assert wechat._body(html).image_ids == ['image-a']
                if process is None:
                    process = renderer._process
                assert renderer._process is process
            assert len(set(outputs)) == 8
            assert len(renderer._cache) == 8
            # Cache remains useful if the idle renderer exits; repeating a
            # preview must not start another process or change final HTML.
            await renderer._stop()
            assert await renderer.render(SOURCE, 'default') == outputs[0]
            assert renderer._process is None
        finally:
            await renderer.close()
        assert process.returncode is not None
        assert renderer._temporary is None
        assert not renderer._cache
    asyncio.run(scenario())


def test_real_renderer_disallows_author_html_and_active_uris_without_changing_code():
    source = """## 安全正文

<div style="background:url(https://example.invalid/leak)">作者HTML</div>
<script>alert('private-article')</script>
<img src="file:///private/secret" onerror="alert(1)">

[危险链接](javascript:alert%281%29)

```html
<script>code_example()</script>
```

![已导入图片](easy-asset://image-a)
"""
    async def scenario():
        renderer = runtime.WenyanRenderer()
        try:
            html = await renderer.render(source, 'default')
            for tag, attrs in Markup(html).elements:
                assert tag not in {'script', 'style', 'iframe', 'object', 'embed', 'form'}
                assert not any(attribute.startswith('on') for attribute in attrs)
                assert not any(re.match(r'(?:javascript|file|data):', attrs.get(attribute, ''), re.I) for attribute in ('src', 'href'))
                assert 'url(' not in attrs.get('style', '')
            assert 'code_example()' in unescape(re.sub(r'<[^>]+>', '', html))
            assert image_sources(html) == ['easy-asset://image-a']
        finally:
            await renderer.close()
    asyncio.run(scenario())


def test_real_wenyan_preview_matches_frozen_queue_payload(api, tmp_path):
    client, worker, app = api
    account = wechat_account(client)
    set_template(client, account['id'], 'wenyan-orangeheart')
    content = article_with_cover(client, tmp_path, body='## 实际主题\n\n保留 **相同正文**。')
    request = {'body': content['body'], 'accountId': account['id']}
    preview = client.post('/api/preview/article', json=request)
    assert preview.status_code == 200, preview.text
    batch = client.post('/api/batches', json=request_batch(content, [account]))
    assert batch.status_code == 200, batch.text
    set_template(client, account['id'], 'wenyan-purple')
    result = completed_batch(client, batch.json()['id'])
    assert result['tasks'][0]['status'] == 'published'
    assert worker.publish_calls[0]['body_html'] == preview.json()['html']
    snapshot = json.loads(app.state.service.store.one('SELECT snapshot FROM batches WHERE id=?', (batch.json()['id'],))['snapshot'])
    assert snapshot['accountTemplateIds'][account['id']] == 'wenyan-orangeheart'
    changed = client.post('/api/preview/article', json=request)
    assert changed.status_code == 200 and changed.json()['html'] != preview.json()['html']


def test_native_platforms_and_image_checks_do_not_start_wenyan(monkeypatch):
    def fail():
        raise AssertionError('No process is needed to parse images or adapt a native editor')
    monkeypatch.setattr(runtime, '_runtime_paths', fail)
    assert image_sources(markdown_html(SOURCE)) == ['easy-asset://image-a']
    for platform in ('zhihu', 'dayu', 'xueqiu'):
        assert render_article(SOURCE, 'wenyan-purple', platform) == render_article(SOURCE, 'clean', platform)


def fake_child(monkeypatch, tmp_path, mode):
    child = tmp_path / 'fixture.py'
    child.write_text('''import json, sys, time
for line in sys.stdin:
    value = json.loads(line)
    if MODE == 'hang': time.sleep(30)
    if MODE == 'large': print('x' * 2000, flush=True)
    elif MODE == 'invalid': print('private-article invalid output', flush=True)
    elif MODE == 'error': print(json.dumps({'error':'private-article provider message'}), flush=True)
    else: print(json.dumps({'html':'<p>fixture result</p>'}), flush=True)
'''.replace('MODE', repr(mode)), encoding='utf-8')
    monkeypatch.setattr(runtime, '_runtime_paths', lambda: (Path(sys.executable), child))


@pytest.mark.parametrize('mode', ['hang', 'large', 'invalid', 'error'])
def test_renderer_failure_is_bounded_private_and_retryable(monkeypatch, tmp_path, mode):
    fake_child(monkeypatch, tmp_path, mode)
    monkeypatch.setattr(runtime, 'RENDER_TIMEOUT', .2)
    monkeypatch.setattr(runtime, 'MAX_OUTPUT', 1000)
    async def scenario():
        renderer = runtime.WenyanRenderer()
        started = time.monotonic()
        with pytest.raises(runtime.WenyanError) as failure:
            await renderer.render('private-article', 'default')
        assert 'private-article' not in str(failure.value)
        assert renderer._process is None and not renderer._cache
        assert time.monotonic() - started < 5
        fake_child(monkeypatch, tmp_path, 'success')
        assert await renderer.render('private-article', 'default') == '<p>fixture result</p>'
        await renderer.close()
    asyncio.run(scenario())


def test_shutdown_cancels_running_and_queued_previews(monkeypatch, tmp_path):
    fake_child(monkeypatch, tmp_path, 'hang')
    async def scenario():
        renderer = runtime.WenyanRenderer()
        tasks = [asyncio.create_task(renderer.render('article', theme)) for theme in ('default', 'purple')]
        async with asyncio.timeout(3):
            while renderer._process is None:
                await asyncio.sleep(.01)
        process = renderer._process
        await renderer.close()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert process.returncode is not None and renderer._process is None
    asyncio.run(scenario())


def test_renderer_environment_ignores_personal_node_and_provider_settings(monkeypatch):
    for name in ('NODE_OPTIONS', 'NODE_PATH', 'PLAYWRIGHT_NODEJS_PATH', 'OPENAI_API_KEY', 'HOME', 'PATH'):
        monkeypatch.setenv(name, 'private-value')
    environment = runtime._environment()
    assert 'private-value' not in environment.values()
    node, _ = runtime._runtime_paths()
    assert 'patchright/driver/node' in str(node).replace('\\', '/')


def test_cli_home_is_owned_and_removed_after_renderer_shutdown(tmp_path):
    async def scenario():
        renderer = runtime.WenyanRenderer()
        try:
            result = await renderer.render('## Markdown\n\n直接预览。', 'purple')
            assert '直接预览' in result
            owned = Path(renderer._temporary.name)
            assert owned.is_dir()
            environment = runtime._environment(owned)
            for key in ('HOME', 'USERPROFILE', 'APPDATA', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'):
                assert environment[key] == str(owned)
            assert list(owned.iterdir()) == [], 'CLI rendering must not create account or configuration stores'
        finally:
            await renderer.close()
        assert not owned.exists() and renderer._temporary is None
    asyncio.run(scenario())
