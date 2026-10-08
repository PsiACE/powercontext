"""Reproduce source-selection ablations without touching user tool environments."""

from __future__ import annotations

# Assertions and subprocesses are the experiment oracle; commands come from this harness.
# ruff: noqa: S101, S603, S607
import argparse
import hashlib
import http.server
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parents[3]
BASELINE = "8b2ea9576a92154cd00a2a0609afdb302010cddf"


@lru_cache
def wheel(version: str) -> bytes:
    output = io.BytesIO()
    metadata = f"powercontext-{version}.dist-info"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "powercontext_fixture.py",
            "from importlib.metadata import version\ndef main():\n    print(version('powercontext'))\n",
        )
        archive.writestr(f"{metadata}/METADATA", f"Metadata-Version: 2.1\nName: powercontext\nVersion: {version}\n")
        archive.writestr(
            f"{metadata}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: source-experiment\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{metadata}/entry_points.txt", "[console_scripts]\npowercontext = powercontext_fixture:main\n"
        )
        archive.writestr(f"{metadata}/RECORD", "")
    return output.getvalue()


class Index(http.server.BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []
    versions: ClassVar[dict[str, list[str]]] = {
        "old": ["1.0.0"],
        "new": ["1.0.0", "1.1.0", "1.2.0rc1"],
        "encoded": ["1.0.0"],
    }

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.requests.append(self.path)
        request_path = urllib.parse.urlsplit(self.path).path
        parts = request_path.strip("/").split("/")
        source = parts[0]
        if source in {"forbidden", "missing", "broken"}:
            self.send_error({"forbidden": 403, "missing": 404, "broken": 503}[source])
            return
        if parts[-1].endswith(".whl"):
            filename = urllib.parse.unquote(parts[-1])
            payload = wheel(filename.split("-")[1])
            content_type = "application/octet-stream"
        elif parts[-1] == "powercontext" and source in self.versions:
            links = []
            for version in self.versions[source]:
                filename = f"powercontext-{version}-py3-none-any.whl"
                href = f"/{source}/files/{filename}"
                if source == "encoded":
                    href = href.replace("1.0.0", "1%2E0%2E0")
                digest = hashlib.sha256(wheel(version)).hexdigest()
                # Opaque anchor text is valid Simple API; filename comes from URL.
                links.append(f'<a href="{href}#sha256={digest}">download</a>')
            payload = ("<!doctype html>" + "\n".join(links)).encode()
            content_type = "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default=shutil.which("uv"))
    parser.add_argument("--pwsh", default=shutil.which("pwsh"))
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.uv:
        parser.error("uv is required")
    args.scratch.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="source-policy-", dir=args.scratch) as temporary:
        root = Path(temporary)
        bash_source = subprocess.check_output(
            ["git", "show", f"{BASELINE}:website/public/install.sh"], cwd=REPO, text=True
        )
        bash_helpers = root / "helpers.sh"
        bash_helpers.write_text(bash_source.rsplit('main "$@"', 1)[0])
        ps_source = subprocess.check_output(
            ["git", "show", f"{BASELINE}:website/public/install.ps1"], cwd=REPO, text=True
        )
        ps_helpers = root / "helpers.ps1"
        ps_helpers.write_text(ps_source.split("$SavedEnvironment = @{}", 1)[0])
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Index)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        # Deliberately enumerate inherited settings; do not leak credentials or host config.
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(root / "home"),
            "LANG": "C.UTF-8",
            "TMPDIR": str(root),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CONFIG_DIRS": str(root / "system-config"),
            "UV_CACHE_DIR": str(root / "cache"),
            "UV_TOOL_DIR": str(root / "tools"),
            "UV_TOOL_BIN_DIR": str(root / "bin"),
            "UV_PYTHON_INSTALL_DIR": str(root / "python"),
            "UV_HTTP_RETRIES": "0",
            "UV_HTTP_TIMEOUT": "2",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        Path(environment["HOME"]).mkdir()

        def run(name, command, expected=0, extra=None):
            before = len(Index.requests)
            started = time.monotonic()
            completed = subprocess.run(
                command, env=environment | (extra or {}), cwd=root, capture_output=True, text=True, timeout=30
            )
            row = {
                "name": name,
                "exit": completed.returncode,
                "expected_exit": expected,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "requests": Index.requests[before:],
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            for stream in ("stdout", "stderr"):
                row[stream] = row[stream].replace(str(root), "<scratch>").replace(base, "<index>")
            results.append(row)
            assert completed.returncode == expected, row
            return completed

        def bash(name, code, expected=0, extra=None):
            return run(name, ["bash", "-c", f'source "{bash_helpers}"; TEMP_DIR="{root}"; {code}'], expected, extra)

        def install(name, source="new", version=None, flags=(), expected=0, extra=None):
            requirement = "powercontext" if version is None else f"powercontext=={version}"
            resolution_flags = () if extra and extra.get("UV_OFFLINE") else ("--reinstall", "--refresh")
            command = [
                args.uv,
                "tool",
                "install",
                "--python",
                sys.executable,
                "--no-python-downloads",
                "--default-index",
                f"{base}/{source}/simple",
                "--upgrade",
                *resolution_flags,
                *flags,
                requirement,
            ]
            return run(name, command, expected, extra)

        def installed(name):
            return run(name, [str(root / "bin" / "powercontext")])

        try:
            bash("encoded_filename_shell_probe", f'VERSION=1.0.0; index_has_version "{base}/encoded/simple"', 1)
            install("encoded_filename_uv_resolution", "encoded", "1.0.0")
            assert installed("encoded_installed_executable").stdout.strip() == "1.0.0"
            install("latest_stable", flags=("--prerelease", "disallow"))
            latest = installed("latest_installed_executable")
            assert latest.stdout.strip() == "1.1.0", results[-3:]
            install("exact_stable", version="1.0.0")
            assert installed("exact_installed_executable").stdout.strip() == "1.0.0"
            install("exact_prerelease", version="1.2.0rc1")
            assert installed("prerelease_installed_executable").stdout.strip() == "1.2.0rc1"
            install("unavailable_exact", version="9.9.9", expected=1)
            assert installed("failure_preserves_runtime").stdout.strip() == "1.2.0rc1"
            install("explicit_forbidden_no_fallback", "forbidden", "1.0.0", expected=1)
            assert not any(path.startswith("/new/") for path in results[-1]["requests"])
            install("additional_index_has_priority", version="1.0.0", flags=("--index", f"{base}/old/simple"))
            install("first_index_conflict", version="1.1.0", flags=("--index", f"{base}/old/simple"), expected=1)
            install(
                "environment_additional_index", version="1.1.0", expected=1, extra={"UV_INDEX": f"{base}/old/simple"}
            )
            install(
                "cli_default_overrides_environment",
                "new",
                "1.1.0",
                extra={"UV_DEFAULT_INDEX": f"{base}/forbidden/simple"},
            )
            install("missing_additional_index_continues", version="1.1.0", flags=("--index", f"{base}/missing/simple"))
            install(
                "forbidden_additional_index_stops",
                version="1.1.0",
                flags=("--index", f"{base}/forbidden/simple"),
                expected=1,
            )
            run(
                "exact_runtime_reuse_without_network",
                [
                    args.uv,
                    "tool",
                    "install",
                    "--python",
                    sys.executable,
                    "--no-python-downloads",
                    "--default-index",
                    f"{base}/forbidden/simple",
                    "powercontext==1.1.0",
                ],
            )
            assert results[-1]["requests"] == []
            run(
                "http_proxy_uv_resolution",
                [
                    args.uv,
                    "tool",
                    "install",
                    "--python",
                    sys.executable,
                    "--no-python-downloads",
                    "--upgrade",
                    "--reinstall",
                    "--default-index",
                    "http://fixture.invalid/new/simple",
                    "powercontext==1.1.0",
                ],
                extra={"HTTP_PROXY": base, "NO_PROXY": ""},
            )
            assert any(path.startswith("http://fixture.invalid/") for path in results[-1]["requests"])
            install("offline_cached_exact", version="1.1.0", extra={"UV_OFFLINE": "1"})
            assert results[-1]["requests"] == []
            install("offline_uncached_exact", "encoded", "9.9.9", expected=1, extra={"UV_OFFLINE": "1"})
            assert results[-1]["requests"] == []
            for name, timezone, locale in [
                ("cn_timezone", "Asia/Shanghai", "en_US.UTF-8"),
                ("cn_locale_utc", "UTC", "zh_CN.UTF-8"),
                ("cn_locale_global_timezone", "Asia/Singapore", "zh_CN.UTF-8"),
                ("no_location_hints", "UTC", "C.UTF-8"),
            ]:
                bash(name, "REGION=auto; detect_region", extra={"TZ": timezone, "LANG": locale})
                bash(name + "_ablation", "REGION=global; detect_region", extra={"TZ": timezone, "LANG": locale})
            config = Path(environment["XDG_CONFIG_HOME"]) / "uv" / "uv.toml"
            config.parent.mkdir(parents=True)
            config.write_text("compile-bytecode = false\n")
            original = config.read_bytes()
            bash(
                "unrelated_config_suppresses_package_mirror",
                "REGION=cn; check_index; printf 'INDEX=%s\\n' \"$INDEX_URL\"",
            )
            install("unrelated_config_uv_still_resolves", version="1.0.0")
            assert config.read_bytes() == original
            config.write_text(f'index-url = "{base}/old/simple"\n')
            run(
                "user_config_index",
                [
                    args.uv,
                    "tool",
                    "install",
                    "--python",
                    sys.executable,
                    "--no-python-downloads",
                    "--upgrade",
                    "powercontext==1.1.0",
                ],
                expected=1,
            )
            (root / "uv.toml").write_text(f'index-url = "{base}/new/simple"\n')
            run(
                "tool_ignores_project_config",
                [
                    args.uv,
                    "tool",
                    "install",
                    "--python",
                    sys.executable,
                    "--no-python-downloads",
                    "--upgrade",
                    "powercontext==1.1.0",
                ],
                expected=1,
            )
            # Two controlled uv-native attempts demonstrate an explicit fallback boundary.
            install("automatic_candidate_uv_failure", "broken", "1.0.0", expected=2)
            install("automatic_candidate_fallback_uv_success", "new", "1.0.0")
            installed("automatic_fallback_installed_executable")
            run(
                "reuse_system_python",
                [args.uv, "python", "find", "--system", "--no-project", "--no-python-downloads", ">=3.11,<4"],
            )
            run(
                "python_specific_mirror_url",
                [args.uv, "python", "list", "cpython@3.12", "--only-downloads", "--show-urls"],
                extra={"UV_PYTHON_INSTALL_MIRROR": f"{base}/python-specific", "UV_ASTRAL_MIRROR_URL": f"{base}/astral"},
            )
            run(
                "python_explicit_mirror_failure",
                [args.uv, "python", "install", "3.12"],
                expected=1,
                extra={"UV_PYTHON_INSTALL_MIRROR": f"{base}/broken", "UV_ASTRAL_MIRROR_URL": f"{base}/astral"},
            )
            assert results[-1]["requests"] and all(path.startswith("/broken/") for path in results[-1]["requests"])
            run(
                "python_offline_missing",
                [args.uv, "python", "install", "3.12"],
                expected=1,
                extra={"UV_OFFLINE": "1", "UV_PYTHON_INSTALL_MIRROR": f"{base}/broken"},
            )
            assert results[-1]["requests"] == []
            config.write_text("compile-bytecode = false\n")
            bash("explicit_cn_preserves_unrelated_config", "REGION=cn; detect_region; check_index")
            bash(
                "auto_cn_preserves_unrelated_config",
                "REGION=auto; detect_region; check_index",
                extra={"TZ": "Asia/Shanghai"},
            )
            if args.pwsh:
                run(
                    "powershell_encoded_filename_probe",
                    [
                        args.pwsh,
                        "-NoProfile",
                        "-Command",
                        f". '{ps_helpers}'; $TempDir='{root}'; $Version='1.0.0'; "
                        f"if (Test-IndexVersion '{base}/encoded/simple') {{ exit 0 }} else {{ exit 1 }}",
                    ],
                    expected=1,
                )
                run(
                    "powershell_region_cn",
                    [args.pwsh, "-NoProfile", "-Command", f". '{ps_helpers}'; $Region='auto'; Select-Region"],
                    extra={"TZ": "Asia/Shanghai"},
                )
                run(
                    "powershell_parse",
                    [
                        args.pwsh,
                        "-NoProfile",
                        "-Command",
                        f"$Errors=$null; [Management.Automation.Language.Parser]::ParseFile('{REPO / 'website/public/install.ps1'}', "
                        "[ref]$null, [ref]$Errors) | Out-Null; if ($Errors) { $Errors; exit 1 }",
                    ],
                )
        finally:
            server.shutdown()
            server.server_close()
    report = {
        "baseline": BASELINE,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "uv": subprocess.check_output([args.uv, "--version"], text=True).strip(),
        "qualification": "Real uv and installed fixture CLI on Linux; loopback network fixtures; direct shell helpers; PowerShell 7 on Linux only.",
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(results)} measured cases written to {args.output}")


if __name__ == "__main__":
    main()
