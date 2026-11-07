"""Offline release guards; these fixtures never create or update a GitHub release."""
from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location("publish_release", Path(__file__).resolve().parents[1] / "publish-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SOURCE = "a" * 40
HEAD = "b" * 40
RUN = 123
REPO = "owner/repository"


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run_fixture():
    return {"id": RUN, "status": "completed", "conclusion": "success", "head_sha": SOURCE,
            "head_repository": {"full_name": REPO}, "path": ".github/workflows/build.yml", "event": "pull_request"}


def jobs_fixture():
    return {"total_count": len(release.JOBS), "jobs": [{"name": name, "conclusion": "success"} for name in release.JOBS]}


def artifacts_fixture():
    return {"total_count": 3, "artifacts": [
        {"id": index, "name": f"easy-publish-0.1.3-{platform}", "expired": False, "size_in_bytes": 2048,
         "digest": "sha256:" + "c" * 64, "workflow_run": {"id": RUN, "head_sha": SOURCE}}
        for index, platform in enumerate(release.PLATFORMS, start=1)]}


class ReleaseGuards(unittest.TestCase):
    def test_accepts_successful_original_repository_build(self):
        release.verify_run(run_fixture(), jobs_fixture(), REPO, SOURCE, RUN)
        self.assertEqual(set(release.select_artifacts(artifacts_fixture(), "0.1.3", SOURCE, RUN)), set(release.PLATFORMS))

    def test_rejects_failed_foreign_or_wrong_workflow_builds(self):
        for update in ({"conclusion": "failure"}, {"status": "in_progress"}, {"head_sha": "d" * 40},
                       {"head_repository": {"full_name": "fork/repository"}}, {"path": ".github/workflows/untrusted.yml"}):
            with self.subTest(update=update), self.assertRaises(release.ReleaseError):
                release.verify_run({**run_fixture(), **update}, jobs_fixture(), REPO, SOURCE, RUN)
        jobs = jobs_fixture()
        jobs["jobs"][0]["conclusion"] = "skipped"
        with self.assertRaises(release.ReleaseError):
            release.verify_run(run_fixture(), jobs, REPO, SOURCE, RUN)

    def test_rejects_expired_duplicate_wrong_source_and_digestless_artifacts(self):
        for update in ({"expired": True}, {"digest": None}, {"digest": "sha256:bad"},
                       {"workflow_run": {"id": RUN, "head_sha": "d" * 40}},
                       {"workflow_run": {"id": RUN + 1, "head_sha": SOURCE}}):
            metadata = artifacts_fixture()
            metadata["artifacts"][0].update(update)
            with self.subTest(update=update), self.assertRaises(release.ReleaseError):
                release.select_artifacts(metadata, "0.1.3", SOURCE, RUN)
        metadata = artifacts_fixture()
        metadata["artifacts"].append(copy.deepcopy(metadata["artifacts"][0]))
        metadata["total_count"] += 1
        with self.assertRaises(release.ReleaseError):
            release.select_artifacts(metadata, "0.1.3", SOURCE, RUN)

    def test_only_documentation_and_release_tooling_can_differ(self):
        for path in ("README.md", "docs/releases/v0.1.3.md", "history/record.md", ".github/workflows/release.yml", "scripts/publish-release.py"):
            self.assertTrue(release.documentation_or_release_file(path), path)
        for path in ("package.json", "src/App.tsx", "engine/easy_publish/service.py", ".github/workflows/build.yml", "scripts/build-engine.py", "src-tauri/tauri.conf.json", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            self.assertFalse(release.documentation_or_release_file(path), path)

    def test_checkout_rejects_missing_tag_and_application_changes_before_api(self):
        with patch.object(release, "command", side_effect=[HEAD, SOURCE]):
            with self.assertRaisesRegex(release.ReleaseError, "existing release tag"):
                release.verify_checkout("v0.1.3", SOURCE)
        with patch.object(release, "command", side_effect=[HEAD, HEAD, "", "src/App.tsx\0"]):
            with self.assertRaisesRegex(release.ReleaseError, "Application or build"):
                release.verify_checkout("v0.1.3", SOURCE)

    def test_verified_zip_extracts_unchanged_installer_without_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, target = root / "bundle.zip", root / "installer.dmg"
            payload = b"DMG fixture" + b"koly" + bytes(508)
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("dmg/Original name.dmg", payload)
            artifact = {"digest": "sha256:" + release.sha256(archive)}
            release.unpack_installer(archive, artifact, target, ".dmg")
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse((root / "dmg").exists())
            artifact["digest"] = "sha256:" + "f" * 64
            with self.assertRaisesRegex(release.ReleaseError, "SHA-256 mismatch"):
                release.unpack_installer(archive, artifact, target, ".dmg")

    def test_zip_rejects_traversal_symlink_and_extra_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for names in (("../outside.exe",), ("/absolute.exe",), ("folder\\file.exe",), ("installer.exe", "extra.txt")):
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name in names:
                        bundle.writestr(name, b"MZfixture")
                with self.subTest(names=names), self.assertRaises(release.ReleaseError):
                    release.unpack_installer(archive, {"digest": "sha256:" + release.sha256(archive)}, root / "out.exe", ".exe")
            archive = root / "link.zip"
            info = zipfile.ZipInfo("installer.exe")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(info, "target")
            with self.assertRaises(release.ReleaseError):
                release.unpack_installer(archive, {"digest": "sha256:" + release.sha256(archive)}, root / "out.exe", ".exe")

    def test_public_or_foreign_draft_is_not_modified(self):
        marker = release.provenance("v0.1.3", RUN, SOURCE, HEAD)
        own = {"draft": True, "tag_name": "v0.1.3", "body": "Notes\n" + marker}
        release.verify_draft(own, "v0.1.3", marker)
        for update in ({"draft": False}, {"tag_name": "v0.1.4"}, {"body": "Other draft"}):
            with self.assertRaises(release.ReleaseError):
                release.verify_draft({**own, **update}, "v0.1.3", marker)

    def test_authenticated_listing_finds_drafts_and_paginates(self):
        earlier = [{"tag_name": f"v0.0.{index}", "id": index} for index in range(100)]
        draft = {"tag_name": "v0.1.3", "id": 101, "draft": True}
        with patch.object(release, "api", side_effect=[earlier, [draft]]) as api:
            self.assertEqual(release.find_release(REPO, "v0.1.3"), draft)
            self.assertIn("page=2", api.call_args.args[0])
            self.assertTrue(api.call_args.kwargs["collection"])
        with patch.object(release, "api", return_value=[]):
            self.assertEqual(release.find_release(REPO, "v0.1.3"), {})

    def test_new_draft_visibility_retries_then_checks_provenance(self):
        marker = release.provenance("v0.1.3", RUN, SOURCE, HEAD)
        draft = {"id": 42, "draft": True, "tag_name": "v0.1.3", "body": marker}
        clock = FakeClock()
        with patch.object(release.time, "monotonic", clock.monotonic), \
                patch.object(release.time, "sleep", clock.sleep), \
                patch.object(release, "find_release", side_effect=[{}, {}, draft]) as find, \
                patch.object(release, "command") as command:
            self.assertEqual(release.wait_for_created_draft(REPO, "v0.1.3", marker), draft)
        self.assertEqual(find.call_count, 3)
        self.assertEqual(clock.now, 4)
        self.assertEqual([call.kwargs["timeout"] for call in find.call_args_list], [20, 18, 16])
        command.assert_not_called()

    def test_new_draft_visibility_timeout_never_uploads_or_recreates(self):
        clock = FakeClock()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": REPO}), \
                patch.object(release, "verify_checkout", return_value=("0.1.3", HEAD)), \
                patch.object(release, "api", side_effect=[run_fixture(), jobs_fixture(), artifacts_fixture()]), \
                patch.object(release, "find_release", return_value={}), \
                patch.object(release.time, "monotonic", clock.monotonic), \
                patch.object(release.time, "sleep", clock.sleep), \
                patch.object(release, "download", side_effect=lambda path, target: target.write_bytes(b"fixture archive")), \
                patch.object(release, "unpack_installer", side_effect=lambda archive, artifact, target, suffix: target.write_bytes(b"fixture installer")), \
                patch.object(release, "command", return_value="") as command:
            with self.assertRaisesRegex(release.ReleaseError, "newly created draft release before the deadline"):
                release.publish("v0.1.3", RUN, SOURCE)
        self.assertEqual(clock.now, release.DRAFT_VISIBILITY_SECONDS)
        self.assertLessEqual(clock.now, 30)
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0][:4], ["gh", "release", "create", "v0.1.3"])
        self.assertIn("--draft", command.call_args.args[0])

    def test_existing_published_release_is_rejected_without_retry_or_mutation(self):
        marker = release.provenance("v0.1.3", RUN, SOURCE, HEAD)
        published = {"id": 42, "draft": False, "tag_name": "v0.1.3", "body": marker}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": REPO}), \
                patch.object(release, "verify_checkout", return_value=("0.1.3", HEAD)), \
                patch.object(release, "api", side_effect=[run_fixture(), jobs_fixture(), artifacts_fixture()]), \
                patch.object(release, "find_release", return_value=published) as find, \
                patch.object(release, "wait_for_created_draft") as wait, \
                patch.object(release, "download") as download, \
                patch.object(release, "command") as command:
            with self.assertRaisesRegex(release.ReleaseError, "Refusing to overwrite"):
                release.publish("v0.1.3", RUN, SOURCE)
        find.assert_called_once_with(REPO, "v0.1.3")
        wait.assert_not_called()
        download.assert_not_called()
        command.assert_not_called()

    def test_visibility_retry_stops_on_foreign_or_published_draft_and_api_errors(self):
        marker = release.provenance("v0.1.3", RUN, SOURCE, HEAD)
        own = {"id": 42, "draft": True, "tag_name": "v0.1.3", "body": marker}
        for result in ({**own, "draft": False}, {**own, "body": "foreign draft"},
                       release.ReleaseError("API failure")):
            with self.subTest(result=result), \
                    patch.object(release, "find_release", side_effect=[result]) as find, \
                    patch.object(release.time, "sleep") as sleep, \
                    patch.object(release, "command") as command:
                with self.assertRaises(release.ReleaseError):
                    release.wait_for_created_draft(REPO, "v0.1.3", marker)
                self.assertEqual(find.call_count, 1)
                sleep.assert_not_called()
                command.assert_not_called()

    def test_listing_pagination_uses_remaining_deadline_for_api_requests(self):
        clock = FakeClock()
        earlier = [{"tag_name": f"v0.0.{index}", "id": index} for index in range(100)]

        def api_page(path, **options):
            if path.endswith("&page=1"):
                clock.now += 19
                return earlier
            return []

        with patch.object(release.time, "monotonic", clock.monotonic), \
                patch.object(release, "api", side_effect=api_page) as api:
            self.assertEqual(release.find_release(REPO, "v0.1.3", timeout=20), {})
        self.assertEqual([call.kwargs["timeout"] for call in api.call_args_list], [20, 1])

    def test_api_timeout_is_reported_without_command_output(self):
        failure = subprocess.TimeoutExpired(["gh", "api"], 2, output="SECRET signed URL")
        with patch.object(release.subprocess, "run", side_effect=failure) as run:
            with self.assertRaisesRegex(release.ReleaseError, "timed out") as result:
                release.api("repos/example/repo", timeout=2)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertNotIn("SECRET", str(result.exception))

    def test_complete_uploaded_set_and_hashes_are_required_before_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "installer.exe"
            file.write_bytes(b"MZfixture")
            asset = {"name": file.name, "state": "uploaded", "size": file.stat().st_size, "digest": "sha256:" + release.sha256(file)}
            release.verify_assets({"assets": [asset]}, [file], REPO, root)
            for assets in ([], [asset, asset], [{**asset, "digest": "sha256:" + "f" * 64}], [{**asset, "state": "starter"}]):
                with self.assertRaises(release.ReleaseError):
                    release.verify_assets({"assets": assets}, [file], REPO, root)

    def test_subprocess_errors_do_not_expose_download_urls(self):
        response = subprocess.CompletedProcess([], 1, stdout="", stderr="Forbidden https://private.invalid/artifact?sig=SECRET")
        with patch.object(release.subprocess, "run", return_value=response):
            for callback in (lambda: release.command(["gh", "release", "upload"]), lambda: release.api("repos/example/repo")):
                with self.assertRaises(release.ReleaseError) as failure:
                    callback()
                self.assertNotIn("SECRET", str(failure.exception))
                self.assertNotIn("private.invalid", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
