---
name: powercontext-install
description: Install, connect, inspect, repair, upgrade, or remove a PowerContext environment and its owned local service (安装、连接、诊断、修复、升级、卸载). Use for environment maintenance; project Memory and Handoff work use the product capabilities.
---

# PowerContext environment maintenance

Select the requested outcome: install software, configure a connection, inspect status, repair a package, or control
an owned local service. These are separate operations. Reuse the user's selected Server, profile, source, and host.
Read [maintenance](references/maintenance.md) when carrying out local diagnosis, package repair, or service control.

If neither PowerContext entry exists, read the official [installation guide](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/)
and [bootstrap workflow](references/bootstrap.md) to select the requested local or Client-only installation.
After installation, discover the installed version and command help before choosing arguments. Prefer the independently available
`powercontext-ops` entry when present. A release without that entry cannot repair itself through a broken Runtime;
use its documented uv/bootstrap recovery path and report that limitation. Do not invent commands or a daemon.

Installing a host integration or Skill does not prove connection or a successful host workflow. Report actual
software, connection, service, and host results separately. Configuration remains owned by the corresponding
Server or Client operation. Connection to a remote Server never authorizes controlling that remote deployment.
