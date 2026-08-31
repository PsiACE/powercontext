#!/usr/bin/env bash
#
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

set -euo pipefail

readonly UV_INSTALL_URL="https://astral.sh/uv/install.sh"
readonly MARKETPLACE_SOURCE="oceanbase/powercontext"

PROFILE=""
REF="${POWERCONTEXT_INSTALL_REF:-}"
HOSTS=()
NO_HOSTS=false
ASSUME_YES=false
UV_BIN=""

say() {
    printf '%s\n' "$*"
}

step() {
    printf '==> %s\n' "$*"
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

show_help() {
    cat <<'EOF'
Install PowerContext and selected Agent integrations.

Usage:
  install.sh [--profile local|seekdb] [--host codex|claude-code]... [--no-hosts] [--ref REF] [--yes]

Options:
  --profile PROFILE  Runtime profile. Choose local or seekdb.
  --host HOST        Agent host integration. Repeatable.
  --no-hosts         Install only the Runtime.
  --ref REF          Override the PowerContext marketplace Git ref.
  --yes              Apply the plan without confirmation.
  -h, --help         Show this help.
EOF
}

parse_args() {
    while (($#)); do
        case "$1" in
            --profile)
                (($# >= 2)) || fail "--profile requires a value"
                PROFILE=$2
                shift 2
                ;;
            --profile=*)
                PROFILE=${1#*=}
                shift
                ;;
            --host)
                (($# >= 2)) || fail "--host requires a value"
                HOSTS+=("$2")
                shift 2
                ;;
            --host=*)
                HOSTS+=("${1#*=}")
                shift
                ;;
            --no-hosts)
                NO_HOSTS=true
                shift
                ;;
            --ref)
                (($# >= 2)) || fail "--ref requires a value"
                REF=$2
                shift 2
                ;;
            --ref=*)
                REF=${1#*=}
                shift
                ;;
            --yes)
                ASSUME_YES=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                fail "unknown argument: $1"
                ;;
        esac
    done
}

choose_plan() {
    if [[ -z "$PROFILE" ]]; then
        [[ -t 0 ]] || fail "--profile is required without an interactive terminal"
        read -r -p "Runtime profile [local/seekdb] (local): " PROFILE
        PROFILE=${PROFILE:-local}
    fi
    [[ "$PROFILE" == "local" || "$PROFILE" == "seekdb" ]] || fail "unsupported profile: $PROFILE"
    [[ "$NO_HOSTS" == false || ${#HOSTS[@]} == 0 ]] || fail "--no-hosts cannot be combined with --host"

    if [[ "$NO_HOSTS" == false && ${#HOSTS[@]} == 0 ]]; then
        [[ -t 0 ]] || fail "--host or --no-hosts is required without an interactive terminal"
        local answer
        read -r -p "Hosts [codex, claude-code, none]: " answer
        if [[ "$answer" == "none" ]]; then
            NO_HOSTS=true
        else
            IFS=',' read -r -a HOSTS <<<"$answer"
        fi
    fi

    local host
    for host in "${HOSTS[@]}"; do
        [[ "$host" == "codex" || "$host" == "claude-code" ]] || fail "unsupported host: $host"
    done

    if [[ -z "$REF" ]]; then
        REF=master
    fi
}

confirm_plan() {
    step "Installation plan"
    say "Runtime profile: $PROFILE"
    say "Runtime source: repository ref $REF"
    say "Marketplace ref: $REF"
    if [[ "$NO_HOSTS" == true ]]; then
        say "Hosts: none"
    else
        say "Hosts: ${HOSTS[*]}"
    fi
    if [[ "$ASSUME_YES" == false ]]; then
        [[ -t 0 ]] || fail "confirmation requires an interactive terminal; pass --yes"
        local answer
        read -r -p "Proceed? [y/N]: " answer
        [[ "$answer" == "y" || "$answer" == "Y" || "$answer" == "yes" ]] || fail "installation cancelled"
    fi
}

preflight_hosts() {
    local host
    for host in "${HOSTS[@]}"; do
        case "$host" in
            codex) command -v codex >/dev/null 2>&1 || fail "Codex CLI is not installed or is not on PATH" ;;
            claude-code) command -v claude >/dev/null 2>&1 || fail "Claude Code CLI is not installed or is not on PATH" ;;
        esac
    done
}

install_uv() {
    local uv_install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
    step "Installing uv"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$UV_INSTALL_URL" | env UV_INSTALL_DIR="$uv_install_dir" sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$UV_INSTALL_URL" | env UV_INSTALL_DIR="$uv_install_dir" sh
    else
        fail "curl or wget is required to install uv"
    fi
    UV_BIN="$uv_install_dir/uv"
    [[ -x "$UV_BIN" ]] || fail "uv was installed, but $UV_BIN is not executable"
}

install_runtime() {
    local install_root=${POWERCONTEXT_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/powercontext/distribution}
    local executable_dir=${POWERCONTEXT_EXECUTABLE_DIR:-$HOME/.local/bin}
    local venv="$install_root/venv"
    local extras="cli,server"
    [[ "$PROFILE" == "seekdb" ]] && extras="cli,server,seekdb"
    local requirement
    if [[ -n "${POWERCONTEXT_INSTALL_PACKAGE:-}" ]]; then
        requirement=$POWERCONTEXT_INSTALL_PACKAGE
    else
        requirement="powercontext[$extras] @ git+https://github.com/oceanbase/powercontext.git@$REF"
    fi

    step "Installing PowerContext Runtime"
    mkdir -p "$install_root" "$executable_dir"
    "$UV_BIN" venv --python 3.11 --allow-existing "$venv"
    "$UV_BIN" pip install --python "$venv/bin/python" --upgrade "$requirement"
    [[ -x "$venv/bin/powercontext" ]] || fail "PowerContext was installed without an executable"
    rm -f -- "$executable_dir/powercontext"
    ln -s "$venv/bin/powercontext" "$executable_dir/powercontext"
    "$venv/bin/powercontext" --version >/dev/null

    RUNTIME_PYTHON="$venv/bin/python"
    PUBLIC_EXECUTABLE="$executable_dir/powercontext"
}

json_assert() {
    local expression=$1
    "$RUNTIME_PYTHON" -c "import json,sys; data=json.load(sys.stdin); assert $expression"
}

install_codex() {
    step "Installing Codex integration"
    codex plugin marketplace add "$MARKETPLACE_SOURCE" --ref "$REF" --json >/dev/null
    codex plugin add powercontext@powercontext --json >/dev/null
    codex plugin list --json | json_assert \
        "any(item.get('name') == 'powercontext' and item.get('installed') is True and item.get('enabled') is True for item in data.get('installed', []))"
}

install_claude_code() {
    step "Installing Claude Code integration"
    if ! claude plugin marketplace list --json | json_assert \
        "any(item.get('name') == 'powercontext' for item in data)"; then
        claude plugin marketplace add "$MARKETPLACE_SOURCE@$REF" --scope user
    fi
    claude plugin install powercontext@powercontext --scope user
    claude plugin list --json | json_assert \
        "any(item.get('id') == 'powercontext@powercontext' and item.get('enabled') is True for item in data)"
}

main() {
    parse_args "$@"
    [[ -n "${HOME:-}" ]] || fail "HOME is not set"
    choose_plan
    preflight_hosts
    confirm_plan

    if command -v uv >/dev/null 2>&1; then
        UV_BIN=$(command -v uv)
        step "Using uv at $UV_BIN"
    else
        install_uv
    fi
    install_runtime

    local host
    for host in "${HOSTS[@]}"; do
        case "$host" in
            codex) install_codex ;;
            claude-code) install_claude_code ;;
        esac
    done

    say
    say "PowerContext installation complete."
    say "Runtime: $PUBLIC_EXECUTABLE"
    say "Next: run 'powercontext config init', then 'powercontext server run'."
}

main "$@"
