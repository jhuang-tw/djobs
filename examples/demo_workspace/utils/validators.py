"""Input validation helpers for email, phone, password strength, and sanitization."""

import re

def is_valid_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))

def is_valid_phone(phone: str) -> bool:
    cleaned = re.sub(r"[\s\-()]", "", phone)
    return bool(re.match(r"^\+?\d{10,15}$", cleaned))

def is_strong_password(password: str) -> bool:
    if len(password) < 8:
        return False
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    return has_upper and has_lower and has_digit

def sanitize_string(text: str) -> str:
    return re.sub(r"[<>&"']", "", text).strip()
