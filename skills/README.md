# Independently loadable PowerContext Skills

[`powercontext-install`](powercontext-install/SKILL.md) guides installation, connection checks, package repair and
owned local service maintenance. Install this complete folder with the selected Agent host's native Skill installer;
include its `references/` directory. It needs no PowerContext Runtime to load. Follow the published installation
guide, discover installed command help, and report which software, connection or service stage actually succeeded.

Project Memory, Handoff, Experience and managed Skill workflows use each host integration's
`powercontext-project-context` Skill. Canonical resources and explicit host overrides are declared in
[`targets.json`](../integrations/distribution/skills/targets.json); regenerate them with `make plugin-skills`.
Neither Skill loading nor package installation proves a successful Agent execution.
