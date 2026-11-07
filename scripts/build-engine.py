#!/usr/bin/env python3
"""Freeze the native publishing engine; run once on each destination platform."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def check_bundled_driver(binary: Path, directory: Path) -> None:
    """Run the driver shipped inside the archive, without installing a browser."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(binary))
    # Fail packaging if a future dependency accidentally reintroduces system
    # credential-store access, including inside the embedded Python archive.
    for name, entry in archive.toc.items():
        if name == "keyring" or name.startswith(("keyring/", "keyring.", "keyring-")):
            raise RuntimeError("System keyring must not be bundled")
        if entry[-1] == "z":
            modules = archive.open_embedded_archive(name).toc
            if any(module == "keyring" or module.startswith("keyring.") for module in modules):
                raise RuntimeError("System keyring must not be bundled")
    for name, entry in archive.toc.items():
        normalized = name.replace("\\", "/")
        if not normalized.startswith("patchright/driver/"):
            continue
        relative = PurePosixPath(normalized)
        if ".." in relative.parts:
            raise RuntimeError("Invalid browser driver archive path")
        destination = directory.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.extract(name))
        if entry[-1] == "b":
            destination.chmod(0o755)
    driver = directory / "patchright/driver"
    node = driver / ("node.exe" if os.name == "nt" else "node")
    result = subprocess.run(
        [str(node), str(driver / "package/cli.js"), "--version"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    if not result.stdout.strip().startswith("Version "):
        raise RuntimeError("Bundled browser driver returned no version")
    print("Bundled browser driver passed its offline startup check.")


def native_target() -> str:
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine)
    if architecture is None:
        raise RuntimeError(f"Unsupported build architecture: {machine}")
    system = {"Darwin": "apple-darwin", "Windows": "pc-windows-msvc", "Linux": "unknown-linux-gnu"}.get(platform.system())
    if system is None:
        raise RuntimeError("Unsupported build operating system")
    return f"{architecture}-{system}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", help="Rust target triple; must match this Python interpreter's native platform")
    args = parser.parse_args()
    target = args.target or native_target()
    if target != native_target():
        parser.error("PyInstaller does not cross-compile. Run with native Python on the target platform.")
    if sys.version_info[:2] != (3, 12):
        parser.error("The embedded engine must be built with Python 3.12.")
    entry_module = ROOT / "engine/easy_publish/__main__.py"
    if not entry_module.is_file():
        parser.error("The engine entry point is missing.")
    wenyan = ROOT / "runtime/wenyan/dist"
    if not (wenyan / "render.cjs").is_file():
        parser.error("The bundled Wenyan renderer is missing. Run npm run prepare:wenyan first.")
    suffix = ".exe" if os.name == "nt" else ""
    destination = ROOT / "src-tauri/binaries" / f"easy-publish-engine-{target}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Keep compiler scratch files and the generated bootstrap outside the repository.
    with tempfile.TemporaryDirectory(prefix="easy-publish-freeze-") as scratch:
        scratch_path = Path(scratch)
        bootstrap = scratch_path / "engine_entry.py"
        bootstrap.write_text(
            "from multiprocessing import freeze_support\n"
            "from easy_publish.__main__ import main\n"
            "if __name__ == '__main__':\n"
            "    freeze_support()\n"
            "    raise SystemExit(main())\n",
            encoding="utf-8",
        )
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--name", "easy-publish-engine", "--paths", str(ROOT / "engine"),
            "--distpath", str(scratch_path / "dist"),
            "--workpath", str(scratch_path / "work"), "--specpath", str(scratch_path),
            # Includes Patchright's platform-specific Node driver and package data.
            "--collect-all", "patchright", "--collect-all", "easy_publish",
            "--add-data", f"{wenyan}{os.pathsep}easy_publish/vendor/wenyan",
            # Account keys live only in the application's private data directory.
            "--exclude-module", "keyring",
            # Uvicorn resolves protocol and event-loop implementations by module name.
            "--collect-submodules", "uvicorn",
        ]
        if platform.system() == "Darwin":
            # PyInstaller's default signs ad-hoc. Passing an explicit "-" identity
            # also enables hardened runtime, which rejects the bundled Python
            # framework because ad-hoc binaries have no matching Team ID.
            command += ["--target-architecture", "arm64" if target.startswith("aarch64") else "x86_64"]
        command.append(str(bootstrap))
        environment = os.environ.copy()
        # Collection helpers execute before Analysis applies --paths. Make the source
        # package importable then, including its dynamically loaded provider modules.
        environment["PYTHONPATH"] = str(ROOT / "engine")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        binary = scratch_path / "dist" / f"easy-publish-engine{suffix}"
        check_bundled_driver(binary, scratch_path / "driver-check")
        shutil.copy2(binary, destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | 0o111)
    if platform.system() == "Darwin":
        subprocess.run([sys.executable, str(ROOT / "scripts/check-macos-signature.py"), str(destination)], check=True)
    print(f"Embedded engine built: {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
