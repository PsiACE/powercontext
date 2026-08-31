# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
INSTALL_SH = REPOSITORY_ROOT / "install.sh"
BASH = shutil.which("bash") or "bash"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_unix_installer_delivers_runtime_and_selected_hosts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    host_state = tmp_path / "host-state"
    home.mkdir()
    bin_dir.mkdir()
    host_state.mkdir()

    write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "venv" ]]; then
    venv=${@: -1}
    mkdir -p "$venv/bin"
    ln -sf "$POWERCONTEXT_TEST_PYTHON" "$venv/bin/python"
    cat >"$venv/bin/powercontext" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == "--version" ]] && printf '0.1.0\n'
EOF
    chmod +x "$venv/bin/powercontext"
fi
""",
    )
    write_executable(
        bin_dir / "codex",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "plugin add" ]]; then
    touch "$POWERCONTEXT_TEST_STATE/codex"
    printf '{"name":"powercontext","version":"0.2.0"}\n'
elif [[ "$1 $2" == "plugin list" ]]; then
    if [[ -f "$POWERCONTEXT_TEST_STATE/codex" ]]; then
        printf '{"installed":[{"name":"powercontext","installed":true,"enabled":true}]}\n'
    else
        printf '{"installed":[]}\n'
    fi
else
    printf '{"marketplaceName":"powercontext"}\n'
fi
""",
    )
    write_executable(
        bin_dir / "claude",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2 $3" == "plugin marketplace list" ]]; then
    [[ -f "$POWERCONTEXT_TEST_STATE/claude-marketplace" ]] && printf '[{"name":"powercontext"}]\n' || printf '[]\n'
elif [[ "$1 $2 $3" == "plugin marketplace add" ]]; then
    touch "$POWERCONTEXT_TEST_STATE/claude-marketplace"
elif [[ "$1 $2" == "plugin install" ]]; then
    touch "$POWERCONTEXT_TEST_STATE/claude-plugin"
elif [[ "$1 $2" == "plugin list" ]]; then
    if [[ -f "$POWERCONTEXT_TEST_STATE/claude-plugin" ]]; then
        printf '[{"id":"powercontext@powercontext","enabled":true}]\n'
    else
        printf '[]\n'
    fi
fi
""",
    )

    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "POWERCONTEXT_INSTALL_PACKAGE": "powercontext[cli,server]",
        "POWERCONTEXT_TEST_PYTHON": sys.executable,
        "POWERCONTEXT_TEST_STATE": str(host_state),
    }
    command = [
        BASH,
        str(INSTALL_SH),
        "--profile",
        "local",
        "--host",
        "codex",
        "--host",
        "claude-code",
        "--yes",
    ]

    first = subprocess.run(command, check=False, capture_output=True, env=environment, text=True)
    second = subprocess.run(command, check=False, capture_output=True, env=environment, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "PowerContext installation complete." in first.stdout
    executable = home / ".local" / "bin" / "powercontext"
    assert executable.is_symlink()
    assert subprocess.check_output([executable, "--version"], text=True).strip() == "0.1.0"
    assert (host_state / "codex").is_file()
    assert (host_state / "claude-plugin").is_file()


def test_unix_installer_rejects_missing_host_before_runtime_mutation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    write_executable(bin_dir / "uv", "#!/usr/bin/env bash\nexit 99\n")
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }

    result = subprocess.run(
        [BASH, str(INSTALL_SH), "--profile", "local", "--host", "codex", "--yes"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "Codex CLI is not installed" in result.stderr
    assert not (home / ".local" / "share" / "powercontext" / "distribution").exists()
