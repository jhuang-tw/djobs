"""Standardized API response helpers and HTTP status code wrappers."""

import json

class ApiResponse:
    def __init__(self, status: int = 200, body = None, headers: dict | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "body": self.body,
            "headers": self.headers,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

def ok(data = None) -> ApiResponse:
    return ApiResponse(200, {"data": data})

def created(data = None) -> ApiResponse:
    return ApiResponse(201, {"data": data})

def bad_request(message: str) -> ApiResponse:
    return ApiResponse(400, {"error": message})

def not_found(message: str = "Not found") -> ApiResponse:
    return ApiResponse(404, {"error": message})

def internal_error(message: str = "Internal server error") -> ApiResponse:
    return ApiResponse(500, {"error": message})
