"""Object serialization and paginated response utilities for API endpoints."""

from datetime import datetime
from typing import Any

class Serializer:
    def __init__(self, fields: list[str]):
        self.fields = fields

    def serialize(self, obj: Any) -> dict:
        result = {}
        for field in self.fields:
            value = getattr(obj, field, None)
            if isinstance(value, datetime):
                result[field] = value.isoformat()
            elif hasattr(value, "__dict__"):
                result[field] = vars(value)
            else:
                result[field] = value
        return result

    def serialize_many(self, objects: list[Any]) -> list[dict]:
        return [self.serialize(obj) for obj in objects]

class PaginatedResponse:
    def __init__(self, items: list, total: int, page: int, per_page: int):
        self.items = items
        self.total = total
        self.page = page
        self.per_page = per_page

    def total_pages(self) -> int:
        return (self.total + self.per_page - 1) // self.per_page

    def has_next(self) -> bool:
        return self.page < self.total_pages()

    def to_dict(self, serializer: Serializer) -> dict:
        return {
            "items": serializer.serialize_many(self.items),
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "total_pages": self.total_pages(),
            "has_next": self.has_next(),
        }
