"""Real subprocess protocol fixtures, encrypted storage and cancellation tests."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from easy_publish.runtime import Runtime
from easy_publish.vault import Vault, VaultError


TEST_ENV = {"EASY_PUBLISH_TESTING": "1", "EASY_PUBLISH_TEST_KEY": base64.b64encode(b"t" * 32).decode()}


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, TEST_ENV)
        env.start()
        self.addCleanup(env.stop)

    def test_ciphertext_round_trip_and_account_binding(self):
        vault = Vault(self.root)
        self.assertTrue(vault.ready)
        secret = b'{"cookie":"fixture-secret-cookie"}'
        vault.put("account-a", "session", secret)
        first, _ = vault._location("account-a", "session")
        second, _ = vault._location("account-b", "session")
        self.assertNotIn(secret, first.read_bytes())
        self.assertEqual(vault.get("account-a", "session"), secret)
        second.write_bytes(first.read_bytes())
        with self.assertRaises(VaultError):
            vault.get("account-b", "session")
        if os.name != "nt":
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_tamper_detected_and_delete_removes_both_kinds(self):
        vault = Vault(self.root)
        vault.put("account", "credentials", b"sensitive")
        vault.put("account", "session", b"cookie")
        location, _ = vault._location("account", "session")
        location.write_bytes(location.read_bytes()[:-1] + b"x")
        with self.assertRaises(VaultError):
            vault.get("account", "session")
        vault.delete("account")
        self.assertIsNone(vault.get("account", "session"))
        self.assertIsNone(vault.get("account", "credentials"))

    def test_test_key_requires_both_switches(self):
        with patch.dict(os.environ, {"EASY_PUBLISH_TESTING": "0"}):
            vault = Vault(self.root)
        self.assertFalse(vault.ready)
        with self.assertRaises(VaultError):
            vault.put("account", "credentials", b"must not become plaintext")
        self.assertEqual(list(vault.directory.iterdir()), [])

    def test_invalid_test_key_does_not_fall_back(self):
        with patch.dict(os.environ, {"EASY_PUBLISH_TEST_KEY": "bad-key"}):
            vault = Vault(self.root)
        self.assertFalse(vault.ready)


FENCE_WORKER = """
import json, pathlib, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'event': 'submitting', 'message': 'fixture mutation'}), flush=True)
assert json.loads(sys.stdin.readline()) == {'ack': 'submitting'}
pathlib.Path(request['payload']['options']['marker']).write_text('mutated')
pathlib.Path(request['session_path']).write_text('{"cookies":[],"origins":[]}')
print(json.dumps({'event': 'result', 'status': 'submitted', 'message': 'fixture receipt', 'remote_id': 'fixture-123', 'url': None}), flush=True)
"""


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, TEST_ENV)
        self.env.start()
        self.runtime = Runtime(self.root)
        self.marker = self.root / "mutation-marker"
        self.payload = {"platform_id": "fixture", "account_id": "test-account", "kind": "article", "title": "fixture", "options": {"marker": str(self.marker)}}

    async def asyncTearDown(self):
        await self.runtime.shutdown()
        self.env.stop()
        self.temp.cleanup()

    def command(self, code):
        return patch.object(self.runtime, "_worker_command", return_value=[sys.executable, "-c", code])

    async def test_mutation_waits_for_durable_parent_ack(self):
        entered, persisted = asyncio.Event(), asyncio.Event()

        async def on_event(event):
            if event["event"] == "submitting":
                entered.set()
                await persisted.wait()

        with self.command(FENCE_WORKER):
            task = asyncio.create_task(self.runtime.run("publish", self.payload, on_event, "job-1"))
            await asyncio.wait_for(entered.wait(), 3)
            self.assertFalse(self.marker.exists())
            persisted.set()
            result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["remote_id"], "fixture-123")
        self.assertTrue(self.marker.exists())
        self.assertEqual(json.loads(self.runtime.vault.get("test-account", "session")), {"cookies": [], "origins": []})
        self.assertEqual(list((self.root / "work").iterdir()), [])

    async def test_failed_parent_commit_never_acknowledges_mutation(self):
        async def on_event(event):
            raise OSError("fixture database unavailable")

        with self.command(FENCE_WORKER):
            result = await self.runtime.run("publish", self.payload, on_event, "job-2")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(self.marker.exists())
        self.assertEqual(list((self.root / "work").iterdir()), [])

    async def test_timeout_after_fence_is_unknown_and_cleans_workspace(self):
        async def on_event(event):
            pass

        script = "import json,sys,time; json.loads(sys.stdin.readline()); print(json.dumps({'event':'submitting'}),flush=True); sys.stdin.readline(); time.sleep(60)"
        with self.command(script), patch.dict("easy_publish.runtime.OPERATION_TIMEOUTS", {"publish": 0.15}):
            result = await self.runtime.run("publish", self.payload, on_event, "job-3")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.runtime._jobs, {})
        self.assertEqual(list((self.root / "work").iterdir()), [])

    async def test_timeout_before_fence_is_retryable(self):
        async def on_event(event):
            pass

        with self.command("import sys,time;sys.stdin.readline();time.sleep(60)"), patch.dict("easy_publish.runtime.OPERATION_TIMEOUTS", {"check": 0.15}):
            result = await self.runtime.run("check", self.payload, on_event, "job-4")
        self.assertEqual(result["status"], "failed")

    async def test_async_cancellation_terminates_worker_and_cleans_workspace(self):
        entered = asyncio.Event()

        async def on_event(event):
            entered.set()

        script = "import json,sys,time;sys.stdin.readline();print(json.dumps({'event':'submitting'}),flush=True);sys.stdin.readline();time.sleep(60)"
        with self.command(script):
            task = asyncio.create_task(self.runtime.run("publish", self.payload, on_event, "job-5"))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        self.assertEqual(self.runtime._jobs, {})
        self.assertEqual(list((self.root / "work").iterdir()), [])

    async def test_cancel_method_kills_worker_child_process(self):
        entered = asyncio.Event()

        async def on_event(event):
            entered.set()

        script = """
import json,pathlib,subprocess,sys,time
request=json.loads(sys.stdin.readline())
child="import time,pathlib;time.sleep(0.8);pathlib.Path(%r).write_text('unexpected child mutation')" % request['payload']['options']['marker']
subprocess.Popen([sys.executable,'-c',child])
print(json.dumps({'event':'submitting'}),flush=True)
sys.stdin.readline()
time.sleep(60)
"""
        with self.command(script):
            task = asyncio.create_task(self.runtime.run("publish", self.payload, on_event, "job-6"))
            await asyncio.wait_for(entered.wait(), 3)
            await self.runtime.cancel("job-6")
            result = await asyncio.wait_for(task, 3)
        await asyncio.sleep(1)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(self.marker.exists(), "owned browser-like child must be killed too")

    async def test_credentials_only_in_worker_stdin_not_arguments(self):
        secret = "fixture-secret-password"
        self.runtime.vault.put("test-account", "credentials", json.dumps({"appSecret": secret}).encode())
        script = """
import json,sys
request=json.loads(sys.stdin.readline())
assert request['payload']['credentials']['appSecret']=='fixture-secret-password'
assert all('fixture-secret-password' not in value for value in sys.argv)
print(json.dumps({'event':'result','status':'ready','message':'fixture authenticated'}),flush=True)
"""

        async def on_event(event):
            self.assertNotIn(secret, json.dumps(event))

        with self.command(script):
            result = await self.runtime.run("check", self.payload, on_event, "job-7")
        self.assertEqual(result["status"], "ready")

    async def test_real_worker_suppresses_raw_output_and_waits_for_ack(self):
        self.runtime.vault.put("test-account", "credentials", json.dumps({"appSecret": "fixture-sensitive"}).encode())
        self.payload["title"] = "中文标题与电视发布测试"
        entered, persisted = asyncio.Event(), asyncio.Event()
        observed = []
        script = """
import os, pathlib, sys, types
sys.stdin.reconfigure(encoding='cp1252')  # Simulate a Windows ANSI text wrapper.
from easy_publish.providers import base
import easy_publish.providers as providers
fake = types.ModuleType('easy_publish.providers.social')
async def publish(payload, ctx):
    assert payload.title == '中文标题与电视发布测试'
    print('raw upstream fixture-sensitive', flush=True)
    os.write(1,b'native stdout fixture-sensitive\\n')
    os.write(2,b'native stderr fixture-sensitive\\n')
    ctx.progress('checking fixture-sensitive')
    ctx.submitting('fixture fence')
    pathlib.Path(payload.options['marker']).write_text('after ack')
    return base.ProviderResult('submitted','fixture-sensitive accepted','fixture-result')
fake.publish=publish
sys.modules['easy_publish.providers.social']=fake
providers.social=fake
from easy_publish.worker import worker_main
sys.exit(worker_main())
"""

        async def on_event(event):
            observed.append(event)
            if event["event"] == "submitting":
                entered.set()
                await persisted.wait()

        with self.command(script):
            task = asyncio.create_task(self.runtime.run("publish", self.payload, on_event, "actual-worker"))
            await asyncio.wait_for(entered.wait(), 3)
            self.assertFalse(self.marker.exists())
            persisted.set()
            result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["remote_id"], "fixture-result")
        self.assertTrue(self.marker.exists())
        self.assertNotIn("fixture-sensitive", json.dumps(observed) + json.dumps(result))
        self.assertEqual([event["event"] for event in observed], ["progress", "submitting"])

    async def test_bootloader_parent_is_not_mistaken_for_engine_owner(self):
        self.payload["title"] = "打包后的中文文章"
        worker_script = """
import sys,types
from easy_publish.providers import base
import easy_publish.providers as providers
fake=types.ModuleType('easy_publish.providers.social')
async def check(payload,ctx):
    return base.ProviderResult('ready',payload.title)
fake.check=check
providers.social=fake
sys.modules['easy_publish.providers.social']=fake
from easy_publish.worker import worker_main
sys.exit(worker_main())
"""
        bootloader = "import subprocess,sys;sys.exit(subprocess.call([sys.executable,'-c',sys.argv[1]]))"

        async def on_event(event):
            pass

        command = [sys.executable, "-c", bootloader, worker_script]
        with patch.object(self.runtime, "_worker_command", return_value=command):
            result = await self.runtime.run("check", self.payload, on_event, "bootloader-fixture")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["message"], self.payload["title"])

    async def test_shutdown_cancels_all_jobs_concurrently(self):
        entered = set()
        all_entered = asyncio.Event()
        self.runtime._jobs = {"first": object(), "second": object()}

        async def cancel(job_id):
            entered.add(job_id)
            if len(entered) == 2:
                all_entered.set()
            await all_entered.wait()
            if job_id == "first":
                raise OSError("fixture: one process already exited")

        try:
            with patch.object(self.runtime, "cancel", cancel):
                await asyncio.wait_for(self.runtime.shutdown(), 1)
        finally:
            self.runtime._jobs.clear()
        self.assertEqual(entered, {"first", "second"})

    async def test_stale_job_cleanup_never_follows_symlinks(self):
        work = self.root / "work"
        stale = work / "job-stale"
        stale.mkdir(parents=True)
        (stale / "session.json").write_text("old plaintext fixture")
        unrelated = work / "other-directory"
        unrelated.mkdir()
        outside = self.root / "keep-outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("must survive")
        try:
            (work / "job-link").symlink_to(outside, target_is_directory=True)
            (stale / "nested-link").symlink_to(outside, target_is_directory=True)
        except OSError:
            pass  # Windows may lack symlink privilege; directory test still runs.
        second = Runtime(self.root)
        await second.shutdown()
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual((outside / "keep.txt").read_text(), "must survive")

    async def test_session_must_be_bounded_json_and_not_a_symlink(self):
        session = self.root / "session-check.json"
        session.write_text('["not a storage object"]')
        with self.assertRaises(ValueError):
            Runtime._session_bytes(session)
        session.write_text('{"cookies": []}')
        with patch("easy_publish.runtime.MAX_SESSION_BYTES", 2):
            with self.assertRaises(ValueError):
                Runtime._session_bytes(session)

    async def test_actual_worker_stops_when_owning_engine_exits(self):
        workspace = self.root / "work" / "job-orphan-fixture"
        workspace.mkdir(parents=True)
        (workspace / "session.json").write_text('{"cookies": []}')
        worker_script = """
import asyncio,pathlib,sys,types
from easy_publish.providers import base
import easy_publish.providers as providers
fake=types.ModuleType('easy_publish.providers.social')
async def publish(payload,ctx):
    ctx.submitting('fixture operation')
    await asyncio.sleep(2)
    pathlib.Path(payload.options['marker']).write_text('orphan continued')
    return base.ProviderResult('submitted','fixture result')
fake.publish=publish
providers.social=fake
sys.modules['easy_publish.providers.social']=fake
from easy_publish.worker import worker_main
sys.exit(worker_main())
"""
        owner_script = """
import json,os,subprocess,sys
request=json.loads(sys.argv[2]); request['parent_pid']=os.getpid()
options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
worker=subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,**options)
worker.stdin.write(json.dumps(request)+'\\n'); worker.stdin.flush()
event=json.loads(worker.stdout.readline()); assert event['event']=='submitting'
worker.stdin.write('{"ack":"submitting"}\\n'); worker.stdin.flush()
os._exit(0)
"""
        request = {"operation": "publish", "payload": self.payload, "workspace": str(workspace), "session_path": str(workspace / "session.json"), "browser_path": None}
        owner = await asyncio.create_subprocess_exec(sys.executable, "-c", owner_script, worker_script, json.dumps(request), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self.assertEqual(await asyncio.wait_for(owner.wait(), 3), 0)
        await asyncio.sleep(2.2)
        self.assertFalse(self.marker.exists(), "orphan worker must not continue after parent exit")
        self.assertFalse(workspace.exists(), "watchdog must remove its own plaintext workspace")


if __name__ == "__main__":
    unittest.main()
