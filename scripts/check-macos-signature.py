#!/usr/bin/env python3
"""Require valid ad-hoc signatures without hardened-runtime library validation."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess


def verify_signature(path: Path) -> None:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(path)], check=True, capture_output=True, text=True)
    result = subprocess.run(["codesign", "--display", "--verbose=4", str(path)], check=True, capture_output=True, text=True)
    details = result.stdout + result.stderr
    flags = re.search(r"\bflags=0x([0-9a-fA-F]+)", details)
    if not flags or "Signature=adhoc" not in details:
        raise RuntimeError(f"Expected an ad-hoc signature: {path.name}")
    if int(flags.group(1), 16) & 0x10000:  # CS_RUNTIME enables hardened runtime.
        raise RuntimeError(f"Ad-hoc binary has hardened runtime enabled: {path.name}")
    print(f"Ad-hoc signature integrity passed without hardened runtime: {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.paths:
        verify_signature(path)


if __name__ == "__main__":
    main()
