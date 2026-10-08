# Installation source experiments

The installer should delegate package interpretation to uv, preserve explicit source intent, and keep uv, Python,
and package downloads independent. The reproducible ablations demonstrate that a shell filename probe rejects a
release that uv successfully installs. Automatic region hints change source choice without establishing network
reachability. Conservative configuration-file suppression remains a deliberate compatibility choice.

## Reproduce

Run from a full checkout containing baseline commit `8b2ea9576a92154cd00a2a0609afdb302010cddf`:

```bash
python experiments/installation/sources/run.py \
  --scratch /home/psiace/.cache/powercontext-installation-research/sources \
  --output experiments/installation/sources/results.json \
  --pwsh /home/psiace/.cache/powercontext-installation-pwsh/pwsh
```

`--uv` accepts a different uv executable. `--pwsh` is optional. The harness uses the standard library, creates
a disposable home/config/cache/tool directory, serves deterministic wheels through a loopback Simple API index,
and executes real uv and the installed fixture CLI. It enumerates subprocess environment variables rather than
inheriting authentication, proxies, or user uv settings. Scratch paths and loopback ports are normalized in the
committed result. It does not modify the user's tool environment, configuration, or persistent PATH.

The fixture distribution is deliberately named `powercontext`, but contains only a version-reporting entry point.
These results establish package-manager/source behavior, not PowerContext Runtime or Server acceptance. Bash and
PowerShell helper probes load functions from the pinned baseline without executing the main installer. PowerShell
7 on Linux verifies parsing and protocol behavior; it does not qualify Windows PowerShell 5.1 or native Windows.

## Evidence and interpretation

The measured environment is Linux x86_64, harness Python 3.14.7, uv 0.11.14. The installer baseline pins missing-uv bootstrap
at 0.12.23; existing uv reuse means 0.11.14 is an independently relevant path. Python discovery independently reuses a local 3.13 interpreter. All 46 measured cases meet their
expected exit/result assertions. [results.json](results.json) contains elapsed milliseconds, requests, exit codes,
and normalized output for every case. Durations are single-run local measurements, not Internet latency estimates.

| Ablation or fault | Measured outcome | Decision supported |
| --- | --- | --- |
| HTML exact-version preflight vs uv-native install | Encoded `1%2E0%2E0` wheel URL with opaque anchor text: Bash and PowerShell probes fail; real uv installs `1.0.0` | Remove filename/HTML interpretation from source selection |
| Latest stable vs exact | Latest installs `1.1.0` despite `1.2.0rc1`; exact stable installs `1.0.0`; exact prerelease installs `1.2.0rc1` | Retain latest default and exact requirements; uv owns PEP 440 |
| Missing exact release | `9.9.9` fails resolution; prior executable still reports `1.2.0rc1` | Resolution failure must not substitute another release |
| Explicit failing source | Forbidden default index fails; no fallback request | Preserve explicit source intent |
| Automatic failed candidate then fallback | First uv attempt receives 503; separate fallback attempt installs `1.0.0` | Fallback can use uv-native operations without HTML probing; requires explicit provenance |
| Conflicting indexes | Additional old index blocks `1.1.0` on default new index | `--default-index` is not exclusive source selection |
| Environment overrides | CLI default beats `UV_DEFAULT_INDEX`; `UV_INDEX` remains higher-priority additional index | Preserve uv's normal precedence and index strategy |
| HTTP 404 vs 403 additional index | 404 continues to default; 403 stops resolution | Do not implement a second status-code policy |
| Exact runtime reuse | Already installed `1.1.0` succeeds with failing configured default, making zero requests | Success can mean reuse; do not imply a network refresh |
| Offline cache vs missing version | Cached exact succeeds, uncached exact fails; both make zero requests | Offline is conditional cache reuse, not an offline bundle |
| Locale/timezone inference vs explicit global | Shanghai and UTC+Chinese locale choose cn; Singapore+Chinese locale chooses global; absent hints choose global | Hints describe preference; explicit policy is deterministic |
| Unrelated user uv.toml | `compile-bytecode = false` suppresses automatic package mirror; explicit CLI source still resolves; bytes unchanged | Document conservative suppression; do not parse/merge arbitrary uv config |
| Explicit cn vs auto cn with unrelated config | Both preserve existing configuration and suppress automatic package index | Region selection is lower priority than existing uv configuration |
| User config vs project config | Tool install obeys user old index and ignores local new index | Source policy must match the particular uv command |
| Proxy | Real uv resolves and downloads through loopback HTTP proxy with absolute target URLs | Delegate proxy handling to uv; this is not HTTPS CONNECT qualification |
| Existing Python | Real `uv python find --system --no-project --no-python-downloads` reuses compatible local interpreter | Discover before provisioning |
| Python mirror precedence | `UV_PYTHON_INSTALL_MIRROR` determines exact build URL over `UV_ASTRAL_MIRROR_URL` | Package index and Python source are separate controls |
| Python explicit mirror failure/offline | Real uv requests only explicit failed Python mirror; missing offline Python makes zero requests | Preserve explicit mirror failure and offline boundary |

Fresh-resolution fault cases use `--upgrade --reinstall --refresh` so an already installed fixture cannot hide a
source error. Offline cases omit reinstall/refresh: uv rejects combining refresh with offline. The separately
named reuse case deliberately follows exact-install behavior without these fault-forcing flags. This distinction
matters when interpreting exit success and request counts.

The two automatic-candidate attempts prove a protocol building block, not the full baseline script fallback
workflow. They do not establish atomic recovery after artifact extraction or filesystem failures. Mirror uptime,
China accessibility, native macOS/Windows execution, authenticated private indexes, TLS proxies, and successful
Python/uv artifact bootstrap require additional acceptance evidence.

## Minimal source contract

1. A source selection has a component, value, and provenance: explicit argument/environment/configuration,
   distribution default, or inferred default. Only inferred/default candidates permit distribution-owned fallback.
2. uv owns package versions, Simple API parsing, authentication, dependency resolution, index ordering, proxy
   behavior, and cache behavior. The installer supplies a requirement and scoped source controls.
3. `latest` is an unpinned stable requirement with upgrade enabled. Exact selection uses equality and never falls
   back to a different version. Report the installed CLI's actual version after the operation.
4. The package default index, uv bootstrap artifact source, and Python distribution mirror are independent.
   Configuring one must not silently rewrite either other component.
5. Existing uv/Python are reused. Explicit uv/Python/package sources fail visibly without distribution-owned
   source substitution. Automatic fallback logs the failed component and next candidate.
6. User config and source environment variables remain unchanged. Conservative suppression on any applicable
   uv config file is acceptable when documented; region selection alone does not override it. Explicit
   `--index-url` changes uv's default index while additional indexes keep priority.
7. Offline disables installer probes and artifact downloads as well as uv networking. Installation succeeds only
   with reusable prerequisites and all required cached/local packages.
8. Region inference is optional input selection, not reachability evidence. An explicit global/cn profile must
   remain available independently of timezone and locale.

## Independent production slice

Deliver source-policy cleanup separately from host integration, service startup, configuration, and immutable
component manifests. Keep all three mirror controls and latest/exact selection. Remove the global package HTML
preflight first: global and explicit package sources go directly to uv. For automatic mirror fallback, select
one documented uv-native operation and preserve its requirement/interpreter/source contract; emit the component
and candidate on failure. Do not use `uv pip compile` as a transparent preflight for `uv tool install`: project
and `[pip]` configuration scopes differ. Either qualify complete uv tool attempts under the promised recovery
contract, or constrain fallback to bootstrap components until a safe package-resolution boundary is established.

Retain conservative existing-config suppression initially. A stable effective-config export was not found in
uv 0.11.14's public commands/documentation; ad hoc TOML parsing and configuration discovery would create a second
configuration implementation. A later change may improve this boundary only with an explicit compatibility
contract and behavior evidence. Keep local automatic hints isolated so removing or changing inference does not
change explicit package/uv/Python source controls.

## Primary references

The implementation under study is pinned: [Bash](https://github.com/PsiACE/powercontext/blob/8b2ea9576a92154cd00a2a0609afdb302010cddf/website/public/install.sh),
[PowerShell](https://github.com/PsiACE/powercontext/blob/8b2ea9576a92154cd00a2a0609afdb302010cddf/website/public/install.ps1),
and [RFC 1892](https://github.com/PsiACE/powercontext/blob/8b2ea9576a92154cd00a2a0609afdb302010cddf/docs/en/rfcs/1892-script-installation.md).

uv 0.11.14 resolves to commit `3fdfdc7d4a63c9f283eb751823b7628b13116684`:
[public CLI](https://github.com/astral-sh/uv/blob/3fdfdc7d4a63c9f283eb751823b7628b13116684/crates/uv-cli/src/lib.rs)
and [configuration/dispatch](https://github.com/astral-sh/uv/blob/3fdfdc7d4a63c9f283eb751823b7628b13116684/crates/uv/src/lib.rs).
Current official documentation was checked on 2026-10-09:
[configuration](https://docs.astral.sh/uv/concepts/configuration-files/),
[indexes](https://docs.astral.sh/uv/concepts/indexes/), and
[environment controls](https://docs.astral.sh/uv/reference/environment/).
These references establish documented policy; the committed measurements establish executed behavior for the
specific binary and controlled inputs above.
