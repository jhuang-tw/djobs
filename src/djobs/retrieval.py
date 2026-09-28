"""Explainable native retrieval with an offline lexical default and optional RRF.

No retrieval result can activate a fact, skill, task or lease. Index maintenance
is separate from this SELECT-only retrieval service; failures retain lexical
results and expose a bounded, non-sensitive fallback reason.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from djobs.embedding import (
    EmbeddingSession,
    ProviderUnavailableError,
    UnavailableEmbeddingProvider,
)
from djobs.memory_policy import (
    POLICY_VERSION,
    coding_entities,
    content_hash,
    embedding_text,
    lexical_terms,
    metadata_object,
    observation_exclusion,
)
from djobs.privacy import redact_text
from djobs.ranking import RankedMemory, rank_memory_rows
from djobs.storage.memory import memory_repository
from djobs.storage.retrieval import RetrievalIndex, pack_vector

RETRIEVAL_VERSION = "djobs-rrf-v2-strong-lexical-k60"
RRF_K = 60
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "why",
        "with",
        "we",
        "can",
        "has",
        "have",
        "its",
        "our",
        "your",
        "into",
        "than",
        "then",
    ]
)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    items: list[dict[str, Any]]
    trace: dict[str, Any]


def reciprocal_rank_fusion(channels: dict[str, list[str]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ids in channels.values():
        for rank, record_id in enumerate(dict.fromkeys(ids), 1):
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def _scope(workspace: Any) -> tuple[str, tuple[str, ...]]:
    family = str(getattr(workspace, "repo_family_id", "") or workspace.workspace_id)
    scopes = getattr(workspace, "memory_correlation_ids", ()) or workspace.correlation_ids
    return family, tuple(dict.fromkeys(str(value) for value in scopes))


def _eligible(
    rows: list[dict[str, Any]], workspace: Any
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    result = []
    rejected: Counter[str] = Counter()
    for row in rows:
        reason = observation_exclusion(row, workspace)
        if reason is None:
            result.append(row)
        else:
            rejected[reason] += 1
    return result, dict(sorted(rejected.items()))


def _fts_query(query: str) -> str:
    # CJK bigrams supplement the bounded scan when FTS word boundaries differ.
    return " OR ".join('"' + term.replace('"', "") + '"' for term in lexical_terms(query)[:16])


def retrieve_memory(
    repo: Any,
    workspace: Any,
    query: str,
    *,
    limit: int = 6,
    embedding: EmbeddingSession | None = None,
    explain: bool = False,
    min_similarity: float = 0.8,
) -> RetrievalResult:
    from djobs.observations import _row_to_observation

    started = time.perf_counter()
    safe_query = " ".join(redact_text(query).replace("\x00", "").split())[:500]
    cap = max(1, min(int(limit), 20))
    family, scopes = _scope(workspace)
    adapter = memory_repository(repo)
    fts = _fts_query(safe_query)
    fts_rows = (
        adapter.fts_rows(
            scopes=scopes, query=fts, marker_event="context_injected", limit=max(40, cap * 8)
        )
        if fts
        else []
    )
    candidates = {str(row["id"]): row for row in fts_rows}
    for row in adapter.scan_rows(scopes=scopes, marker_event="context_injected", limit=300):
        candidates.setdefault(str(row["id"]), row)
    eligible, filters = _eligible(list(candidates.values()), workspace)
    lexical = rank_memory_rows(
        eligible, query=safe_query, workspace_root=workspace.root, limit=max(cap, 20)
    )
    lexical_ms = (time.perf_counter() - started) * 1000
    trace: dict[str, Any] = {
        "query_hash": hashlib.sha256(safe_query.encode()).hexdigest(),
        "repository_scope": family,
        "retrieval_version": RETRIEVAL_VERSION,
        "policy_version": POLICY_VERSION,
        "candidate_providers": ["lexical"],
        "candidate_counts": {"fts": len(fts_rows), "bounded_scan_union": len(candidates)},
        "filters": filters,
        "semantic_index_status": "disabled",
        "fallback_reason": None,
        "provider_calls": 0,
        "provider_latency_ms": 0.0,
        "lexical_latency_ms": round(lexical_ms, 3),
        "stored_content_is_data": True,
        "cache_hits": 0,
    }
    chosen = lexical[:cap]
    ranks: dict[str, dict[str, int]] = {}
    similarities: dict[str, float] = {}
    entity_matches: dict[str, tuple[str, ...]] = {}
    if embedding is not None and safe_query:
        calls_before = embedding.calls
        provider_started = time.perf_counter()
        try:
            if isinstance(embedding.provider, UnavailableEmbeddingProvider):
                trace["semantic_index_status"] = "provider_unavailable"
                raise ProviderUnavailableError("provider_initialization_failed")
            if not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1:
                raise ValueError("invalid similarity threshold")
            index = RetrievalIndex(repo)
            sources = index.source_rows(scopes)
            semantic_rows, semantic_filters = _eligible(sources, workspace)
            status, vectors, metadata = index.load(
                family,
                embedding.identity,
                sources,
                eligible_ids={str(row["id"]) for row in semantic_rows},
            )
            trace["semantic_index_status"] = status
            trace["index"] = metadata
            if status != "ready":
                trace["fallback_reason"] = "index_" + status
            elif not vectors:
                trace["semantic_index_status"] = "empty"
            else:
                trace["filters"] = semantic_filters
                semantic_rows = [row for row in semantic_rows if str(row["id"]) in vectors]
                query_vector = embedding.embed([safe_query], purpose="query")[0]
                similarities = {
                    str(row["id"]): sum(
                        a * b for a, b in zip(query_vector, vectors[str(row["id"])], strict=True)
                    )
                    for row in semantic_rows
                }
                semantic_ids = sorted(
                    (
                        record_id
                        for record_id, score in similarities.items()
                        if score >= min_similarity
                    ),
                    key=lambda record_id: (-similarities[record_id], record_id),
                )[:20]
                # Never discard exact/FTS candidates merely because they fall
                # outside the bounded semantic index window.
                row_map = {str(row["id"]): row for row in eligible}
                row_map.update({str(row["id"]): row for row in semantic_rows})
                expanded_lexical = rank_memory_rows(
                    list(row_map.values()),
                    query=safe_query,
                    workspace_root=workspace.root,
                    limit=len(row_map) or 1,
                )
                lexical_map = {str(item.row["id"]): item for item in expanded_lexical}
                meaningful = tuple(
                    term for term in lexical_terms(safe_query) if term not in _STOP_WORDS
                )
                query_entities = set(coding_entities(safe_query))
                for record_id, row in row_map.items():
                    entities = coding_entities(
                        embedding_text(row), metadata_object(row.get("metadata_json"))
                    )
                    overlap = tuple(sorted(query_entities.intersection(entities)))
                    if overlap:
                        entity_matches[record_id] = overlap
                entity_ids = sorted(
                    entity_matches, key=lambda key: (-len(entity_matches[key]), key)
                )[:20]
                lexical_ids = [
                    str(item.row["id"])
                    for item in expanded_lexical
                    if "exact_query" in item.matched_by
                    or str(item.row["id"]) in entity_matches
                    or sum(term in embedding_text(item.row).casefold() for term in meaningful)
                    >= min(2, max(1, len(meaningful)))
                ][:20]
                channels = {"lexical": lexical_ids, "semantic": semantic_ids, "entity": entity_ids}
                scores = reciprocal_rank_fusion(channels)
                for channel, ids in channels.items():
                    for rank, record_id in enumerate(ids, 1):
                        ranks.setdefault(record_id, {})[channel] = rank

                def anchor(record_id: str) -> bool:
                    text = embedding_text(row_map[record_id]).casefold()
                    return bool(
                        safe_query.casefold() in text
                        or (0 < len(meaningful) <= 6 and all(term in text for term in meaningful))
                    )

                fused = sorted(
                    scores,
                    key=lambda key: (
                        -int(anchor(key)),
                        -scores[key],
                        -(lexical_map[key].score if key in lexical_map else 0.0),
                        -similarities.get(key, -1.0),
                        key,
                    ),
                )
                chosen = []
                seen: set[str] = set()
                for record_id in fused:
                    row = row_map[record_id]
                    normalized = " ".join(embedding_text(row).casefold().split())
                    if normalized in seen:
                        continue
                    seen.add(normalized)
                    previous = lexical_map.get(record_id)
                    reasons = list(previous.matched_by if previous else ())
                    reasons.extend(
                        channel for channel in channels if record_id in channels[channel]
                    )
                    if anchor(record_id):
                        reasons.append("exact_anchor")
                    chosen.append(
                        RankedMemory(
                            row=row,
                            score=scores[record_id],
                            matched_by=tuple(dict.fromkeys(reasons)),
                        )
                    )
                    if len(chosen) == cap:
                        break
                trace["candidate_providers"] = [key for key, ids in channels.items() if ids]
                trace["candidate_counts"].update({key: len(ids) for key, ids in channels.items()})
                trace["semantic_identity"] = {
                    "provider": embedding.identity.provider_id,
                    "model": embedding.identity.model_id,
                    "revision": embedding.identity.model_revision,
                }
                trace["similarity_threshold"] = min_similarity
                trace["similarity_is_probability"] = False
        except ProviderUnavailableError as exc:
            chosen = lexical[:cap]
            trace["fallback_reason"] = str(exc)
        except Exception:
            chosen = lexical[:cap]
            trace["fallback_reason"] = "semantic_unavailable"
        finally:
            trace["provider_calls"] = embedding.calls - calls_before
            trace["provider_latency_ms"] = round(
                (time.perf_counter() - provider_started) * 1000, 3
            )
    items = []
    for ranked in chosen:
        item = _row_to_observation(ranked.row, score=ranked.score, matched_by=ranked.matched_by)
        if explain:
            record_id = str(ranked.row["id"])
            item["retrieval"] = {
                "version": RETRIEVAL_VERSION,
                "component_ranks": ranks.get(record_id, {}),
                "fusion_score": round(ranked.score, 8) if ranks else None,
                "entity_matches": list(entity_matches.get(record_id, ())),
                "temporal_decision": "currently_eligible",
                "lifecycle_status": "active",
                "semantic_similarity": round(similarities[record_id], 6)
                if record_id in similarities
                else None,
                "semantic_identity": trace.get("semantic_identity"),
                "score_is_probability": False,
            }
        items.append(item)
    trace["selected_ids"] = [str(item["id"]) for item in items]
    trace["retrieval_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return RetrievalResult(items=items, trace=trace)


def reindex_memory(repo: Any, workspace: Any, embedding: EmbeddingSession) -> dict[str, Any]:
    """Explicit, atomic maintenance. A failed provider never changes the old index."""

    started = time.perf_counter()
    family, scopes = _scope(workspace)
    calls = embedding.calls
    index = RetrievalIndex(repo)
    try:
        if isinstance(embedding.provider, UnavailableEmbeddingProvider):
            raise ProviderUnavailableError("provider_initialization_failed")
        if index.status() == "unsupported_schema":
            return {"ok": False, "status": "unsupported_schema", "continue_coding": True}
        sources = index.source_rows(scopes)
        eligible, filters = _eligible(sources, workspace)
        status, _existing, metadata = index.load(
            family,
            embedding.identity,
            sources,
            eligible_ids={str(row["id"]) for row in eligible},
        )
        if status == "ready":
            return {"ok": True, "status": "unchanged", "provider_calls": 0, **metadata}
        records = []
        for offset in range(0, len(eligible), 8):
            batch = eligible[offset : offset + 8]
            vectors = embedding.embed(
                [embedding_text(row) for row in batch], purpose="passage", maintenance=True
            )
            for row, vector in zip(batch, vectors, strict=True):
                records.append(
                    (
                        str(row["id"]),
                        content_hash(row),
                        pack_vector(vector, embedding.identity.dimension),
                        coding_entities(
                            embedding_text(row), metadata_object(row.get("metadata_json"))
                        ),
                    )
                )
        index.replace(
            family=family,
            scopes=scopes,
            identity=embedding.identity,
            sources=sources,
            records=records,
        )
        return {
            "ok": True,
            "status": "ready",
            "record_count": len(records),
            "filters": filters,
            "provider_calls": embedding.calls - calls,
            "vector_bytes": len(records) * embedding.identity.dimension * 4,
            "identity_hash": embedding.identity.fingerprint,
            "ingestion_ms": round((time.perf_counter() - started) * 1000, 3),
            "canonical_observations_unchanged": True,
            "task_ownership_unchanged": True,
        }
    except ProviderUnavailableError as exc:
        reason = str(exc)
    except ValueError as exc:
        reason = (
            str(exc)
            if str(exc)
            in {"sources_changed_during_reindex", "unsupported future retrieval schema"}
            else "index_unavailable"
        )
    except Exception:
        reason = "index_unavailable"
    return {
        "ok": False,
        "status": reason,
        "continue_coding": True,
        "provider_calls": embedding.calls - calls,
        "previous_index_preserved": True,
    }
