# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PowerContext operation names and request validation metadata."""

from __future__ import annotations

OPERATION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "prepare_context": ("query",),
    "capture_content_source": ("source_id", "content"),
    "revise_memory_entry": ("citation", "kind", "text"),
    "create_work_contract": ("source_id", "contract"),
    "handoff_current_work": ("source_id", "handoff"),
    "acknowledge_handoff": ("source_id", "receiver", "status", "selection"),
    "record_task_outcome": ("source_id", "outcome"),
    "activate_handoff": ("boundary_source", "objective"),
    "prepare_handoff": ("objective", "evidence"),
    "finalize_handoff": ("draft",),
    "commit_handoff": ("handoff",),
    "continue_handoff": ("selection",),
    "propose_experience": ("proposal", "source_refs", "artifact_refs"),
    "generate_experience": ("source_refs", "artifact_refs"),
    "get_experience": ("artifact",),
    "propose_skill": ("proposal", "source_refs", "artifact_refs"),
    "generate_skill": ("origin", "source_refs", "artifact_refs"),
    "get_skill": ("artifact",),
    "resolve_external_skill": ("external_skill_id", "fingerprint"),
    "import_external_skill": ("external_skill_id", "fingerprint", "mode"),
    "get_artifact_candidate": ("candidate_id",),
    "approve_artifact_candidate": ("candidate_id", "expected_version"),
    "reject_artifact_candidate": ("candidate_id", "expected_version", "reason"),
    "revise_artifact_candidate": (
        "candidate_id",
        "expected_version",
        "proposal",
        "source_refs",
        "artifact_refs",
    ),
}
