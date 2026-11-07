#!/usr/bin/env python3
"""Promote verified CI installers to an existing release tag without rebuilding.

Run only by the manually dispatched release workflow. All subprocess failures
are replaced with fixed messages: gh download errors can include signed URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "macOS-arm64": ("macos-arm64.dmg", ".dmg"),
    "macOS-x64": ("macos-x64.dmg", ".dmg"),
    "Windows-x64": ("windows-x64-setup.exe", ".exe"),
}
JOBS = {"macOS Apple Silicon", "macOS Intel", "Windows x64", "Browser interface fixtures"}
MAX_FILE_BYTES = 1024 * 1024 * 1024
DRAFT_VISIBILITY_SECONDS = 20
DRAFT_RETRY_SECONDS = 2


class ReleaseError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def command(arguments: list[str]) -> str:
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
    require(result.returncode == 0, "Release command failed; no raw command output was logged.")
    return result.stdout.strip()


def api(path: str, *, collection: bool = False, timeout: float = 120) -> dict | list:
    try:
        result = subprocess.run(["gh", "api", path], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReleaseError("GitHub API request timed out; no command output was logged.") from None
    if result.returncode:
        raise ReleaseError("GitHub API request failed; no signed URL or credential was logged.")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise ReleaseError("GitHub returned invalid release metadata.") from None
    require(isinstance(value, list if collection else dict), "GitHub returned an unexpected metadata shape.")
    return value


def find_release(repo: str, tag: str, *, timeout: float | None = None) -> dict:
    # Authenticated release listing includes drafts; the tag lookup endpoint
    # is documented for published releases and need not expose a new draft.
    deadline = time.monotonic() + timeout if timeout is not None else None
    for page in range(1, 101):
        options = {"collection": True}
        if deadline is not None:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "GitHub did not return the newly created draft release before the deadline.")
            options["timeout"] = min(120, remaining)
        entries = api(f"repos/{repo}/releases?per_page=100&page={page}", **options)
        matches = [entry for entry in entries if entry.get("tag_name") == tag]
        require(len(matches) <= 1, "Multiple releases use the requested tag.")
        if matches:
            require(type(matches[0].get("id")) is int, "Release identity is invalid.")
            return matches[0]
        if len(entries) < 100:
            return {}
    raise ReleaseError("Release listing exceeded the supported pagination limit.")


def download(path: str, destination: Path, *, release_asset: bool = False) -> None:
    arguments = ["gh", "api", path]
    if release_asset:
        arguments += ["-H", "Accept: application/octet-stream"]
    with destination.open("wb") as output:
        result = subprocess.run(arguments, cwd=ROOT, stdout=output, stderr=subprocess.PIPE, timeout=600)
    require(result.returncode == 0, "Artifact download failed; no signed URL or credential was logged.")
    require(0 < destination.stat().st_size <= MAX_FILE_BYTES, "Downloaded artifact size is invalid.")


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def documentation_or_release_file(name: str) -> bool:
    # LICENSE and THIRD_PARTY_NOTICES.md are actual Tauri bundle resources,
    # so they must stay identical to the installer build despite being text.
    return name in {"README.md", "AGENTS.md", ".github/workflows/release.yml",
                    "scripts/publish-release.py", "scripts/tests/test_publish_release.py"} or name.startswith(("docs/", "history/"))


def verify_checkout(tag: str, source: str) -> tuple[str, str]:
    require(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag) is not None, "Release tag must be vMAJOR.MINOR.PATCH.")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "Expected source must be a full lowercase commit SHA.")
    head = command(["git", "rev-parse", "HEAD"])
    require(command(["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"] ) == head,
            "The checked-out commit must be exactly the existing release tag.")
    command(["git", "merge-base", "--is-ancestor", source, head])
    changes = command(["git", "diff", "--no-renames", "--name-only", "-z", source, head]).split("\0")
    require(all(not name or documentation_or_release_file(name) for name in changes),
            "Application or build files differ from the verified installer source.")
    version = tag.removeprefix("v")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    engine = re.search(r'__version__ = "([^"]+)"', (ROOT / "engine/easy_publish/__init__.py").read_text(encoding="utf-8"))
    require(engine is not None and all(value == version for value in (
        package["version"], lock["version"], lock["packages"][""]["version"], tauri["version"], cargo["package"]["version"], engine[1]
    )), "Version files do not match the release tag.")
    require((ROOT / f"docs/releases/{tag}.md").is_file(), "Release notes for this tag are missing.")
    return version, head


def verify_run(run: dict, jobs: dict, repo: str, source: str, run_id: int) -> None:
    require(run.get("id") == run_id and run.get("status") == "completed" and run.get("conclusion") == "success",
            "The installer build must have completed successfully.")
    require(run.get("head_sha") == source and run.get("head_repository", {}).get("full_name") == repo,
            "Build source or source repository does not match.")
    require(run.get("path") == ".github/workflows/build.yml" and run.get("event") in {"push", "pull_request", "workflow_dispatch"},
            "Installers must come from this repository's desktop build workflow.")
    entries = jobs.get("jobs", [])
    require(jobs.get("total_count") == len(entries) and {job.get("name") for job in entries} == JOBS
            and all(job.get("conclusion") == "success" for job in entries),
            "Every desktop and interface build job must have passed.")


def select_artifacts(metadata: dict, version: str, source: str, run_id: int) -> dict[str, dict]:
    entries = metadata.get("artifacts", [])
    require(metadata.get("total_count") == len(entries), "Artifact metadata is incomplete.")
    selected = {}
    for platform in PLATFORMS:
        name = f"easy-publish-{version}-{platform}"
        matches = [item for item in entries if item.get("name") == name]
        require(len(matches) == 1, "Expected exactly one artifact for each installer platform.")
        artifact = matches[0]
        workflow = artifact.get("workflow_run", {})
        require(not artifact.get("expired", True) and workflow.get("head_sha") == source and workflow.get("id") == run_id,
                "Installer artifact is expired or belongs to another source build.")
        digest = artifact.get("digest")
        require(isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
                "Installer artifact has no verifiable SHA-256 digest.")
        require(type(artifact.get("id")) is int and 0 < artifact.get("size_in_bytes", 0) <= MAX_FILE_BYTES,
                "Installer artifact metadata is invalid.")
        selected[platform] = artifact
    return selected


def unpack_installer(archive: Path, artifact: dict, target: Path, suffix: str) -> None:
    require(sha256(archive) == artifact["digest"].removeprefix("sha256:"), "Downloaded artifact SHA-256 mismatch.")
    with zipfile.ZipFile(archive) as bundle:
        files = [entry for entry in bundle.infolist() if not entry.is_dir()]
        require(len(files) == 1, "Installer archive must contain exactly one file.")
        entry = files[0]
        path = PurePosixPath(entry.filename)
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in entry.filename
                and path.suffix.lower() == suffix and not stat.S_ISLNK(entry.external_attr >> 16)
                and 0 < entry.file_size <= MAX_FILE_BYTES, "Installer archive entry is invalid.")
        with bundle.open(entry) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        require(target.stat().st_size == entry.file_size, "Installer extraction was incomplete.")
    with target.open("rb") as source:
        if suffix == ".dmg":
            require(target.stat().st_size >= 512, "DMG installer is too short.")
            source.seek(-512, os.SEEK_END)
            require(source.read(4) == b"koly", "Installer has no DMG trailer.")
        else:
            require(source.read(2) == b"MZ", "Installer has no Windows executable header.")


def provenance(tag: str, run_id: int, source: str, head: str) -> str:
    return f"<!-- easy-publish-release tag={tag} build={run_id} source={source} release={head} -->"


def verify_draft(release: dict, tag: str, marker: str) -> None:
    require(release.get("draft") is True and release.get("tag_name") == tag and marker in release.get("body", ""),
            "Refusing to overwrite a published release or a draft from another publication.")


def wait_for_created_draft(repo: str, tag: str, marker: str) -> dict:
    """Wait only after our create succeeded; never retry a mutation or an API error."""
    deadline = time.monotonic() + DRAFT_VISIBILITY_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "GitHub did not return the newly created draft release before the deadline.")
        release = find_release(repo, tag, timeout=remaining)
        if release:
            verify_draft(release, tag, marker)
            return release
        remaining = deadline - time.monotonic()
        require(remaining > 0, "GitHub did not return the newly created draft release before the deadline.")
        time.sleep(min(DRAFT_RETRY_SECONDS, remaining))


def verify_assets(release: dict, files: list[Path], repo: str, scratch: Path) -> None:
    assets = release.get("assets", [])
    expected = {file.name: file for file in files}
    require(len(assets) == len(expected) and {asset.get("name") for asset in assets} == set(expected),
            "Draft release does not contain exactly the expected installers and checksum file.")
    for asset in assets:
        file = expected[asset["name"]]
        require(asset.get("state") == "uploaded" and asset.get("size") == file.stat().st_size,
                "Draft release upload is incomplete.")
        digest = asset.get("digest")
        if digest:
            require(digest == "sha256:" + sha256(file), "Uploaded release asset SHA-256 mismatch.")
        else:
            # Older GitHub responses may not expose an asset digest immediately.
            copied = scratch / "uploaded-asset-check"
            download(f"repos/{repo}/releases/assets/{asset['id']}", copied, release_asset=True)
            require(sha256(copied) == sha256(file), "Uploaded release asset SHA-256 mismatch.")
            copied.unlink()


def publish(tag: str, run_id: int, source: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None, "GITHUB_REPOSITORY is required.")
    require(run_id > 0, "Build run ID must be positive.")
    version, head = verify_checkout(tag, source)
    root = f"repos/{repo}"
    run = api(f"{root}/actions/runs/{run_id}")
    verify_run(run, api(f"{root}/actions/runs/{run_id}/jobs?per_page=100"), repo, source, run_id)
    artifacts = select_artifacts(api(f"{root}/actions/runs/{run_id}/artifacts?per_page=100"), version, source, run_id)
    marker = provenance(tag, run_id, source, head)
    release = find_release(repo, tag)
    if release:
        verify_draft(release, tag, marker)
    with tempfile.TemporaryDirectory(prefix="easy-publish-release-") as directory:
        scratch = Path(directory)
        files = []
        for platform, artifact in artifacts.items():
            name, suffix = PLATFORMS[platform]
            archive = scratch / "artifact.zip"
            download(f"{root}/actions/artifacts/{artifact['id']}/zip", archive)
            installer = scratch / f"Easy-Publish_{version}_{name}"
            unpack_installer(archive, artifact, installer, suffix)
            archive.unlink()
            files.append(installer)
            print(f"Verified {platform} installer.", flush=True)
        checksums = scratch / "SHA256SUMS.txt"
        checksums.write_text("".join(f"{sha256(file)}  {file.name}\n" for file in files), encoding="utf-8")
        files.append(checksums)
        notes = scratch / "release-notes.md"
        notes.write_text((ROOT / f"docs/releases/{tag}.md").read_text(encoding="utf-8").rstrip() + "\n\n" + marker + "\n", encoding="utf-8")
        if not release:
            command(["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--target", head,
                     "--draft", "--title", f"Easy Publish {version}", "--notes-file", str(notes)])
            release = wait_for_created_draft(repo, tag, marker)
        release_path = f"{root}/releases/{release['id']}"
        # Recheck the draft before mutation, including when resuming our own draft.
        release = api(release_path)
        verify_draft(release, tag, marker)
        require({asset.get("name") for asset in release.get("assets", [])} <= {file.name for file in files},
                "Draft release contains unexpected assets; refusing to replace them.")
        command(["gh", "release", "upload", tag, "--repo", repo, "--clobber", *(str(file) for file in files)])
        release = api(release_path)
        verify_draft(release, tag, marker)
        verify_assets(release, files, repo, scratch)
        command(["gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest", "--notes-file", str(notes)])
        released = api(release_path)
        require(released.get("draft") is False and released.get("prerelease") is False and marker in released.get("body", ""),
                "GitHub did not confirm publication of the complete release.")
        verify_assets(released, files, repo, scratch)
    return f"https://github.com/{repo}/releases/tag/{tag}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--build-run-id", type=int, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    arguments = parser.parse_args()
    try:
        url = publish(arguments.tag, arguments.build_run_id, arguments.expected_source_sha)
    except Exception as error:
        message = str(error) if isinstance(error, ReleaseError) else type(error).__name__
        parser.exit(1, "Release publication stopped: " + message + "\n")
    print("Published verified installers: " + url)


if __name__ == "__main__":
    main()
