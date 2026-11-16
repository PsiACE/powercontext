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

"""Stable failures for Scope organization and binding."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class ScopeError(PowerContextError):
    """Base failure for Scope operations."""


class ScopeNotFoundError(ScopeError, LookupError):
    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__("scope was not found")


class ScopeBindingTargetMissingError(ScopeError, RuntimeError):
    """A persisted binding references a missing Scope and requires operator repair."""

    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__("persisted Scope binding target is missing")


class ScopeVersionConflictError(ScopeError, RuntimeError):
    def __init__(self, scope_id: str, expected: int, actual: int) -> None:
        self.scope_id = scope_id
        self.expected = expected
        self.actual = actual
        super().__init__("scope metadata changed since it was read")


class ScopeIdempotencyConflictError(ScopeError, RuntimeError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("scope creation key was reused with different parameters")


class ScopeRelationshipError(ScopeError, ValueError):
    def __init__(self, relationship: str, issue: str) -> None:
        self.relationship = relationship
        self.issue = issue
        super().__init__(f"invalid Scope {relationship}: {issue}")


class ScopeBindingNotFoundError(ScopeError, LookupError):
    """Raised when no explicit, durable, or default binding can be resolved."""

    def __init__(self) -> None:
        super().__init__("no Scope binding is available")


class ScopeNotWritableError(ScopeError, RuntimeError):
    """An ordinary content write was refused by the named Scope's lifecycle state."""

    def __init__(self, scope_id: str, state: str) -> None:
        self.scope_id = scope_id
        self.state = state
        super().__init__("scope is not writable")


class ScopeLifecycleConflictError(ScopeError, RuntimeError):
    """Archive or reopen was requested for a state the Scope no longer holds."""

    def __init__(self, scope_id: str, requested: str, state: str) -> None:
        self.scope_id = scope_id
        self.requested = requested
        self.state = state
        super().__init__("scope lifecycle transition is not available from the current state")


class ScopeDeletionConflictError(ScopeError, RuntimeError):
    """A deletion request was refused because the confirmed set or children changed."""

    def __init__(self, scope_id: str, reason: str) -> None:
        self.scope_id = scope_id
        self.reason = reason
        super().__init__("Scope deletion was refused")


class ScopeDeletionBindingRepairError(ScopeError, RuntimeError):
    """A deployment default or persistent binding must be repaired before its Scope is removed."""

    def __init__(self, scope_id: str, binding: tuple[str, str, str] | None, *, is_default: bool) -> None:
        self.scope_id = scope_id
        self.binding = binding
        self.is_default = is_default
        super().__init__("Scope is still referenced by a deployment default or persistent binding")


class ScopeResourceHandlerUnsupportedError(ScopeError, RuntimeError):
    """A required cleanup mode cannot be performed, so the deletion is refused before acceptance."""

    def __init__(self, handler: str, reason: str) -> None:
        self.handler = handler
        self.reason = reason
        super().__init__("Scope resource cleanup is unsupported")
