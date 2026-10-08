# Save or find durable context

An explicit save uses the host's Memory write operation, such as `remember_memory` when exposed. Automatic Source
capture is processing evidence and does not satisfy a Memory save. Report the actual returned Memory citation;
use its supported exact-read operation when readback is needed. A candidate workflow is not a prerequisite for a
direct Memory write. Preserve the exact revision when revising or retiring a record.

Search uses the supplied query and current Scope. Inventory lists a collection only when requested. Empty search
is an ordinary result and does not authorize a broader listing or a different Scope. Read selected records by their
exact citation; do not claim that a hit describes the current repository before checking current state.

A failed write is not saved. A timed-out write has an unknown outcome; inspect a supported status/read path before
retrying, rather than assuming the operation was never accepted. Keep credentials and unrelated private records
out of stored content and reported results.
