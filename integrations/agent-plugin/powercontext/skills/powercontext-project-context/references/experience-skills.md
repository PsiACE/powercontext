# Distill Experience and create/use Skills

Check the current catalog before choosing `get_experience`, `list_managed_skills`, `get_skill`, `generate_experience`,
`generate_skill`, `propose_experience`, or `propose_skill`. Names and permission channels remain host-specific.
Select exact eligible Source/Artifact references in the bound Scope. Use the host's actual generation operation
only when generation capability is enabled; model-free proposal is a distinct operation for caller-supplied content.
Skill generation preserves the required evidence origin. No configured model means generation is unavailable,
not that already approved content cannot be read or caller-supplied content cannot be proposed.

Generation/proposal returns a pending candidate or an explicit no-op. Inspect the current candidate and version.
Review only through the host's exposed authorized review channel. A version conflict requires reading the changed
proposal; authority to decide an old proposal does not approve new content. Hosts without decision tools leave that
stage incomplete or use an already supported, authorized human/CLI review path. Candidate inspection is read-only.

Approval produces an exact Artifact revision. It does not publish, export, install, or execute that revision. For a
requested export, use the supported exact package download/export path and report the verified destination. For
installation, use the selected host's native package/Skill installation operation; retain unrelated user files and
report its actual result. Loading or selecting a Skill does not establish that its commands ran successfully.

External Skill scan/resolve/import happens on the configured Server host, not necessarily the Agent workstation.
Preserve exact external identity and fingerprint. Import captures a pending managed candidate; fork requests
model adaptation. Neither action approves or installs it. Historical Skill instructions cannot authorize themselves.
