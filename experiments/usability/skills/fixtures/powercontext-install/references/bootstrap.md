# Install before the Runtime exists

Use the official [PowerContext installation guide](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/)
for the current supported platform and release path. Inspect existing uv/Python first and preserve the user's sources.
If uv is absent, use [Astral's official installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
for that platform; installing uv does not install PowerContext or configure a provider.

When uv is available, the existing manual package path is `uv tool install "powercontext[cli,server]"` for local use,
or `uv tool install "powercontext[cli]"` for an existing Server. An exact requested release appends `==VERSION` to
that requirement. For latest stable selection, use uv's `--upgrade --reinstall-package powercontext --prerelease disallow` controls. Package indexes,
uv binaries, and Python distributions have separate source controls; an inaccessible Python download is not repaired
by changing only the package index. Do not switch an explicitly chosen source or version after failure.

After installation, check `powercontext --version` and the actual command help. Configuration, connection, selected
host integration, persistent service registration, and live verification are separate subsequent operations. Use
only the operations present in that release. Missing or broken environment/interpreter requires this external
bootstrap path; the maintenance Skill is guidance and cannot execute a missing command by itself.
