# Inspect and recover a local installation

Use the installed command help and actual status; the Skill does not implement ownership or package repair.

1. Read local software/registration/manager facts. A stopped or unreachable Server is not an absent installation.
   The independently available operations entry must work even when Runtime imports fail.
2. For a repair request, identify the recorded Runtime interpreter, profile, exact intended release, and source
   controls. Repair only the selected package through the supported operations entry. A source failure does not
   authorize another source or release. Package repair preserves valid configuration and business data.
3. Start, stop, restart, or remove only an explicitly selected local registration verified as PowerContext-owned.
   A port, display name, or PID alone is insufficient. Refuse foreign/unknown ownership and report the exact
   required inspection or recovery action. Remote targets receive connection diagnostics only.
4. Verify the stage requested. A package-manager exit establishes package installation; the actual CLI establishes
   import/startup capability. A native manager result, Server liveness, and readiness are separate facts. Retain
   successful prerequisites when a later check fails and report which step remains incomplete.

Read logs only from the exact owned service's reported selector/path. Preserve denied, unavailable, and unknown
outcomes. Before repeating a partially completed mutation, inspect the returned state. Do not delete configuration,
business data, or an arbitrary environment as a repair shortcut. Program rollback does not imply data-format recovery.
