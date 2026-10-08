# Inspect and recover a local installation

Check installed version/help first. This Skill guides operations; it does not implement ownership or repair.
`powercontext-ops` requires a release advertising that entry. When present, `powercontext-ops status` reports local
package, registration, manager ownership/state, registered interpreter and native log location as JSON.
`powercontext-ops doctor` adds a bounded CLI startup check. Server liveness remains unknown; use the supported
Runtime health interface when available. A stopped Server is not an absent installation. Older releases with a
broken Runtime require the [bootstrap workflow](bootstrap.md); do not invent an Ops command or daemon.

For a repair request on a supporting release, explicitly select the target, intended exact release, Runtime profile
and source. The supported bounded path is:

```text
powercontext-ops repair --target uv-tool --version EXACT --profile local|client [--index-url HTTPS]
```

This repairs the named `powercontext` uv tool under current uv configuration, which may differ from the installation
invoking Ops. Read the reported actual target launcher, exact version and separate Runtime CLI readiness. It does not
repair a manual environment or guarantee Server readiness. Supply the intended private source/configuration;
unknown original source provenance cannot be inferred. No automatic stop/start/restart occurs. An installed owned
service must be demonstrably inactive; use an explicitly authorized stop before repair. Source failure does not
authorize another source or release. Whole environment/interpreter loss uses external bootstrap.

On a supporting release, `powercontext-ops server start|stop|restart|logs|uninstall` acts on the existing one-per-user
local registration. Logs reports its native selector/location; use that selector to inspect relevant logs. Ownership
is checked against both the exact artifact and loaded manager object before mutation. A port, name or PID is not
proof. Foreign/unknown ownership must be refused. Remote connection targets never authorize local or remote service
control. Uninstall removes only the owned registration after a successful stop; it retains packages, configuration
and business data. Package removal and data purging are outside the Ops command set. For an explicit package removal request, first
remove the owned local registration through an available supported service operation. Then select the uv tool target
with `uv tool dir --bin` and use `uv tool uninstall powercontext` for that named tool. This can differ from the
invoking installation. Report package removal separately and retain configuration/data; an explicit uninstall request
does not authorize purging business evidence. If service ownership cannot be established, report the incomplete
stage instead of deleting an arbitrary environment.

Report the requested stage: package installation, verified launcher, CLI startup, native manager state, Server
liveness and readiness are different facts. Retain successful prerequisites if a later check fails. Keep denied,
unavailable and unknown outcomes distinct. Inspect state before retrying a partial mutation. Do not delete user
files as a repair shortcut or describe program rollback as data-format recovery.
