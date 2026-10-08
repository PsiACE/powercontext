# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run real uv package-resolution and recovery ablations using local synthetic wheels."""

# Assertions are experiment acceptance checks; subprocess inputs are controlled fixture paths.
# ruff: noqa: S101, S603

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def wheel(directory: Path, version: str, *, broken: bool = False, reported: str | None = None) -> Path:
    name = f"powercontext-{version}-py3-none-any.whl"
    info = f"powercontext-{version}.dist-info"
    source = (
        "raise RuntimeError('synthetic startup failure')\n"
        if broken
        else f"def main():\n    print('{reported or version}')\n"
    )
    files = {
        "powercontext/__init__.py": source,
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: powercontext\nVersion: {version}\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: experiment\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/entry_points.txt": "[console_scripts]\npowercontext = powercontext:main\n",
    }
    records = []
    for path, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content.encode()).digest()).decode().rstrip("=")
        records.append(f"{path},sha256={digest},{len(content.encode())}")
    files[f"{info}/RECORD"] = "\n".join([*records, f"{info}/RECORD,,"]) + "\n"
    destination = directory / name
    with zipfile.ZipFile(destination, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must already be installed")
    observations = {"uv": subprocess.check_output([uv, "--version"], text=True).strip(), "cases": []}
    with tempfile.TemporaryDirectory(dir=args.scratch) as temp:
        root = Path(temp)
        packages = root / "packages"
        packages.mkdir()
        wheel(packages, "1.0.0")
        wheel(packages, "2.0.0")
        wheel(packages, "3.0.0rc1")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("UV_", "PIP_", "PYTHON")) and key != "VIRTUAL_ENV"
        }
        environment.update(
            HOME=str(root),
            USERPROFILE=str(root),
            PYTHONIOENCODING="utf-8",
            UV_TOOL_DIR=str(root / "tools"),
            UV_TOOL_BIN_DIR=str(root / "bin"),
            UV_CACHE_DIR=str(root / "cache"),
            UV_NO_CONFIG="1",
        )

        def install(requirement: str, *flags: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    uv,
                    "tool",
                    "install",
                    "--python",
                    sys.executable,
                    "--no-python-downloads",
                    "--no-index",
                    "--find-links",
                    str(packages),
                    *flags,
                    requirement,
                ],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        def executable() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(root / "bin" / "powercontext")],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        for label, flags, expected in [
            ("latest_without_stable_policy", (), "2.0.0"),
            ("latest_with_stable_policy", ("--prerelease", "disallow"), "2.0.0"),
        ]:
            result = install("powercontext", "--upgrade", *flags)
            visible = executable()
            observations["cases"].append({
                "case": label,
                "install_status": result.returncode,
                "version": visible.stdout.strip(),
            })
            # uv defaults normally exclude prereleases when stable releases exist.
            if label == "latest_with_stable_policy":
                assert result.returncode == 0 and visible.stdout.strip() == expected
        stable_wheels = list(packages.glob("powercontext-[12].0.0-*.whl"))
        for path in stable_wheels:
            path.rename(path.with_suffix(".hidden"))
        result = install("powercontext", "--upgrade", "--reinstall")
        assert result.returncode == 0 and executable().stdout.strip() == "3.0.0rc1"
        observations["cases"].append({
            "case": "prerelease_only_without_stable_policy",
            "install_status": 0,
            "version": "3.0.0rc1",
        })
        result = install("powercontext", "--upgrade", "--reinstall", "--prerelease", "disallow")
        assert result.returncode != 0
        observations["cases"].append({"case": "prerelease_only_with_stable_policy", "install_status": 1})
        for path in stable_wheels:
            path.with_suffix(".hidden").rename(path)
        result = install("powercontext==1.0.0")
        assert result.returncode == 0 and executable().stdout.strip() == "1.0.0"
        receipt_before = (root / "tools" / "powercontext" / "uv-receipt.toml").read_bytes()
        result = install("powercontext==99.0.0")
        assert result.returncode != 0 and executable().stdout.strip() == "1.0.0"
        assert receipt_before == (root / "tools" / "powercontext" / "uv-receipt.toml").read_bytes()
        observations["cases"].append({
            "case": "unavailable_exact_without_custom_rollback",
            "install_status": result.returncode,
            "version": "1.0.0",
            "uv_receipt_preserved": True,
        })
        trusted = packages / "powercontext-1.0.0-py3-none-any.whl"
        digest = hashlib.sha256(trusted.read_bytes()).hexdigest()
        constraint = root / "constraints.txt"
        constraint.write_text(f"powercontext @ {trusted.as_uri()}#sha256={digest}\n")
        wheel(packages, "1.0.0", broken=True)
        result = install("powercontext==1.0.0", "--reinstall", "--refresh", "--constraints", str(constraint))
        assert result.returncode == 0 and executable().returncode != 0
        observations["cases"].append({
            "case": "local_file_hash_fragment_is_not_enforced",
            "install_status": 0,
            "executable_status": 1,
            "hash_mismatch_reported": "mismatch" in result.stderr.lower(),
        })
        wheel(packages, "1.0.0")
        wheel(packages, "4.0.0", broken=True)
        result = install("powercontext==4.0.0")
        visible = executable()
        assert result.returncode == 0 and visible.returncode != 0
        observations["cases"].append({
            "case": "package_identity_without_executable_check",
            "install_status": result.returncode,
            "executable_status": visible.returncode,
            "diagnostic": visible.stderr.splitlines()[-1],
            "previous_executable_replaced": True,
        })
        wheel(packages, "5.0.0", reported="1.0.0")
        result = install("powercontext==5.0.0")
        visible = executable()
        assert result.returncode == 0 and visible.stdout.strip() == "1.0.0"
        observations["cases"].append({
            "case": "distribution_identity_without_reported_version_comparison",
            "distribution_version": "5.0.0",
            "reported_version": "1.0.0",
            "install_status": 0,
            "exact_version_check_rejects": True,
        })
        result = install("powercontext==1.0.0", "--refresh")
        assert result.returncode == 0 and executable().stdout.strip() == "1.0.0"
        observations["cases"].append({
            "case": "explicit_retry_without_custom_receipt",
            "install_status": 0,
            "version": "1.0.0",
        })
    args.output.write_text(json.dumps(observations, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
