"""HTTP middleware components for request/response processing pipelines."""

import time
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

class Middleware:
    def __init__(self, handler: Callable):
        self.handler = handler

    def __call__(self, request: dict) -> dict:
        return self.handler(request)

class LoggingMiddleware(Middleware):
    def __call__(self, request: dict) -> dict:
        start = time.time()
        response = self.handler(request)
        elapsed = time.time() - start
        logger.info(f"{request.get('method')} {request.get('path')} {elapsed:.3f}s")
        return response

class AuthMiddleware(Middleware):
    def __init__(self, handler: Callable, auth_service):
        super().__init__(handler)
        self.auth_service = auth_service

    def __call__(self, request: dict) -> dict:
        token = request.get("headers", {}).get("authorization", "")
        if not token:
            return {"status": 401, "body": "Unauthorized"}
        user_id = self.auth_service.validate_token(token)
        if user_id is None:
            return {"status": 403, "body": "Forbidden"}
        request["user_id"] = user_id
        return self.handler(request)

class CorsMiddleware(Middleware):
    def __init__(self, handler: Callable, allowed_origins: list[str] | None = None):
        super().__init__(handler)
        self.allowed_origins = allowed_origins or ["*"]

    def __call__(self, request: dict) -> dict:
        response = self.handler(request)
        response.setdefault("headers", {})
        response["headers"]["Access-Control-Allow-Origin"] = ", ".join(self.allowed_origins)
        return response
