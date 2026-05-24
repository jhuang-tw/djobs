"""Generate dummy Python files for Durable Coder crash recovery demo.

Creates 30 small Python modules without docstrings — enough to:
1. Fill up context window if processed naively
2. Take long enough that session interruption is realistic
3. Demonstrate progress tracking and crash recovery

Usage:
    python scripts/generate_demo_files.py       # create files
    python scripts/generate_demo_files.py clean  # remove files
"""

from __future__ import annotations

import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "demo_workspace"

# 30 files — each has classes/functions WITHOUT docstrings
TEMPLATES = [
    # --- data models ---
    ("models/user.py", '''
class User:
    def __init__(self, name: str, email: str, age: int):
        self.name = name
        self.email = email
        self.age = age

    def is_adult(self) -> bool:
        return self.age >= 18

    def display_name(self) -> str:
        return f"{self.name} <{self.email}>"

    def to_dict(self) -> dict:
        return {"name": self.name, "email": self.email, "age": self.age}
'''),
    ("models/product.py", '''
class Product:
    def __init__(self, sku: str, name: str, price: float, stock: int):
        self.sku = sku
        self.name = name
        self.price = price
        self.stock = stock

    def is_available(self) -> bool:
        return self.stock > 0

    def apply_discount(self, percent: float) -> float:
        return self.price * (1 - percent / 100)

    def restock(self, quantity: int) -> None:
        self.stock += quantity
'''),
    ("models/order.py", '''
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class OrderItem:
    product_sku: str
    quantity: int
    unit_price: float

    def subtotal(self) -> float:
        return self.quantity * self.unit_price

@dataclass
class Order:
    customer_id: str
    items: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def total(self) -> float:
        return sum(item.subtotal() for item in self.items)

    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)
'''),
    ("models/payment.py", '''
from enum import Enum

class PaymentMethod(Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"

class Payment:
    def __init__(self, order_id: str, amount: float, method: PaymentMethod):
        self.order_id = order_id
        self.amount = amount
        self.method = method
        self.is_completed = False

    def process(self) -> bool:
        if self.amount <= 0:
            return False
        self.is_completed = True
        return True

    def refund(self) -> float:
        if not self.is_completed:
            return 0.0
        self.is_completed = False
        return self.amount
'''),
    ("models/inventory.py", '''
class InventoryManager:
    def __init__(self):
        self._stock: dict[str, int] = {}

    def add_stock(self, sku: str, quantity: int) -> None:
        self._stock[sku] = self._stock.get(sku, 0) + quantity

    def remove_stock(self, sku: str, quantity: int) -> bool:
        current = self._stock.get(sku, 0)
        if current < quantity:
            return False
        self._stock[sku] = current - quantity
        return True

    def check_stock(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def low_stock_items(self, threshold: int = 5) -> list[str]:
        return [sku for sku, qty in self._stock.items() if qty < threshold]
'''),
    # --- services ---
    ("services/auth.py", '''
import hashlib
import secrets

class AuthService:
    def __init__(self):
        self._tokens: dict[str, str] = {}

    def hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"{salt}:{hashed}"

    def verify_password(self, password: str, stored: str) -> bool:
        salt, hashed = stored.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == hashed

    def create_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = user_id
        return token

    def validate_token(self, token: str) -> str | None:
        return self._tokens.get(token)

    def revoke_token(self, token: str) -> bool:
        return self._tokens.pop(token, None) is not None
'''),
    ("services/email.py", '''
class EmailService:
    def __init__(self, smtp_host: str, smtp_port: int):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self._sent: list[dict] = []

    def send(self, to: str, subject: str, body: str) -> bool:
        if not to or not subject:
            return False
        self._sent.append({"to": to, "subject": subject, "body": body})
        return True

    def send_bulk(self, recipients: list[str], subject: str, body: str) -> int:
        count = 0
        for r in recipients:
            if self.send(r, subject, body):
                count += 1
        return count

    def sent_count(self) -> int:
        return len(self._sent)
'''),
    ("services/cache.py", '''
import time

class CacheEntry:
    def __init__(self, value, ttl_seconds: int):
        self.value = value
        self.expires_at = time.time() + ttl_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

class SimpleCache:
    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None or entry.is_expired():
            return None
        return entry.value

    def set(self, key: str, value, ttl: int | None = None) -> None:
        self._store[key] = CacheEntry(value, ttl or self.default_ttl)

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def clear_expired(self) -> int:
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]
        return len(expired)
'''),
    ("services/rate_limiter.py", '''
import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        self._requests[client_id] = [
            t for t in self._requests[client_id] if t > window_start
        ]
        if len(self._requests[client_id]) >= self.max_requests:
            return False
        self._requests[client_id].append(now)
        return True

    def remaining(self, client_id: str) -> int:
        now = time.time()
        window_start = now - self.window_seconds
        recent = [t for t in self._requests[client_id] if t > window_start]
        return max(0, self.max_requests - len(recent))

    def reset(self, client_id: str) -> None:
        self._requests.pop(client_id, None)
'''),
    ("services/notification.py", '''
from enum import Enum

class Channel(Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    WEBHOOK = "webhook"

class Notification:
    def __init__(self, recipient: str, channel: Channel, message: str):
        self.recipient = recipient
        self.channel = channel
        self.message = message
        self.delivered = False

    def mark_delivered(self) -> None:
        self.delivered = True

class NotificationService:
    def __init__(self):
        self._queue: list[Notification] = []
        self._sent: list[Notification] = []

    def enqueue(self, recipient: str, channel: Channel, message: str) -> None:
        self._queue.append(Notification(recipient, channel, message))

    def process_queue(self) -> int:
        count = 0
        while self._queue:
            n = self._queue.pop(0)
            n.mark_delivered()
            self._sent.append(n)
            count += 1
        return count

    def pending_count(self) -> int:
        return len(self._queue)
'''),
    # --- utils ---
    ("utils/validators.py", '''
import re

def is_valid_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))

def is_valid_phone(phone: str) -> bool:
    cleaned = re.sub(r"[\\s\\-()]", "", phone)
    return bool(re.match(r"^\\+?\\d{10,15}$", cleaned))

def is_strong_password(password: str) -> bool:
    if len(password) < 8:
        return False
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    return has_upper and has_lower and has_digit

def sanitize_string(text: str) -> str:
    return re.sub(r"[<>&\"']", "", text).strip()
'''),
    ("utils/formatters.py", '''
from datetime import datetime

def format_currency(amount: float, currency: str = "USD") -> str:
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "TWD": "NT$"}
    symbol = symbols.get(currency, currency + " ")
    return f"{symbol}{amount:,.2f}"

def format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
    return dt.strftime(fmt)

def format_filesize(bytes_count: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_count < 1024:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024
    return f"{bytes_count:.1f} PB"

def truncate(text: str, max_length: int = 100, suffix: str = "...") -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix

def slugify(text: str) -> str:
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^\\w\\s-]", "", slug)
    slug = re.sub(r"[\\s_]+", "-", slug)
    return slug.strip("-")
'''),
    ("utils/retry.py", '''
import time
import random
from typing import Callable, TypeVar

T = TypeVar("T")

class RetryExhausted(Exception):
    pass

def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> T:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt == max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= random.uniform(0.5, 1.5)
            time.sleep(delay)
    raise RetryExhausted(f"Failed after {max_attempts} attempts") from last_error

def retry_decorator(max_attempts: int = 3, base_delay: float = 1.0):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            return retry_with_backoff(
                lambda: fn(*args, **kwargs),
                max_attempts=max_attempts,
                base_delay=base_delay,
            )
        return wrapper
    return decorator
'''),
    ("utils/logging_utils.py", '''
import logging
import json
from datetime import datetime

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger

def log_execution_time(logger: logging.Logger):
    import functools
    import time
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = fn(*args, **kwargs)
            elapsed = time.time() - start
            logger.info(f"{fn.__name__} took {elapsed:.3f}s")
            return result
        return wrapper
    return decorator
'''),
    ("utils/collections.py", '''
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
'''),
    # --- api layer ---
    ("api/router.py", '''
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
'''),
    ("api/middleware.py", '''
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
'''),
    ("api/response.py", '''
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
'''),
    ("api/serializers.py", '''
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
'''),
    # --- config / infra ---
    ("config/settings.py", '''
import os

class Settings:
    def __init__(self):
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///app.db")
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
        self.max_connections = int(os.getenv("MAX_CONNECTIONS", "10"))
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

    def is_production(self) -> bool:
        return not self.debug

    def validate(self) -> list[str]:
        errors = []
        if self.secret_key == "change-me-in-production" and self.is_production():
            errors.append("SECRET_KEY must be set in production")
        if self.max_connections < 1:
            errors.append("MAX_CONNECTIONS must be positive")
        return errors
'''),
    ("config/database.py", '''
class DatabaseConfig:
    def __init__(self, url: str, pool_size: int = 5, echo: bool = False):
        self.url = url
        self.pool_size = pool_size
        self.echo = echo
        self._connection = None

    def connect(self):
        self._connection = f"connected:{self.url}"
        return self._connection

    def disconnect(self):
        self._connection = None

    def is_connected(self) -> bool:
        return self._connection is not None

    def get_connection(self):
        if not self.is_connected():
            self.connect()
        return self._connection

class ConnectionPool:
    def __init__(self, config: DatabaseConfig, max_size: int = 10):
        self.config = config
        self.max_size = max_size
        self._pool: list = []
        self._in_use: int = 0

    def acquire(self):
        if self._pool:
            self._in_use += 1
            return self._pool.pop()
        if self._in_use < self.max_size:
            self._in_use += 1
            return self.config.connect()
        raise RuntimeError("Connection pool exhausted")

    def release(self, conn) -> None:
        self._pool.append(conn)
        self._in_use -= 1

    def size(self) -> int:
        return len(self._pool) + self._in_use
'''),
    # --- domain logic ---
    ("domain/pricing.py", '''
class PricingRule:
    def __init__(self, name: str, discount_percent: float, min_quantity: int = 0):
        self.name = name
        self.discount_percent = discount_percent
        self.min_quantity = min_quantity

    def applies_to(self, quantity: int) -> bool:
        return quantity >= self.min_quantity

    def calculate_discount(self, price: float) -> float:
        return price * (self.discount_percent / 100)

class PricingEngine:
    def __init__(self):
        self._rules: list[PricingRule] = []

    def add_rule(self, rule: PricingRule) -> None:
        self._rules.append(rule)

    def best_discount(self, price: float, quantity: int) -> float:
        applicable = [r for r in self._rules if r.applies_to(quantity)]
        if not applicable:
            return 0.0
        return max(r.calculate_discount(price) for r in applicable)

    def final_price(self, unit_price: float, quantity: int) -> float:
        discount = self.best_discount(unit_price, quantity)
        return max(0, (unit_price - discount) * quantity)
'''),
    ("domain/shipping.py", '''
class ShippingZone:
    def __init__(self, name: str, base_rate: float, per_kg_rate: float):
        self.name = name
        self.base_rate = base_rate
        self.per_kg_rate = per_kg_rate

    def calculate(self, weight_kg: float) -> float:
        return self.base_rate + (self.per_kg_rate * weight_kg)

class ShippingCalculator:
    def __init__(self):
        self._zones: dict[str, ShippingZone] = {}

    def register_zone(self, zone: ShippingZone) -> None:
        self._zones[zone.name] = zone

    def estimate(self, zone_name: str, weight_kg: float) -> float | None:
        zone = self._zones.get(zone_name)
        if zone is None:
            return None
        return zone.calculate(weight_kg)

    def cheapest_option(self, weight_kg: float) -> tuple[str, float] | None:
        if not self._zones:
            return None
        best = min(self._zones.items(), key=lambda z: z[1].calculate(weight_kg))
        return best[0], best[1].calculate(weight_kg)

    def available_zones(self) -> list[str]:
        return list(self._zones.keys())
'''),
    ("domain/tax.py", '''
class TaxRule:
    def __init__(self, region: str, rate: float, name: str = ""):
        self.region = region
        self.rate = rate
        self.name = name or f"Tax {region}"

    def compute(self, amount: float) -> float:
        return round(amount * self.rate, 2)

class TaxCalculator:
    def __init__(self):
        self._rules: dict[str, list[TaxRule]] = {}

    def add_rule(self, rule: TaxRule) -> None:
        self._rules.setdefault(rule.region, []).append(rule)

    def total_tax(self, region: str, amount: float) -> float:
        rules = self._rules.get(region, [])
        return sum(r.compute(amount) for r in rules)

    def tax_breakdown(self, region: str, amount: float) -> list[dict]:
        rules = self._rules.get(region, [])
        return [{"name": r.name, "rate": r.rate, "amount": r.compute(amount)} for r in rules]

    def effective_rate(self, region: str) -> float:
        rules = self._rules.get(region, [])
        return sum(r.rate for r in rules)
'''),
    # --- more modules to reach 30 ---
    ("domain/discount.py", '''
from datetime import datetime

class CouponCode:
    def __init__(self, code: str, discount_percent: float, expires_at: datetime, max_uses: int = 100):
        self.code = code
        self.discount_percent = discount_percent
        self.expires_at = expires_at
        self.max_uses = max_uses
        self.used_count = 0

    def is_valid(self) -> bool:
        return self.used_count < self.max_uses and datetime.now() < self.expires_at

    def apply(self, amount: float) -> float:
        if not self.is_valid():
            return amount
        self.used_count += 1
        return amount * (1 - self.discount_percent / 100)

    def remaining_uses(self) -> int:
        return max(0, self.max_uses - self.used_count)
'''),
    ("domain/analytics.py", '''
from collections import Counter
from datetime import datetime

class EventTracker:
    def __init__(self):
        self._events: list[dict] = []

    def track(self, event_type: str, metadata: dict | None = None) -> None:
        self._events.append({
            "type": event_type,
            "metadata": metadata or {},
            "timestamp": datetime.now().isoformat(),
        })

    def count_by_type(self) -> dict[str, int]:
        counter = Counter(e["type"] for e in self._events)
        return dict(counter)

    def recent(self, limit: int = 10) -> list[dict]:
        return self._events[-limit:]

    def filter_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self._events if e["type"] == event_type]

    def total_events(self) -> int:
        return len(self._events)

    def clear(self) -> int:
        count = len(self._events)
        self._events.clear()
        return count
'''),
    ("domain/workflow.py", '''
from enum import Enum

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class WorkflowStep:
    def __init__(self, name: str, action: str):
        self.name = name
        self.action = action
        self.status = StepStatus.PENDING
        self.error: str | None = None

    def start(self) -> None:
        self.status = StepStatus.RUNNING

    def complete(self) -> None:
        self.status = StepStatus.COMPLETED

    def fail(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.error = error

    def skip(self) -> None:
        self.status = StepStatus.SKIPPED

class Workflow:
    def __init__(self, name: str):
        self.name = name
        self.steps: list[WorkflowStep] = []

    def add_step(self, name: str, action: str) -> WorkflowStep:
        step = WorkflowStep(name, action)
        self.steps.append(step)
        return step

    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return done / len(self.steps) * 100

    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self.steps)

    def failed_steps(self) -> list[WorkflowStep]:
        return [s for s in self.steps if s.status == StepStatus.FAILED]
'''),
    # --- __init__.py files ---
    ("models/__init__.py", ""),
    ("services/__init__.py", ""),
    ("utils/__init__.py", ""),
    ("api/__init__.py", ""),
    ("config/__init__.py", ""),
    ("domain/__init__.py", ""),
    ("__init__.py", ""),
]


def create_files() -> int:
    count = 0
    for rel_path, content in TEMPLATES:
        fpath = DEMO_DIR / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content.lstrip("\n"), encoding="utf-8")
        count += 1
        print(f"  Created: {fpath.relative_to(DEMO_DIR.parent.parent)}")
    return count


def clean() -> int:
    import shutil
    if DEMO_DIR.exists():
        count = sum(1 for _ in DEMO_DIR.rglob("*.py"))
        shutil.rmtree(DEMO_DIR)
        print(f"  Removed {DEMO_DIR}")
        return count
    print("  Nothing to clean.")
    return 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        n = clean()
        print(f"Cleaned {n} files.")
    else:
        n = create_files()
        print(f"\nGenerated {n} files in {DEMO_DIR}")
        print("\nNow open a Durable Coder chat and paste:")
        print('  "幫 examples/demo_workspace/ 底下所有 Python 檔案加上 module docstring"')


if __name__ == "__main__":
    main()
