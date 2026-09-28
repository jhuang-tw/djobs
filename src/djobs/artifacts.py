"""One application owner for typed temporal memory and source-bound review.

Proposals never activate themselves. Reads only select existing canonical data;
source loss suppresses derived content even before explicit maintenance deletes
it. No operation calls a model, changes a task, or edits an agent prompt.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Any

from djobs.memory_artifacts import (
    MAX_ARTIFACTS,
    MAX_DEPTH,
    ArtifactDraft,
    ArtifactError,
    artifact_content_hash,
    canonical_json,
    digest,
    observation_hash,
    safe_text,
    timestamp,
)
from djobs.memory_learning import effective_type, learning_source_checks, skill_markdown
from djobs.memory_policy import lexical_terms, metadata_object, observation_exclusion
from djobs.memory_projection import FOLDERS, context_uri, parse_context_uri
from djobs.memory_review import ReviewGate, ReviewRequest
from djobs.privacy import redact_value
from djobs.storage.artifacts import ArtifactStore, delete_artifacts


def _scope_key(workspace: Any, scope: str, agent: str, session: str) -> str:
    if scope == "repository_family":
        return str(workspace.repo_family_id or workspace.workspace_id)
    if scope == "checkout":
        return str(workspace.checkout_id or workspace.workspace_id)
    if scope == "agent" and agent:
        return safe_text(agent, 160, required=True)
    if scope == "session" and session:
        return hashlib.sha256(session.encode()).hexdigest()[:16]
    raise ArtifactError("explicit_private_scope_required")


class ArtifactView:
    """Pure bounded eligibility over a detached source snapshot."""

    def __init__(
        self,
        snapshot: dict[str, Any],
        workspace: Any,
        *,
        agent: str = "",
        session: str = "",
        private: bool = False,
    ) -> None:
        self.data = snapshot
        self.workspace = workspace
        self.family = str(workspace.repo_family_id or workspace.workspace_id)
        self.agent = agent
        self.session_hash = hashlib.sha256(session.encode()).hexdigest()[:16] if session else ""
        self.private = private

    def visible(self, row: dict[str, Any]) -> bool:
        if row.get("repo_family_id") != self.family:
            return False
        scope, key = row.get("scope"), row.get("scope_key")
        return bool(
            (scope == "repository_family" and key == self.family)
            or (
                scope == "checkout"
                and key == (self.workspace.checkout_id or self.workspace.workspace_id)
            )
            or (scope == "agent" and self.private and self.agent and key == self.agent)
            or (
                scope == "session"
                and self.private
                and self.session_hash
                and key == self.session_hash
            )
        )

    def raw_reason(self, row: dict[str, Any]) -> str | None:
        metadata = metadata_object(row.get("metadata_json"))
        if metadata is None:
            return "corrupt_source"
        scope = metadata.get("scope", "repository_family")
        if scope in {"agent", "session"}:
            allowed = self.private and (
                (
                    scope == "session"
                    and self.session_hash
                    and row.get("session_id_hash") == self.session_hash
                )
                or (
                    scope == "agent"
                    and self.agent
                    and metadata.get("agent_id", row.get("agent_type")) == self.agent
                )
            )
            if not allowed:
                return "private_source"
            # Private visibility was checked above. All non-scope eligibility
            # checks still use the shared raw-observation policy unchanged.
            metadata = {**metadata, "scope": "repository_family"}
            row = {**row, "metadata_json": metadata}
        return observation_exclusion(row, self.workspace)

    def source_descriptor(self, source: dict[str, Any]) -> dict[str, str] | None:
        if source["source_kind"] == "observation":
            row = self.data["observations"].get(source.get("observation_id"))
            if row is None:
                return None
            metadata = metadata_object(row.get("metadata_json")) or {}
            scope = metadata.get("scope", "repository_family")
            if scope == "repository_family":
                key = self.family
            elif scope == "checkout":
                key = str(metadata.get("checkout_id", ""))
            elif scope == "session":
                key = str(row.get("session_id_hash", ""))
            else:
                key = str(metadata.get("agent_id", row.get("agent_type", "")))
            return {"scope": scope, "scope_key": key}
        row = self.data["artifacts"].get(source.get("source_artifact_id"))
        return None if row is None else {"scope": row["scope"], "scope_key": row["scope_key"]}

    def provenance_reason(
        self, artifact_id: str, seen: tuple[str, ...] = (), *, at: str | None = None
    ) -> str | None:
        if artifact_id in seen or len(seen) >= MAX_DEPTH:
            return "cyclic_or_deep_provenance"
        row = self.data["artifacts"].get(artifact_id)
        if row is None or not self.visible(row):
            return "out_of_scope"
        sources = self.data["sources"].get(artifact_id, [])
        if (
            row.get("schema_version") != 1
            or len(sources) != row.get("source_count")
            or not sources
        ):
            return "incomplete_sources"
        if artifact_content_hash(row, sources) != row.get("content_hash"):
            return "artifact_content_mismatch"
        for source in sources:
            descriptor = self.source_descriptor(source)
            if descriptor is None:
                return "source_forgotten"
            if descriptor["scope"] != "repository_family" and (
                descriptor["scope"] != row["scope"] or descriptor["scope_key"] != row["scope_key"]
            ):
                return "source_scope_widening"
            if source["source_kind"] == "observation":
                original = self.data["observations"].get(source.get("observation_id"))
                if original is None or self.raw_reason(original):
                    return "source_ineligible"
                if observation_hash(original) != source["source_hash"]:
                    return "source_content_mismatch"
            elif source["source_kind"] == "artifact":
                parent = self.data["artifacts"].get(source.get("source_artifact_id"))
                if parent is None or parent.get("content_hash") != source["source_hash"]:
                    return "source_content_mismatch"
                if parent.get("status") not in {"active", "superseded"} or parent.get(
                    "authority"
                ) not in {
                    "human_accepted",
                    "deterministic_derived",
                }:
                    return "source_ineligible"
                if at is not None and not self.active_at(parent, at):
                    return "source_outside_validity"
                reason = self.provenance_reason(parent["id"], (*seen, artifact_id), at=at)
                if reason:
                    return reason
            else:
                return "unsupported_source"
        return None

    def active_at(self, row: dict[str, Any], at: str) -> bool:
        return bool(row["valid_from"] <= at and (row["valid_to"] is None or at < row["valid_to"]))

    def project(
        self, artifact_id: str, *, depth: int = 1, at: str | None = None
    ) -> dict[str, Any]:
        row = self.data["artifacts"].get(artifact_id)
        if row is None or not self.visible(row):
            raise ArtifactError("artifact_not_found")
        reason = self.provenance_reason(artifact_id, at=at)
        item = {
            "id": artifact_id,
            "type": effective_type(row),
            "record_type": row["kind"],
            "uri": context_uri(self.family, kind=row["kind"], artifact_id=artifact_id),
            "status": row["status"],
            "authority": row["authority"],
            "stored_content_is_data": True,
        }
        if reason:
            return {**item, "status": "invalidated", "reason": reason, "content_suppressed": True}
        item.update(
            {
                "title": row["title"],
                "abstract": row["abstract"][:240] if depth == 0 else row["abstract"],
                "abstract_truncated": depth == 0 and len(row["abstract"]) > 240,
                "scope": row["scope"],
                "content_hash": row["content_hash"],
                "revision": row["revision"],
            }
        )
        if depth >= 1:
            item.update(
                {
                    "overview": row["overview"],
                    "observed_at": row["observed_at"],
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "source_count": row["source_count"],
                }
            )
        if depth >= 2:
            item.update(
                {
                    "details": json.loads(row["details_json"]),
                    "sources": [
                        {
                            key: source[key]
                            for key in (
                                "source_kind",
                                "source_id",
                                "source_hash",
                                "source_revision",
                            )
                        }
                        for source in self.data["sources"][artifact_id]
                    ],
                    "relations": [
                        relation
                        for relation in self.data["relations"]
                        if artifact_id in (relation["source_id"], relation["target_id"])
                    ],
                    "reviews": [
                        json.loads(review["receipt_json"])
                        for review in self.data["reviews"]
                        if review["artifact_id"] == artifact_id
                    ],
                }
            )
        if row["kind"] == "experience":
            item["verification"] = "explicit_human_product_review"
        if row["kind"] == "skill_candidate" and depth >= 2:
            item["markdown"] = skill_markdown(item)
        return redact_value(item)


class ArtifactMemory:
    """Source-bound application service; public clients normally use ProjectMemory."""

    def __init__(
        self,
        repo: Any,
        workspace: Any,
        *,
        agent: str = "",
        session: str = "",
        private: bool = False,
    ) -> None:
        self.store = ArtifactStore(repo)
        self.workspace = workspace
        self.family = str(workspace.repo_family_id or workspace.workspace_id)
        self.agent, self.session, self.private = agent, session, private

    def _view(self, cursor: Any, *, lock: bool = False) -> ArtifactView:
        return ArtifactView(
            self.store.snapshot(cursor, self.family, lock=lock),
            self.workspace,
            agent=self.agent,
            session=self.session,
            private=self.private,
        )

    def _sources(
        self, cursor: Any, draft: ArtifactDraft, view: ArtifactView, *, lock: bool = True
    ) -> list[dict[str, Any]]:
        ids = [source.id for source in draft.sources if source.kind == "observation"]
        view.data["observations"].update(self.store.observations(cursor, ids, lock=lock))
        records = []
        for source in draft.sources:
            raw = source.kind == "observation"
            row = view.data["observations" if raw else "artifacts"].get(source.id)
            if row is None:
                raise ArtifactError("source_not_found")
            if raw:
                if view.raw_reason(row) or row.get("event_type") == "context_injected":
                    raise ArtifactError("source_not_eligible")
                pinned_hash, revision = observation_hash(row), "raw-v1"
            else:
                if (
                    view.provenance_reason(source.id)
                    or row["status"] != "active"
                    or row["authority"] not in {"human_accepted", "deterministic_derived"}
                ):
                    raise ArtifactError("source_not_eligible")
                pinned_hash, revision = row["content_hash"], str(row["revision"])
            records.append(
                {
                    "artifact_id": "",
                    "source_kind": source.kind,
                    "source_id": source.id,
                    "source_hash": pinned_hash,
                    "source_revision": revision,
                    "observation_id": source.id if raw else None,
                    "source_artifact_id": None if raw else source.id,
                }
            )
        return records

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft = ArtifactDraft.parse(payload)
        if draft.kind not in {"fact", "lesson", "skill_candidate"}:
            raise ArtifactError("use_verified_type_constructor")
        return self._create(draft, deterministic=False)

    def episode(
        self,
        source_ids: list[str],
        *,
        title: str = "Observed coding episode",
        scope: str = "repository_family",
    ) -> dict[str, Any]:
        # A deterministic group describes its membership, not verified success.
        draft = ArtifactDraft.parse(
            {
                "kind": "episode",
                "title": title,
                "abstract": f"Episode containing {len(source_ids)} source observations.",
                "sources": [{"kind": "observation", "id": item} for item in source_ids],
                "scope": scope,
            }
        )
        return self._create(draft, deterministic=True)

    def _create(
        self,
        draft: ArtifactDraft,
        *,
        deterministic: bool,
        reviewed: tuple[ReviewGate, Any] | None = None,
    ) -> dict[str, Any]:
        if draft.scope in {"agent", "session"} and not self.private:
            raise ArtifactError("explicit_private_scope_required")
        key = _scope_key(self.workspace, draft.scope, self.agent, self.session)
        with self.store.transaction(write=True, family=self.family) as cursor:
            self.store.ensure_schema(cursor)
            view = self._view(cursor, lock=True)
            sources = self._sources(cursor, draft, view)
            learning_source_checks(draft, view.data)
            if draft.kind == "experience" and reviewed is None:
                raise ArtifactError("trusted_experience_verification_required")
            review_binding = None
            if reviewed is not None:
                gate, approval = reviewed
                review_binding = digest({"draft": asdict(draft), "sources": view.data})
                if gate.consume(approval, review_binding) != "accept":
                    return {"ok": True, "verified": False, "changed": False}
            proposal = digest(
                {
                    "family": self.family,
                    "scope": draft.scope,
                    "scope_key": key,
                    "kind": draft.kind,
                    "title": draft.title,
                    "abstract": draft.abstract,
                    "overview": draft.overview,
                    "details": draft.details,
                    "observed_at": draft.observed_at,
                    "valid_from": draft.valid_from,
                    "sources": sorted(
                        (item["source_kind"], item["source_id"], item["source_hash"])
                        for item in sources
                    ),
                }
            )
            for existing in view.data["artifacts"].values():
                if existing["proposal_hash"] == proposal:
                    return {
                        "ok": True,
                        "duplicate": True,
                        **({"verified": True} if reviewed is not None else {}),
                        "artifact": view.project(existing["id"]),
                    }
            if len(view.data["artifacts"]) >= MAX_ARTIFACTS:
                raise ArtifactError("artifact_capacity_exceeded")
            now = timestamp()
            row: dict[str, Any] = {
                "id": "mem_" + uuid.uuid4().hex,
                "repo_family_id": self.family,
                "scope": draft.scope,
                "scope_key": key,
                "kind": draft.kind,
                "authority": "human_accepted"
                if reviewed
                else ("deterministic_derived" if deterministic else "agent_proposed"),
                "status": "active" if (deterministic or reviewed) else "candidate",
                "title": draft.title,
                "abstract": draft.abstract,
                "overview": draft.overview,
                "details_json": draft.details,
                "observed_at": draft.observed_at or now,
                "valid_from": draft.valid_from or now,
                "valid_to": None,
                "created_at": now,
                "revision": 1,
                "schema_version": 1,
                "source_count": len(sources),
                "proposal_hash": proposal,
            }
            for source in sources:
                source["artifact_id"] = row["id"]
            row["content_hash"] = artifact_content_hash(row, sources)
            view.data["artifacts"][row["id"]] = row
            view.data["sources"][row["id"]] = sources
            reason = view.provenance_reason(row["id"])
            if reason:
                raise ArtifactError(reason)
            # Content + provenance idempotency. Never reinterpret a rejected
            # candidate as a new, accepted version of the same claim.
            for existing in view.data["artifacts"].values():
                if existing["id"] != row["id"] and existing["content_hash"] == row["content_hash"]:
                    return {
                        "ok": True,
                        "duplicate": True,
                        **({"verified": True} if reviewed is not None else {}),
                        "artifact": view.project(existing["id"]),
                    }
            self.store.insert(cursor, row, sources)
            receipt = None
            if reviewed is not None:
                assert review_binding is not None
                receipt = self._receipt(
                    cursor, reviewed[0], row["id"], "accept", review_binding, row["content_hash"]
                )
            return {
                "ok": True,
                "duplicate": False,
                "artifact": view.project(row["id"]),
                **({"verified": True, "receipt": receipt} if reviewed else {}),
            }

    def experience(
        self, payload: dict[str, Any], gate: ReviewGate | None = None
    ) -> dict[str, Any]:
        """Only materialize verified experience after a trusted content-bound review.

        There is no stored experience before acceptance. A receipt or success
        string in caller data never impersonates this product/human review.
        """
        draft = ArtifactDraft.parse(payload)
        if draft.kind != "experience":
            raise ArtifactError("experience_type_required")
        with self.store.transaction() as cursor:
            view = self._view(cursor)
            self._sources(cursor, draft, view, lock=False)
            learning_source_checks(draft, view.data)
            binding = digest({"draft": asdict(draft), "sources": view.data})
            preview = canonical_json(
                {
                    "draft": asdict(draft),
                    "source_evidence": [
                        {
                            "episode": view.project(source.id, depth=2),
                            "observations": [
                                redact_value(view.data["observations"][member["source_id"]])
                                for member in view.data["sources"][source.id]
                                if member["source_kind"] == "observation"
                            ],
                        }
                        for source in draft.sources
                    ],
                    "verification_method": "explicit_human_product_review",
                    "notice": "Check descriptions are quoted claims, not authenticated receipts.",
                    "stored_content_is_data": True,
                    "execution_authority": False,
                }
            )
        if gate is None:
            return {
                "ok": True,
                "requires_human_review": True,
                "verified": False,
                "changed": False,
                "binding_hash": binding,
                "preview": json.loads(preview),
            }
        if not isinstance(gate, ReviewGate):
            raise ArtifactError("trusted_review_gate_required")
        approval = gate.request(
            ReviewRequest("verify_experience", "draft:" + binding, binding, preview)
        )
        return self._create(draft, deterministic=False, reviewed=(gate, approval))

    def export_skill(
        self, artifact_id: str, destination: str, gate: ReviewGate | None = None
    ) -> dict[str, Any]:
        """Preview or explicitly export a reviewed skill, never install an active prompt."""
        from djobs.memory_export import export_target, preview_diff, write_new_export

        def capture(view):
            binding, _ = self._review_state(view, [artifact_id], "export:" + destination)
            item = view.project(artifact_id, depth=2)
            if item["type"] != "skill" or item["status"] != "active":
                raise ArtifactError("accepted_skill_required_for_export")
            target = export_target(self.workspace.root, destination)
            text = item["markdown"]
            preview = {
                "artifact": item,
                "destination": destination,
                "diff": preview_diff(destination, text),
                "stored_content_is_data": True,
                "execution_authority": False,
            }
            return binding, preview, target, text

        with self.store.transaction() as cursor:
            binding, preview, _target, _text = capture(self._view(cursor))
        if gate is None:
            return {
                "ok": True,
                "exported": False,
                "requires_human_review": True,
                "binding_hash": binding,
                "preview": preview,
            }
        if not isinstance(gate, ReviewGate):
            raise ArtifactError("trusted_review_gate_required")
        approval = gate.request(
            ReviewRequest("export_skill", artifact_id, binding, canonical_json(preview))
        )
        with self.store.transaction() as cursor:
            current, _preview, target, text = capture(self._view(cursor))
            if gate.consume(approval, current) != "accept":
                return {"ok": True, "exported": False}
            size = write_new_export(target, text)
        return {
            "ok": True,
            "exported": True,
            "destination": destination,
            "bytes": size,
            "file_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "execution_authority": False,
            "canonical_memory_unchanged": True,
        }

    def get(self, artifact_id: str, *, depth: int = 1) -> dict[str, Any]:
        if depth not in (0, 1, 2):
            raise ArtifactError("invalid_content_depth")
        folder = None
        if artifact_id.startswith("djobs:"):
            folder, resolved = parse_context_uri(artifact_id, self.family)
            if resolved is None:
                raise ArtifactError("context_item_required")
            artifact_id = resolved
        if artifact_id.startswith("imp_"):
            from djobs.session_memory import SessionMemory

            if folder is not None and folder != "imports":
                raise ArtifactError("context_category_mismatch")
            return SessionMemory(self.store.repo, self.workspace).get(artifact_id, depth=depth)
        with self.store.transaction() as cursor:
            item = self._view(cursor).project(artifact_id, depth=depth)
            if folder is not None and FOLDERS[item["record_type"]] != folder:
                raise ArtifactError("context_category_mismatch")
            return item

    def tree(
        self,
        *,
        uri: str | None = None,
        query: str = "",
        at: str | None = None,
        exposure: str = "resume",
        depth: int = 0,
        limit: int = 12,
        trace: bool = False,
    ) -> dict[str, Any]:
        """Read-through projection; no persistent file tree or alternate lifecycle state."""
        folder = None
        if uri:
            folder, item = parse_context_uri(uri, self.family)
            if item is not None:
                raise ArtifactError("context_folder_required")
        result = self.list_artifacts(
            query=query,
            at=at,
            exposure=exposure,
            depth=depth,
            limit=limit,
            folder=folder,
            explain=trace,
        )
        if exposure != "resume" and folder in {None, "imports"}:
            from djobs.session_memory import SessionMemory

            remaining = max(1, min(int(limit), 20)) - len(result["memories"])
            if remaining > 0:
                imported = SessionMemory(self.store.repo, self.workspace).list_imports(
                    depth=depth, limit=remaining, query=query
                )
                result["memories"].extend(imported["memories"])
                result["count"] = len(result["memories"])
                result["truncated"] |= imported["truncated"]
                if "trace" in result:
                    result["trace"]["selected_ids"] = [item["id"] for item in result["memories"]]
                    result["trace"]["projection_count"] = result["count"]
        seen = set()
        folders = []
        for kind, name in FOLDERS.items():
            if name in seen or (folder and folder != name):
                continue
            seen.add(name)
            folders.append(
                {
                    "name": name,
                    "uri": context_uri(self.family, kind=kind),
                    "shown_count": sum(
                        FOLDERS[item["record_type"]] == name for item in result["memories"]
                    ),
                }
            )
        return {
            **result,
            "root_uri": context_uri(self.family),
            "folders": folders,
            "content_depth": depth,
            "exposure": exposure,
            "storage_loading": "bounded_source_validating_snapshot",
            "persistent_projection": False,
        }

    def list_artifacts(
        self,
        *,
        kind: str | None = None,
        query: str = "",
        at: str | None = None,
        exposure: str = "resume",
        limit: int = 8,
        depth: int = 1,
        folder: str | None = None,
        explain: bool = False,
    ) -> dict[str, Any]:
        if exposure not in {"resume", "evidence", "audit", "candidates"} or depth not in (0, 1, 2):
            raise ArtifactError("invalid_artifact_projection")
        if folder is not None and folder not in FOLDERS.values():
            raise ArtifactError("unsupported_context_category")
        instant = timestamp(at)
        query = safe_text(query, 500)
        terms = lexical_terms(query)
        with self.store.transaction() as cursor:
            view = self._view(cursor)
            eligible = []
            filtered: dict[str, int] = {}
            scoped_count = 0
            for row in view.data["artifacts"].values():
                if not view.visible(row) or (kind and effective_type(row) != kind):
                    continue
                if folder is not None and FOLDERS[row["kind"]] != folder:
                    continue
                scoped_count += 1
                reason = view.provenance_reason(
                    row["id"], at=instant if exposure == "resume" else None
                )
                if reason:
                    filtered[reason] = filtered.get(reason, 0) + 1
                    continue
                if exposure == "resume":
                    if row["authority"] not in {"human_accepted", "deterministic_derived"}:
                        continue
                    if row["status"] not in ({"active", "superseded"} if at else {"active"}):
                        continue
                    if not view.active_at(row, instant):
                        continue
                elif exposure == "candidates" and row["status"] != "candidate":
                    continue
                text = (row["title"] + " " + row["abstract"]).casefold()
                matched = sum(term in text for term in terms)
                if terms and not matched and query.casefold() not in text:
                    continue
                eligible.append((matched, row))
            eligible.sort(key=lambda pair: (-pair[0], pair[1]["valid_from"], pair[1]["id"]))
            allowed_ids = {row["id"] for _, row in eligible}
            conflicts = []
            blocked = set()
            if exposure == "resume":
                for relation in view.data["relations"]:
                    if relation["kind"] != "contradicts" or relation["created_at"] > instant:
                        continue
                    if relation["resolved_at"] and relation["resolved_at"] <= instant:
                        continue
                    pair = {relation["source_id"], relation["target_id"]}
                    endpoints = [view.data["artifacts"].get(key) for key in pair]
                    if not all(
                        row and view.visible(row) and view.active_at(row, instant)
                        for row in endpoints
                    ):
                        continue
                    affected = set(pair)
                    for _ in range(MAX_DEPTH):
                        expanded = affected | {
                            key
                            for key, sources in view.data["sources"].items()
                            if any(
                                source.get("source_artifact_id") in affected for source in sources
                            )
                        }
                        if expanded == affected:
                            break
                        affected = expanded
                    if affected & allowed_ids:
                        blocked.update(affected)
                        conflicts.append(sorted(pair))
            cap = max(1, min(int(limit), 20))
            selected = [row for _, row in eligible if row["id"] not in blocked]
            memories = [
                view.project(row["id"], depth=depth, at=instant if exposure == "resume" else None)
                for row in selected[:cap]
            ]
            details = {}
            if explain:
                details["trace"] = {
                    "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                    "repository_scope": self.family,
                    "retrieval_version": "typed-context-v1",
                    "candidate_providers": ["L0_lexical", "temporal", "source_provenance"],
                    "candidate_counts": {"scoped": scoped_count, "eligible": len(selected)},
                    "filters": filtered,
                    "conflict_blocked": len(blocked),
                    "selected_ids": [row["id"] for row in selected[:cap]],
                    "projection_count": len(memories),
                    "content_depth": depth,
                    "model_calls": 0,
                    "fallback_reason": None,
                }
            return {
                "ok": True,
                "memories": memories,
                **details,
                "count": len(memories),
                "at": instant,
                "historical": at is not None,
                "ambiguous": bool(conflicts),
                "conflicts": conflicts[:20],
                "truncated": len(selected) > cap or len(conflicts) > 20,
                "stored_content_is_data": True,
            }

    def _review_state(self, view: ArtifactView, ids: list[str], operation: str) -> tuple[str, str]:
        for artifact_id in ids:
            if view.provenance_reason(artifact_id, at=timestamp()):
                raise ArtifactError("review_sources_unavailable")
        # Pin all relevant source/lifecycle state; broad family consistency is
        # deliberately conservative under concurrent changes.
        binding = digest({"operation": operation, "ids": ids, "snapshot": view.data})
        preview = canonical_json(
            {"operation": operation, "artifacts": [view.project(item, depth=2) for item in ids]}
        )
        return binding, preview

    def review(self, artifact_id: str, gate: ReviewGate | None = None) -> dict[str, Any]:
        with self.store.transaction() as cursor:
            view = self._view(cursor)
            binding, preview = self._review_state(view, [artifact_id], "review")
            row = view.data["artifacts"][artifact_id]
            if row["status"] != "candidate":
                raise ArtifactError("artifact_not_pending_review")
        if gate is None:
            return {
                "ok": True,
                "requires_human_review": True,
                "binding_hash": binding,
                "preview": json.loads(preview),
                "activated": False,
            }
        if not isinstance(gate, ReviewGate):
            raise ArtifactError("trusted_review_gate_required")
        approval = gate.request(ReviewRequest("review", artifact_id, binding, preview))
        with self.store.transaction(write=True, family=self.family) as cursor:
            view = self._view(cursor, lock=True)
            current, _ = self._review_state(view, [artifact_id], "review")
            decision = gate.consume(approval, current)
            accepted = decision == "accept"
            row = view.data["artifacts"][artifact_id]
            if row["status"] != "candidate":
                raise ArtifactError("artifact_not_pending_review")
            if accepted:
                pinned = {
                    source["observation_id"]
                    for values in view.data["sources"].values()
                    for source in values
                    if source["observation_id"] is not None
                }
                if len(pinned) > 512:
                    raise ArtifactError("provenance_retention_capacity")
            self.store.execute(
                cursor,
                "UPDATE memory_artifacts SET status=?,authority=?,revision=revision+1 "
                "WHERE id=? AND revision=?",
                (
                    "active" if accepted else "rejected",
                    "human_accepted" if accepted else "human_reviewed",
                    artifact_id,
                    row["revision"],
                ),
            )
            receipt = self._receipt(
                cursor, gate, artifact_id, decision, current, row["content_hash"]
            )
            return {"ok": True, "activated": accepted, "receipt": receipt}

    def _receipt(
        self,
        cursor: Any,
        gate: ReviewGate,
        artifact_id: str,
        decision: str,
        binding: str,
        content_hash: str,
    ) -> dict[str, Any]:
        receipt = {
            "id": "review_" + uuid.uuid4().hex,
            "artifact_id": artifact_id,
            "decision": decision,
            "reviewer": gate.reviewer,
            "policy": gate.policy,
            "binding_hash": binding,
            "content_hash": content_hash,
            "created_at": timestamp(),
            "execution_authority": False,
            "integrity_is_authentication": False,
        }
        receipt["receipt_hash"] = digest(receipt)
        self.store.review_receipt(
            cursor,
            {
                key: receipt[key]
                for key in (
                    "id",
                    "artifact_id",
                    "decision",
                    "reviewer",
                    "policy",
                    "binding_hash",
                    "created_at",
                )
            }
            | {"receipt_json": canonical_json(receipt)},
        )
        return receipt

    def relate(
        self,
        source_id: str,
        target_id: str,
        kind: str,
        *,
        at: str | None = None,
        gate: ReviewGate | None = None,
    ) -> dict[str, Any]:
        if kind not in {"supersedes", "contradicts"} or source_id == target_id:
            raise ArtifactError("invalid_temporal_relation")
        instant = timestamp(at)
        ids = [source_id, target_id]
        operation = canonical_json({"kind": kind, "at": instant})
        with self.store.transaction() as cursor:
            view = self._view(cursor)
            binding, preview = self._review_state(view, ids, operation)
        if gate is None:
            return {
                "ok": True,
                "requires_human_review": True,
                "preview": json.loads(preview),
                "binding_hash": binding,
                "changed": False,
            }
        if not isinstance(gate, ReviewGate):
            raise ArtifactError("trusted_review_gate_required")
        approval = gate.request(ReviewRequest(operation, source_id, binding, preview))
        with self.store.transaction(write=True, family=self.family) as cursor:
            view = self._view(cursor, lock=True)
            current, _ = self._review_state(view, ids, operation)
            decision = gate.consume(approval, current)
            a, b = (view.data["artifacts"][key] for key in ids)
            if any(
                row["kind"] != "fact"
                or row["status"] != "active"
                or row["authority"] != "human_accepted"
                for row in (a, b)
            ):
                raise ArtifactError("relation_requires_accepted_facts")
            if (a["scope"], a["scope_key"]) != (b["scope"], b["scope_key"]):
                raise ArtifactError("relation_scope_mismatch")
            if not all(view.active_at(row, instant) for row in (a, b)):
                raise ArtifactError("relation_outside_validity")
            if decision == "reject":
                return {
                    "ok": True,
                    "changed": False,
                    "receipt": self._receipt(
                        cursor, gate, source_id, "reject:" + kind, current, a["content_hash"]
                    ),
                }
            if kind == "supersedes":
                if instant != a["valid_from"] or instant <= b["valid_from"]:
                    raise ArtifactError("supersession_must_start_at_replacement_validity")
                self.store.execute(
                    cursor,
                    "UPDATE memory_artifacts SET valid_to=?,status='superseded',"
                    "revision=revision+1 WHERE id=?",
                    (instant, target_id),
                )
                self.store.execute(
                    cursor,
                    "UPDATE memory_relations SET resolved_at=? "
                    "WHERE kind='contradicts' AND resolved_at IS NULL AND "
                    "((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))",
                    (instant, source_id, target_id, target_id, source_id),
                )
            self.store.execute(
                cursor,
                "INSERT INTO memory_relations(source_id,target_id,kind,created_at) "
                "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (source_id, target_id, kind, instant),
            )
            receipt = self._receipt(
                cursor, gate, source_id, "accept:" + kind, current, a["content_hash"]
            )
            return {"ok": True, "changed": True, "receipt": receipt}

    def forget(self, artifact_id: str) -> bool:
        with self.store.transaction(write=True, family=self.family) as cursor:
            view = self._view(cursor, lock=True)
            row = view.data["artifacts"].get(artifact_id)
            if row is None or not view.visible(row):
                return False
            doomed = {artifact_id}
            for _ in range(MAX_DEPTH):
                expanded = doomed | {
                    key
                    for key, sources in view.data["sources"].items()
                    if any(source.get("source_artifact_id") in doomed for source in sources)
                }
                if expanded == doomed:
                    break
                doomed = expanded
            else:
                raise ArtifactError("deep_provenance_requires_maintenance")
            delete_artifacts(cursor, self.store.sqlite, sorted(doomed))
            return True
