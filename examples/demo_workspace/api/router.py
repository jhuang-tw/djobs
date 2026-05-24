"""Lightweight HTTP router for mapping methods and paths to handler functions."""

from typing import Callable, Any

class Route:
    def __init__(self, method: str, path: str, handler: Callable):
        self.method = method.upper()
        self.path = path
        self.handler = handler

class Router:
    def __init__(self):
        self._routes: list[Route] = []

    def get(self, path: str):
        def decorator(fn):
            self._routes.append(Route("GET", path, fn))
            return fn
        return decorator

    def post(self, path: str):
        def decorator(fn):
            self._routes.append(Route("POST", path, fn))
            return fn
        return decorator

    def resolve(self, method: str, path: str) -> Callable | None:
        for route in self._routes:
            if route.method == method.upper() and route.path == path:
                return route.handler
        return None

    def list_routes(self) -> list[dict[str, str]]:
        return [{"method": r.method, "path": r.path} for r in self._routes]
