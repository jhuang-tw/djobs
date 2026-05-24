"""User model with profile display and dictionary serialization."""


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
