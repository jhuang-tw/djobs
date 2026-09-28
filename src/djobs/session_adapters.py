"""Bounded text-only session adapters from inspected official format sources.

No home-directory discovery, external history following, tool execution, harness
permission restoration, or claim of full-fidelity native session resumption.
Unknown content blocks are counted, not interpreted as instructions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol

from djobs.memory_artifacts import ArtifactError, canonical_json
from djobs.privacy import REDACTION_VERSION, redact_text

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 4096
MAX_SELECTION = 32
MAX_TEXT = 8000
ADAPTER_VERSION = "djobs-session-text-v1"
_DENIED = {"auth.json", "credentials.json", "config.json", "settings.json", "package.json"}


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ArtifactError("invalid_session_identity")
    if redact_text(value) != value:
        raise ArtifactError("sensitive_session_identity")
    return value


def _object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ArtifactError("session_record_object_required")
    if "schema_version" in data and data["schema_version"] != 1:
        raise ArtifactError("unsupported_session_schema")
    return data


def _text(content: Any) -> tuple[str, int, bool]:
    skipped = 0
    if isinstance(content, str):
        value = content
    elif isinstance(content, list) and len(content) <= 256:
        pieces = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") in {"text", "input_text", "output_text"}
                and isinstance(block.get("text"), str)
            ):
                pieces.append(block["text"])
            else:
                skipped += 1
        value = "\n".join(pieces)
    else:
        raise ArtifactError("unsupported_message_content")
    safe = redact_text(value).replace("\x00", "")
    return safe[:MAX_TEXT], skipped, len(safe) > MAX_TEXT


def _message(
    identity: Any, role: Any, content: Any, parent: Any = None
) -> tuple[dict | None, int]:
    if role not in {"user", "assistant"}:
        return None, 1
    text, skipped, truncated = _text(content)
    if not text.strip():
        return None, skipped + 1
    return {
        "id": _identifier(identity),
        "role": role,
        "text": text,
        "parent_id": _identifier(parent) if parent is not None else None,
        "text_truncated": truncated,
    }, skipped


def _result(
    harness: str,
    format_name: str,
    session: Any,
    messages: list,
    skipped: int,
    history_references: bool = False,
) -> dict[str, Any]:
    if not 1 <= len(messages) <= MAX_RECORDS:
        raise ArtifactError("empty_or_oversized_session")
    ids = [item["id"] for item in messages]
    if len(set(ids)) != len(ids):
        raise ArtifactError("duplicate_message_identity")
    return {
        "schema": "djobs.session.v1",
        "source_harness": harness,
        "source_format_version": format_name,
        "source_session_id": _identifier(session),
        "adapter_version": ADAPTER_VERSION,
        "redaction_version": REDACTION_VERSION,
        "messages": messages,
        "omitted_records_or_blocks": skipped,
        "external_history_not_followed": history_references,
        "history_interpretation": "selected records, not a reconstructed active branch",
        "native_resumption_supported": False,
        "stored_content_is_data": True,
    }


def _lines(raw: bytes) -> list[dict]:
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ArtifactError("session_file_bound")
    lines = raw.decode("utf-8-sig").splitlines()
    if len(lines) > MAX_RECORDS:
        raise ArtifactError("session_record_bound")
    return [_object(json.loads(line)) for line in lines if line.strip()]


class SessionAdapter(Protocol):
    name: str

    def parse(self, raw: bytes) -> dict[str, Any]: ...


class ClaudeSessionAdapter:
    name = "claude"

    def parse(self, raw: bytes) -> dict[str, Any]:
        messages, sessions = [], set()
        skipped = 0
        for record in _lines(raw):
            if record.get("sessionId"):
                sessions.add(_identifier(record["sessionId"]))
            if record.get("type") not in {"user", "assistant"}:
                skipped += 1
                continue
            body = _object(record.get("message"))
            if body.get("role", record["type"]) != record["type"]:
                raise ArtifactError("session_role_mismatch")
            item, count = _message(
                record.get("uuid"), record["type"], body.get("content"), record.get("parentUuid")
            )
            skipped += count
            if item:
                messages.append(item)
        if len(sessions) != 1:
            raise ArtifactError("single_source_session_required")
        return _result(
            self.name,
            "claude-transcript-jsonl-inspected-20260928",
            sessions.pop(),
            messages,
            skipped,
        )


class CodexSessionAdapter:
    name = "codex"

    def parse(self, raw: bytes) -> dict[str, Any]:
        records = _lines(raw)
        headers = [
            _object(record.get("payload"))
            for record in records
            if record.get("type") == "session_meta"
        ]
        if len(headers) != 1:
            raise ArtifactError("single_codex_session_header_required")
        header = headers[0]
        messages = []
        skipped = 0
        for ordinal, record in enumerate(records):
            if record.get("type") == "session_meta":
                continue
            payload = record.get("payload")
            if (
                record.get("type") != "response_item"
                or not isinstance(payload, dict)
                or payload.get("type") != "message"
            ):
                skipped += 1
                continue
            item, count = _message(
                payload.get("id") or f"record-{ordinal}",
                payload.get("role"),
                payload.get("content"),
            )
            skipped += count
            if item:
                messages.append(item)
        return _result(
            self.name,
            "codex-rollout-jsonl-inspected-20260928",
            header.get("id"),
            messages,
            skipped,
            bool(header.get("history_base") or header.get("forked_from_id")),
        )


class OpenCodeSessionAdapter:
    name = "opencode"

    def parse(self, raw: bytes) -> dict[str, Any]:
        data = _object(json.loads(raw))
        info = _object(data.get("info"))
        session = _identifier(info.get("id"))
        records = data.get("messages")
        if not isinstance(records, list) or len(records) > MAX_RECORDS:
            raise ArtifactError("opencode_messages_required")
        messages = []
        skipped = 0
        for record in records:
            record = _object(record)
            message = _object(record.get("info"))
            if message.get("sessionID", session) != session:
                raise ArtifactError("source_session_mismatch")
            item, count = _message(
                message.get("id"),
                message.get("role"),
                record.get("parts"),
                message.get("parentID"),
            )
            skipped += count
            if item:
                messages.append(item)
        return _result(
            self.name,
            "opencode-info-messages-json-inspected-20260928",
            session,
            messages,
            skipped,
            bool(info.get("parentID")),
        )


class PortableSessionAdapter:
    name = "djobs"

    def parse(self, raw: bytes) -> dict[str, Any]:
        data = _object(json.loads(raw))
        if data.get("schema") != "djobs.session.v1":
            raise ArtifactError("unsupported_portable_session_schema")
        allowed = {
            "schema",
            "source_harness",
            "source_format_version",
            "source_session_id",
            "adapter_version",
            "redaction_version",
            "messages",
            "omitted_records_or_blocks",
            "external_history_not_followed",
            "history_interpretation",
            "native_resumption_supported",
            "stored_content_is_data",
            "selection_is_partial",
        }
        if set(data) - allowed:
            raise ArtifactError("portable_bundle_contains_unsupported_fields")
        if not isinstance(data.get("messages"), list) or len(data["messages"]) > MAX_RECORDS:
            raise ArtifactError("portable_messages_required")
        messages = []
        for value in data["messages"]:
            value = _object(value)
            if set(value) - {"id", "role", "text", "parent_id", "text_truncated"}:
                raise ArtifactError("portable_message_fields_invalid")
            item, _ = _message(
                value.get("id"), value.get("role"), value.get("text"), value.get("parent_id")
            )
            if item:
                item["text_truncated"] |= bool(value.get("text_truncated"))
                messages.append(item)
        result = _result(
            _identifier(data.get("source_harness")),
            _identifier(data.get("source_format_version")),
            data.get("source_session_id"),
            messages,
            int(data.get("omitted_records_or_blocks", 0)),
            bool(data.get("external_history_not_followed")),
        )
        result["selection_is_partial"] = bool(data.get("selection_is_partial", False))
        return result


ADAPTERS: dict[str, SessionAdapter] = {
    item.name: item
    for item in (
        ClaudeSessionAdapter(),
        CodexSessionAdapter(),
        OpenCodeSessionAdapter(),
        PortableSessionAdapter(),
    )
}


def selected_document(raw: bytes, harness: str, selected_ids: list[str]) -> dict[str, Any]:
    if not raw or len(raw) > MAX_FILE_BYTES or harness not in ADAPTERS:
        raise ArtifactError("unsupported_or_oversized_session_file")
    if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= MAX_SELECTION:
        raise ArtifactError("explicit_bounded_message_selection_required")
    wanted = {_identifier(value) for value in selected_ids}
    if len(wanted) != len(selected_ids):
        raise ArtifactError("duplicate_selection")
    result = ADAPTERS[harness].parse(raw)
    if wanted - {item["id"] for item in result["messages"]}:
        raise ArtifactError("selected_message_not_found")
    result["selection_is_partial"] = bool(result.get("selection_is_partial")) or len(
        wanted
    ) != len(result["messages"])
    result["messages"] = [item for item in result["messages"] if item["id"] in wanted]
    if len(canonical_json(result).encode()) > 96000:
        raise ArtifactError("selected_session_payload_bound")
    return result


def _real_directory(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ArtifactError("real_session_directory_required")


def session_file(root: str, relative: str) -> Path:
    base = Path(root).expanduser().absolute()
    _real_directory(base)
    path = Path(relative.replace("\\", "/"))
    if (
        path.is_absolute()
        or PureWindowsPath(relative).drive
        or ":" in relative
        or any(part in {"..", "."} for part in path.parts)
        or path.name.casefold() in _DENIED
        or path.name.startswith(".")
        or path.suffix.casefold() not in {".json", ".jsonl"}
    ):
        raise ArtifactError("explicit_session_file_required")
    current = base
    for part in path.parts[:-1]:
        current /= part
        _real_directory(current)
    target = base / path
    info = target.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not target.resolve().is_relative_to(base.resolve())
    ):
        raise ArtifactError("real_session_file_required")
    return target


def read_session_file(root: str, relative: str) -> tuple[bytes, str]:
    path = session_file(root, relative)
    before = path.stat()
    if not 1 <= before.st_size <= MAX_FILE_BYTES:
        raise ArtifactError("session_file_bound")
    with path.open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ArtifactError("session_changed_during_read")
    if len(raw) != before.st_size:
        raise ArtifactError("session_changed_during_read")
    return raw, hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def discover_sessions(root: str) -> dict[str, Any]:
    base = Path(root).expanduser().absolute()
    _real_directory(base)
    stack, items, visited = [(base, 0)], [], 0
    while stack and visited < 256:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 256:
                    break
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
                if entry.name.startswith(".") or entry.name.casefold() in _DENIED:
                    continue
                if entry.is_dir(follow_symlinks=False) and depth < 3:
                    stack.append((Path(entry.path), depth + 1))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.casefold() in {".json", ".jsonl"}
                    and 1 <= info.st_size <= MAX_FILE_BYTES
                ):
                    items.append(
                        {
                            "relative_path": redact_text(str(Path(entry.path).relative_to(base))),
                            "bytes": info.st_size,
                            "format_validated": False,
                        }
                    )
    return {
        "ok": True,
        "files": sorted(items, key=lambda item: item["relative_path"]),
        "truncated": bool(stack) or visited > 256,
        "content_read": False,
        "source_root_fingerprint": hashlib.sha256(str(base.resolve()).encode()).hexdigest(),
    }
