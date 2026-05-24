"""Collection utilities: chunking, flattening, grouping, and deduplication."""

from typing import TypeVar, Iterable, Callable

T = TypeVar("T")

def chunk_list(items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]

def flatten(nested: list[list[T]]) -> list[T]:
    return [item for sublist in nested for item in sublist]

def unique_by(items: Iterable[T], key: Callable[[T], str]) -> list[T]:
    seen: set[str] = set()
    result: list[T] = []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result

def group_by(items: Iterable[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    groups: dict[str, list[T]] = {}
    for item in items:
        k = key(item)
        groups.setdefault(k, []).append(item)
    return groups

def first_or_none(items: Iterable[T], predicate: Callable[[T], bool]) -> T | None:
    for item in items:
        if predicate(item):
            return item
    return None
