# PowerContext

PowerContext is the 2.0 continuation of [PowerMem](https://www.powermem.ai/). It stores project-scoped context so a
later agent session can recover decisions, outcomes, current state, and next steps without relying on chat history.

The repository contains a local Server with SQLite storage, an asynchronous Python client, a Core SDK, a CLI, and a
Codex plugin. Start with the [documentation](https://oceanbase.github.io/powercontext/en/docs/) or read it in
[Chinese](https://oceanbase.github.io/powercontext/zh/docs/).

## Quickstart with Codex

You need macOS or Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/), Codex CLI, and read access to
`oceanbase/powercontext`.

Install PowerContext and configure the Codex plugin:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
```

Start the local Server:

```bash
powercontext server run
```

In another terminal, check the package, plugin, Server, and database:

```bash
powercontext doctor
```

Start a new Codex session after installation. If Codex asks you to trust the PowerContext hook, open `/hooks` and
approve it. PowerContext uses a persistent local database by default.

The [Codex quickstart](docs/en/docs/tutorials/codex-quickstart.md) walks through saving and restoring context across
sessions. See [Install and run](docs/en/docs/how-to/install-and-run.md) for updates, alternate Git refs, and Python
package installation.

## Interfaces

| Interface | Use it for |
| --- | --- |
| Codex plugin | Recall project context and explicitly remember, revise, or retire Memory while coding |
| CLI | Configure the plugin, run or connect to the Server, inspect content, and diagnose an installation |
| Python Client SDK | Make typed asynchronous calls to a running Server |
| Core SDK | Embed the PowerContext contracts or provide custom adapters in a Python application |
| HTTP | Integrate a service or a non-Python application |
| MCP | Expose selected Memory and Candidate Review operations to an agent host |

The [interface guide](docs/en/docs/reference/interfaces.md) describes these boundaries and links to the detailed
reference for each surface.

## Python installation

Install only the role that the application imports. For example, add the Client SDK with:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Available extras are `builtin`, `client`, `server`, and `cli`. The `cli` extra includes Server-backed content
commands. Install the `server` extra when the local process also needs to run the Server.

## Documentation

- [Codex quickstart](docs/en/docs/tutorials/codex-quickstart.md) is a guided cross-session workflow.
- [How-to guides](docs/en/docs/index.md#how-to-guides) cover installation, Codex configuration, and troubleshooting.
- [Concepts](docs/en/docs/index.md#concepts) explain the Artifact lifecycle and interface boundaries.
- [Reference](docs/en/docs/index.md#reference) covers configuration, commands, SDKs, HTTP, MCP, and the Python API.
- [Development guides](docs/en/development/index.md) describe package boundaries and implementation workflows.
- [RFCs](docs/en/rfcs/README.md) record design proposals and decisions; they are not a statement of released behavior.

## Benchmarks

The [LoCoMo benchmark](benchmark/locomo/README.md) documents the dataset, scoring contract, commands, outputs, and
evaluation limits. The 1,540-question run reports 90.78% answer accuracy, 1.38 s search p95 latency, and about
1.65 k answer tokens per question. See the benchmark documentation before comparing these numbers with another run.

## Contributing

Run `make install` to create the locked development environment and install hooks. Before opening a pull request, use
the checks that match the change:

```bash
make test
make check
make docs-test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, test groups, API generation rules, and pull
request requirements.

## License

PowerContext is available under the [Apache License 2.0](LICENSE).
