"""Native quarantined session memory. No raw/typed recovery authority is granted.

The same djobs database owns these selected text imports. Native observations,
accepted facts and task leases are untouched. Reviewing a transcript means
reviewed_reference, not verified truth or permission to resume a foreign agent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from djobs.memory_artifacts import ArtifactError, canonical_json, digest, timestamp
from djobs.memory_projection import context_uri
from djobs.memory_review import ReviewGate, ReviewRequest
from djobs.privacy import redact_value
from djobs.session_adapters import ADAPTER_VERSION, ADAPTERS, read_session_file, selected_document
from djobs.storage.artifacts import ArtifactStore, table_exists
from djobs.storage.schema import MEMORY_SESSION_IMPORT_SCHEMA_SQL

MAX_IMPORTS = 128


class SessionMemory:
    def __init__(self, repo: Any, workspace: Any):
        self.store = ArtifactStore(repo)
        self.workspace = workspace
        self.family = str(workspace.repo_family_id or workspace.workspace_id)

    def _version(self, cursor) -> int | None:
        if not table_exists(cursor, self.store.sqlite, "djobs_memory_schema"):
            return None
        row = self.store.execute(
            cursor,
            "SELECT version FROM djobs_memory_schema WHERE component=?",
            ("session_imports",),
        ).fetchone()
        if row is None:
            return None
        if row["version"] != 1:
            raise ArtifactError("unsupported_session_import_schema")
        return 1

    def _ensure(self, cursor) -> None:
        if self._version(cursor) is not None:
            return
        for statement in MEMORY_SESSION_IMPORT_SCHEMA_SQL.split(";"):
            if statement.strip():
                cursor.execute(statement)
        self.store.execute(
            cursor,
            "INSERT INTO djobs_memory_schema(component,version) VALUES (?,?) "
            "ON CONFLICT(component) DO NOTHING",
            ("session_imports", 1),
        )

    def _rows(self, cursor, *, import_id: str | None = None) -> list[dict]:
        if self._version(cursor) is None:
            return []
        values: tuple = (self.family,)
        where = "repo_family_id=?"
        if import_id is not None:
            where += " AND id=?"
            values += (import_id,)
        rows = self.store.execute(
            cursor,
            "SELECT * FROM memory_session_imports WHERE "
            + where
            + " ORDER BY imported_at,id LIMIT ?",
            (*values, MAX_IMPORTS + 1),
        ).fetchall()
        if len(rows) > MAX_IMPORTS:
            raise ArtifactError("session_import_capacity_exceeded")
        return [dict(row) for row in rows]

    def _document(self, row: dict) -> dict:
        raw = row["payload_json"]
        if (
            len(raw.encode()) > 96000
            or row["content_hash"] != hashlib.sha256(raw.encode()).hexdigest()
        ):
            raise ArtifactError("session_import_content_mismatch")
        document = json.loads(raw)
        if (
            document.get("schema") != "djobs.session.v1"
            or document.get("stored_content_is_data") is not True
        ):
            raise ArtifactError("unsupported_session_import_payload")
        if row["status"] not in {"imported_unverified", "reviewed_reference", "rejected"}:
            raise ArtifactError("invalid_session_import_lifecycle")
        return document

    def _item(self, row: dict, *, depth: int = 0) -> dict:
        if depth not in {0, 1, 2}:
            raise ArtifactError("invalid_session_projection_depth")
        doc = self._document(row)
        item = {
            key: row[key]
            for key in (
                "id",
                "repo_family_id",
                "source_harness",
                "source_format_version",
                "source_session_id",
                "source_content_hash",
                "adapter_version",
                "imported_at",
                "redaction_version",
                "source_path_fingerprint",
                "content_hash",
                "status",
                "selection_hash",
            )
        }
        item.update(
            {
                "type": "imported_session",
                "record_type": "imported_session",
                "uri": context_uri(self.family, kind="imported_session", artifact_id=row["id"]),
                "scope": "session/import",
                "authority": "externally_derived",
                "message_count": len(doc["messages"]),
                "stored_content_is_data": True,
                "normal_resume_eligible": False,
                "execution_authority": False,
            }
        )
        if depth >= 1:
            item["excerpts"] = [
                {"id": value["id"], "role": value["role"], "text": value["text"][:240]}
                for value in doc["messages"]
            ]
            item["selection_is_partial"] = doc.get("selection_is_partial", False)
            item["external_history_not_followed"] = doc["external_history_not_followed"]
            item["omitted_records_or_blocks"] = doc["omitted_records_or_blocks"]
        if depth == 2:
            item["document"] = doc
            item["review_receipt"] = json.loads(row["review_json"]) if row["review_json"] else None
        return redact_value(item)

    def list_imports(self, *, depth: int = 0, limit: int = 12, query: str = "") -> dict[str, Any]:
        with self.store.transaction() as cursor:
            rows = self._rows(cursor)
            if query:
                safe_query = str(redact_value(query)).casefold()[:500]
                rows = [
                    row
                    for row in rows
                    if safe_query
                    in (row["source_harness"] + " " + row["source_session_id"]).casefold()
                ]
            selected = rows[: max(1, min(int(limit), 20))]
            items, corrupt = [], 0
            for row in selected:
                try:
                    items.append(self._item(row, depth=depth))
                except (ArtifactError, ValueError, TypeError):
                    corrupt += 1
        return {
            "ok": True,
            "memories": items,
            "count": len(items),
            "truncated": len(rows) > len(selected),
            "corrupt_items_suppressed": corrupt,
            "stored_content_is_data": True,
            "normal_resume_eligible": False,
        }

    def get(self, import_id: str, *, depth: int = 2) -> dict:
        with self.store.transaction() as cursor:
            rows = self._rows(cursor, import_id=import_id)
            if not rows:
                raise ArtifactError("session_import_not_found")
            return self._item(rows[0], depth=depth)

    def preview(
        self, root: str, relative: str, harness: str, selected_ids: list[str] | None = None
    ) -> dict[str, Any]:
        raw, path_hash = read_session_file(root, relative)
        if harness not in ADAPTERS:
            raise ArtifactError("unsupported_session_adapter")
        parsed = ADAPTERS[harness].parse(raw)
        if selected_ids is None:
            return {
                "ok": True,
                "repo_family_id": self.family,
                "selection_required": True,
                "messages": [
                    {"id": item["id"], "role": item["role"], "excerpt": item["text"][:240]}
                    for item in parsed["messages"][:32]
                ],
                "total_messages": len(parsed["messages"]),
                "omitted_records_or_blocks": parsed["omitted_records_or_blocks"],
                "external_history_not_followed": parsed["external_history_not_followed"],
                "stored_content_is_data": True,
                "changed": False,
            }
        document = selected_document(raw, harness, selected_ids)
        encoded = canonical_json(document)
        file_hash = hashlib.sha256(raw).hexdigest()
        content_hash = hashlib.sha256(encoded.encode()).hexdigest()
        selection_hash = digest({"family": self.family, "document": document})
        binding = digest(
            {
                "family": self.family,
                "file_hash": file_hash,
                "path_hash": path_hash,
                "selection_hash": selection_hash,
                "adapter": ADAPTER_VERSION,
            }
        )
        with self.store.transaction() as cursor:
            rows = self._rows(cursor)
            duplicate = next(
                (row["id"] for row in rows if row["selection_hash"] == selection_hash), None
            )
        return {
            "ok": True,
            "changed": False,
            "status": "imported_unverified",
            "repo_family_id": self.family,
            "document": document,
            "content_hash": content_hash,
            "source_content_hash": file_hash,
            "source_path_fingerprint": path_hash,
            "selection_hash": selection_hash,
            "binding_hash": binding,
            "duplicate": duplicate is not None,
            "duplicate_id": duplicate,
            "stored_content_is_data": True,
            "normal_resume_eligible": False,
        }

    def import_session(
        self,
        root: str,
        relative: str,
        harness: str,
        selected_ids: list[str],
        *,
        expected_hash: str,
        expected_family: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview(root, relative, harness, selected_ids)
        if not confirm:
            return preview
        if expected_family != self.family or expected_hash != preview["binding_hash"]:
            raise ArtifactError("session_import_preview_or_repository_changed")
        document = preview["document"]
        now = timestamp()
        row = {
            "id": "imp_" + uuid.uuid4().hex,
            "repo_family_id": self.family,
            "source_harness": document["source_harness"],
            "source_format_version": document["source_format_version"],
            "source_session_id": document["source_session_id"],
            "source_content_hash": preview["source_content_hash"],
            "adapter_version": ADAPTER_VERSION,
            "imported_at": now,
            "redaction_version": document["redaction_version"],
            "source_path_fingerprint": preview["source_path_fingerprint"],
            "content_hash": preview["content_hash"],
            "selection_hash": preview["selection_hash"],
            "status": "imported_unverified",
            "payload_json": canonical_json(document),
            "review_json": None,
        }
        with self.store.transaction(write=True, family=self.family) as cursor:
            self._ensure(cursor)
            rows = self._rows(cursor)
            duplicate = next(
                (item for item in rows if item["selection_hash"] == row["selection_hash"]), None
            )
            if duplicate:
                return {
                    "ok": True,
                    "duplicate": True,
                    "changed": False,
                    "import": self._item(duplicate),
                }
            if len(rows) >= MAX_IMPORTS:
                raise ArtifactError("session_import_capacity_exceeded")
            columns = tuple(row)
            self.store.execute(
                cursor,
                "INSERT INTO memory_session_imports ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                tuple(row.values()),
            )
        return {
            "ok": True,
            "duplicate": False,
            "changed": True,
            "import": self._item(row),
            "tasks_untouched": True,
            "normal_resume_eligible": False,
        }

    def review(self, import_id: str, gate: ReviewGate | None = None) -> dict:
        item = self.get(import_id, depth=2)
        if item["status"] != "imported_unverified":
            raise ArtifactError("import_not_pending_review")
        binding = digest(item)
        preview = {
            "import": item,
            "review_effect": "reviewed_reference, not truth or normal resume",
            "stored_content_is_data": True,
            "execution_authority": False,
        }
        if gate is None:
            return {
                "ok": True,
                "requires_human_review": True,
                "preview": preview,
                "binding_hash": binding,
            }
        if not isinstance(gate, ReviewGate):
            raise ArtifactError("trusted_review_gate_required")
        approval = gate.request(
            ReviewRequest("review_import", import_id, binding, canonical_json(preview))
        )
        with self.store.transaction(write=True, family=self.family) as cursor:
            rows = self._rows(cursor, import_id=import_id)
            if not rows:
                raise ArtifactError("session_import_not_found")
            current = self._item(rows[0], depth=2)
            decision = gate.consume(approval, digest(current))
            status = "reviewed_reference" if decision == "accept" else "rejected"
            receipt = {
                "decision": decision,
                "reviewer": gate.reviewer,
                "binding_hash": binding,
                "reviewed_at": timestamp(),
                "normal_resume_eligible": False,
                "execution_authority": False,
                "content_hash": item["content_hash"],
            }
            self.store.execute(
                cursor,
                "UPDATE memory_session_imports SET status=?,review_json=? "
                "WHERE repo_family_id=? AND id=?",
                (status, canonical_json(receipt), self.family, import_id),
            )
        return {"ok": True, "status": status, "receipt": receipt, "normal_resume_eligible": False}

    def export(self, import_id: str) -> dict:
        item = self.get(import_id, depth=2)
        return {
            "ok": True,
            "document": item["document"],
            "stored_content_is_data": True,
            "native_resumption_supported": False,
            "execution_authority": False,
        }

    def forget(self, import_id: str) -> bool:
        with self.store.transaction(write=True, family=self.family) as cursor:
            if self._version(cursor) is None:
                return False
            self.store.execute(
                cursor,
                "DELETE FROM memory_session_imports WHERE repo_family_id=? AND id=?",
                (self.family, import_id),
            )
            return cursor.rowcount == 1

    def clear(self) -> int:
        with self.store.transaction(write=True, family=self.family) as cursor:
            if self._version(cursor) is None:
                return 0
            self.store.execute(
                cursor, "DELETE FROM memory_session_imports WHERE repo_family_id=?", (self.family,)
            )
            return cursor.rowcount
