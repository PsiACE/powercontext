# Install before Runtime exists

Use the current official [PowerContext installation guide](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/)
to discover the supported release and platform. Prefer the Bash or PowerShell installer when that guide advertises a
published script for the release. Download from the guide's documented URL, inspect the platform's documented help option, then choose the
requested local or Client-only profile and exact version or default latest stable. Preserve explicit package,
uv-binary and Python mirror choices. Script installation selects software; configuration, host integration and
persistent service registration are separate tasks.

A script present in a development branch is not proof that the website URL is published. If the guide does not yet
advertise a usable script, use its existing uv installation path. If uv is absent, use
[Astral's official installation instructions](https://docs.astral.sh/uv/getting-started/installation/) for that platform.
Inspect existing uv/Python first. Installing uv does not install PowerContext or configure a provider.

With uv available, the manual package requirements are `powercontext[cli,server]` for local use and `powercontext[cli]`
for an existing Server. For example, `uv tool install --upgrade --reinstall-package powercontext --prerelease disallow "powercontext[cli,server]"`
selects latest stable even when an installed prerelease would otherwise be retained. For an exact requested release, use `uv tool install --upgrade --reinstall-package powercontext
"powercontext[cli,server]==VERSION"` (or the Client-only extras), with the requested version in place of `VERSION`.
Keep latest-stable prerelease filtering separate from explicit version selection. Package indexes, uv
binaries and Python distributions have separate source controls. Do not change an explicit source or version after
failure; a package index change cannot repair an inaccessible Python download.

After installation, check `powercontext --version` and actual help. Releases predating `powercontext-ops` do not gain
it by loading this Skill. Discover advertised commands/capabilities before configuration, connection, host integration
or service operations. Verify software, connection, selected host and Server health separately. Missing wheel,
environment or interpreter requires this external bootstrap path, not a missing maintenance command.
