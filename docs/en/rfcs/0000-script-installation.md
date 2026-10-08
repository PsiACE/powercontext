---
title: Script Installation
description: Install the latest or an exact release with Bash and PowerShell, independent package and runtime mirrors, and explicit Agent selection.
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md), [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

Provide distribution-owned Bash and PowerShell entry points that install PowerContext through uv. The default
release is the latest stable version from the selected package index; `--version` selects an exact release.
The scripts provision missing uv and Python, install the selected Runtime profile, and optionally invoke the
existing Agent integration adapters with a tag matching the installed Runtime.

Installation, configuration, diagnostics, and service operation have separate responsibilities. Installing software
does not configure inference providers, start a Server, or register a persistent service. The scripts and their
installation guide are the first recommended path in the READMEs and Quick Start.

# Motivation

A normal installation should not require users to coordinate Python discovery, uv installation, package extras,
package indexes, and integration Git refs. Package downloads, uv binaries, and Python distributions also use different
sources: changing the PyPI index alone cannot fix an inaccessible Python download.

Installation places a versioned component and registers explicitly selected integrations. Configuration controls
mutable values such as Server URL, Scope, and capture policy. Diagnostics observe the resulting environment.
Keeping these responsibilities distinct lets installation improve independently of configuration storage, shared
Hooks, an operations tool, or service lifecycle changes.

The intended outcome is one installation entry point per shell, reuse of existing dependencies, predictable version
selection, explicit host selection, recoverable partial failures, and verification through the installed product.

# Guide-level explanation

## Install a Runtime profile

On macOS or Linux:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --no-hosts
```

On Windows, using PowerShell 5.1 or newer:

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1))) --no-hosts"
```

`local`, the default profile, installs CLI, Client, and local Server dependencies. `--profile client` installs CLI and
Client dependencies for an existing Server. Profile and host selection are independent: choosing a database role does
not select an Agent host, and selecting Codex does not change the Runtime profile.

The installer reuses uv and compatible system or uv-managed Python. Otherwise it provisions uv and Python 3.12 in
user-owned locations. It prints the actual Runtime version, profile, executable directories, PATH command, and next
configuration command. It does not change persistent PATH settings. Apply the printed PATH command before continuing.

## Select releases and integrations

`--version latest` is the default. Repeating it checks for newer stable releases on the configured index. For a
repeatable package selection, use an exact version:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

`--host` is repeatable. `--no-hosts` skips integration setup. With neither option, an interactive terminal opens
`powercontext setup select`; without a terminal the installer fails before provisioning. Piped Bash installation
uses `/dev/tty` for selection, separate from script input. Git and each selected host's own prerequisites are required.

The installed CLI supplies the exact version for `powercontext-vVERSION`. Host setup never resolves `latest` again
and never defaults to `master`. If a host installation fails, the Runtime remains installed and the script returns
nonzero with instructions to retry setup at that tag. Each adapter supplies its existing host-specific result.

For a local installation, continue with `powercontext config init`, then `powercontext server run --env-file .env`.
Client-only installations follow the remote connection guide to set the Server endpoint and authentication. Persistent services remain an explicit operation.

## Choose download sources

`--region auto|cn|global` overrides `POWERCONTEXT_INSTALL_REGION`. Automatic selection checks local named timezone,
then locale territory, then uses global sources. It does not call a network location service.

| Component | Global source | Automatic China source | Explicit control |
| --- | --- | --- | --- |
| PowerContext and dependencies | PyPI | Tsinghua PyPI mirror | `--index-url`, uv index settings and configuration |
| uv installer and binaries | Astral channels | USTC release mirror | `POWERCONTEXT_UV_INSTALLER_URL`, `UV_DOWNLOAD_URL`, uv installer mirror variables |
| Python | uv default channels | NJU python-build-standalone mirror | `UV_PYTHON_INSTALL_MIRROR`, uv Python download configuration |

Existing uv configuration suppresses automatic package mirror selection. An explicit `--index-url` changes only the
default index; additional indexes keep uv's priority rules. uv owns authentication and index resolution. Script URL
arguments must use HTTPS and omit credentials; credentials belong in uv configuration. pip index environment variables
are not uv configuration. Python and uv download overrides are independent of package indexes.

# Reference-level explanation

## Installation responsibilities

The entry points are `website/public/install.sh` and `website/public/install.ps1`, served as static website assets.
Their supported options and failure behavior are equivalent. They own bootstrap and sequencing; uv owns environment
creation and package resolution, while existing `powercontext setup` adapters own integration installation.

The effective installation inputs are:

```text
Package requirement + Runtime profile + explicit Host selection = installation operation
```

The package manager resolves the requirement before replacing the tool environment. Once installed, the CLI's exact
version is verified and used for all host setup. This keeps one release coordinate for the operation without adding a
second package resolver or introducing a release manifest dependency.

The scripts preserve `powercontext setup`, `config`, `doctor`, and service commands. They do not move domain behavior
into shell, add a standalone Python installer engine, or change Runtime/public HTTP APIs.

## Version and profile semantics

- `latest` passes an unpinned profile requirement to `uv tool install --upgrade --prerelease disallow`. It means the
  newest stable version compatible with the selected interpreter and configured sources, not necessarily the version
  most recently uploaded to another mirror. Resolution failures are reported without substituting another requirement.
- An exact `X.Y.Z`, optionally followed by `aN`, `bN`, or `rcN`, uses `==VERSION`. Explicit prereleases are supported;
  source refs, ranges, and URLs are not `--version` values. Releases before 0.1.0 are excluded.
- `local` uses `powercontext[cli,server]`; `client` uses `powercontext[cli]`, whose dependencies include the Client.
  Rerunning with a different profile replaces that tool's dependency set. Independently added extras must be managed
  through the documented manual installation path.
- The installed executable must report a release version. Exact requests must match it. The script prints success
  only after this check. The user receives a usable Runtime even if subsequent host setup fails.

uv is bootstrapped at an installer-controlled version; that version is independent of the PowerContext release.
An existing uv is reused rather than upgraded silently. Python discovery excludes virtual environments so a project
venv cannot become an accidental installation prerequisite. If no compatible Python 3.11+ is found, uv provisions
Python 3.12. uv remains responsible for platform, architecture, and wheel compatibility errors.

## Mirror precedence and recovery

User-specified uv/Python sources are preserved without fallback. For automatic China selection, an unavailable uv
mirror installer falls back to Astral; failed mirrored uv artifacts may use official sources. Python's exact build
URL is obtained from uv, and its mirrored artifact is checked before installation. If the automatic Python mirror
fails, installation retries with uv's default channels.

Automatic package selection checks the PowerContext index page and, for exact requests, the listed version. An
unavailable or unsynchronized China index falls back to PyPI. After selection, uv owns dependency resolution and
artifact errors; the script does not retry a partially attempted tool installation against another index.
Explicit indexes bypass shell probing so uv can apply its authentication and configuration correctly.

Shell environment changes are local to the installer process; PowerShell restores temporary source variables in
`finally`. Existing configuration files are neither parsed by shell nor rewritten. Only installer-owned temporary
downloads are cleaned. `UV_INSTALL_DIR`, `UV_TOOL_DIR`, and `UV_TOOL_BIN_DIR` retain their location controls.

## Persistence and compatibility

The installer does not read or rewrite `.env`, credentials, data directories, or database schemas. It does not stop
or restart a running service. Users follow the existing upgrade and migration instructions when starting an updated
Server. An exact-version retry may reuse cached packages; `latest` intentionally allows upgrades.

`UV_OFFLINE=1` permits cached reinstallation only when uv, a compatible Python, and all dependencies are present.
It does not promise a complete offline distribution. A failure after bootstrapping leaves installed prerequisites
available for retry; a failure in host setup preserves the Runtime. There is no cross-component rollback or repair
of unrelated host configuration.

Windows retains its experimental product status. Native acceptance runs on Linux, macOS, and Windows; host-specific
support still comes from each integration's capability contract.

## Acceptance

`tests/native/test_installation.py` runs the actual shell installer, uv, installed CLI, and HTTP Server. A wheel built
from the tested commit uses release-shaped metadata and an exact file/checksum constraint. Qualification includes:

- missing uv/Python under global and China source selection, existing tools/configuration, and spaces/Unicode paths;
- real stable/prerelease wheel resolution through a controlled package index, default upgrade, exact selection, and
  preserving the installed version when a requested release is unavailable;
- Client-only installation and profile changes, with no local Server state created by installation;
- `.env` generation and validation, readiness, Memory remember/search, cached offline reinstall, and restart readback;
- explicit index failure, non-interactive missing choices, piped Bash terminal selection, and the downloaded
  PowerShell scriptblock entry point.

A separate CI matrix executes the suite on all three operating systems. Release reference checks preserve script
`latest` defaults while updating explicit version examples. Website validation confirms documentation links and the
static build. Syntax checks alone do not establish Windows or macOS installation support, and these tests do not
claim that a real Agent host completes its capture/recall workflow.

# Drawbacks

Bash and PowerShell duplicate some sequencing and source-selection policy. Automatic mirrors add operational
availability dependencies. `latest` deliberately changes over time and is unsuitable for reproducible deployment
without an exact version. Matching tags coordinate releases but do not independently prove host compatibility.

# Rationale and alternatives

A uv-only command is retained for users who already manage Python and uv; it cannot bootstrap a clean machine.
Source installation is useful for development but is not the default release channel. Pinning the public script to
a PowerContext version would leave new users on stale releases until the website is republished.

A standalone installer engine and immutable component manifest can serve richer installation plans. They are not
prerequisites for package bootstrap or reliable source controls. Delegating to existing package and host adapters
keeps this change independently deliverable without removing working CLI contracts.

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) defines the separation of installation, configuration,
and diagnostics; independent Runtime profiles and host selection; component-level recovery; and explicit service
registration. [RFC 1299](1299_local_server_availability_and_service_installation.md) defines personal service lifecycle.
[Bub](https://github.com/bubbuild/bub/tree/main/website/public) provides Bash/PowerShell bootstrap entry points that
reuse uv and report configuration next steps. [uv](https://docs.astral.sh/uv/guides/tools/) supplies isolated tools,
Python provisioning, package indexes, and caches.

# Unresolved questions

No further cross-subsystem decision is required for this contract. Complete offline bundles, cryptographic installer
manifests, independently packaged host artifacts, and changes to configuration storage need separate designs.
The platform matrix must remain green before promotion; Windows product support remains experimental.

# Future possibilities

The scripts can bootstrap a distribution-owned installer engine if installation plans later need immutable component
manifests, independent integration versions, or durable per-component repair records. An offline bundle can include
all required artifacts explicitly. Neither extension should make Server startup or service registration implicit.
