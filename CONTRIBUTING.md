# Contributing to PowerContext

PowerContext accepts bug fixes, features, documentation changes, and design proposals through GitHub issues and pull
requests.

## Before you start

- Search the [issue tracker](https://github.com/oceanbase/powercontext/issues) for existing reports or proposals.
- Use an issue to discuss a feature when its behavior or scope is not yet clear.
- Use the [RFC process](docs/en/rfcs/README.md) for changes to public APIs, persisted formats, compatibility guarantees,
  integration boundaries, or core architecture.
- Look for issues labeled `help wanted` if you want a task that maintainers have opened to contributors.

## Set up the repository

You need Git and [uv](https://docs.astral.sh/uv/). Fork the repository, then clone your fork:

```bash
git clone git@github.com:YOUR_NAME/powercontext.git
cd powercontext
```

Install the locked development environment and Git hooks:

```bash
make install
```

Recommended Codex skills are optional and are not required to build or test the project. If `npx` is available, you
can install the versions pinned in `skills-lock.json` before starting a new Codex session:

```bash
make skills-install
```

Create a branch from the current `master` branch:

```bash
git switch -c <short-description>
```

## Make a change

Keep each pull request focused on one problem. Add tests for new observable behavior and for defects that could recur.
Do not add tests solely to increase coverage when they would only preserve implementation details.

Update user documentation when behavior, configuration, interfaces, or compatibility changes. Keep the English and
Chinese documentation aligned. If you change `openapi/powercontext.yaml`, run `make api-generate`; do not edit files
under `src/powercontext/http/_generated/` by hand.

## Validate the change

Run the checks that cover your change:

| Command | Use it for |
| --- | --- |
| `make check` | Lock-file consistency, formatting, linting, and type checking |
| `make unit-test` | Tests that do not cross the Server boundary |
| `make e2e-test` | CLI-to-Client-to-Server acceptance behavior |
| `make test` | The complete pytest suite with doctests |
| `make contract-test` | OpenAPI contract and generated bindings |
| `make docs-test` | Strict documentation build |
| `tox` | Supported Python version matrix |

Run `tox` when a change may affect supported Python versions. It requires the relevant Python interpreters to be
installed locally; the same version matrix runs in CI.

## Open a pull request

Push the branch to your fork and open a pull request against `oceanbase/powercontext`:

```bash
git push -u origin <short-description>
```

Use a short Conventional Commit-style pull request title. Complete every relevant section of the pull request
template, including:

- the related issue or RFC;
- the rationale and behavior changes;
- user-facing, compatibility, or migration impact;
- validation commands and tests;
- the AI usage statement.

## Report a bug

Open a [bug report][bug-report] with the steps needed to reproduce the problem, the expected and actual behavior, your
operating system and version, and relevant local configuration. Remove credentials and other sensitive data from logs
or examples.

## Propose a feature

Open a [feature request][feature-request]. Describe the problem, the proposed behavior, alternatives you considered,
and the smallest useful scope. Maintainers may ask for an RFC before implementation if the proposal changes a
substantial public contract.

[bug-report]: https://github.com/oceanbase/powercontext/issues/new?template=1-bug-report.yml
[feature-request]: https://github.com/oceanbase/powercontext/issues/new?template=2-feature-request.yml
