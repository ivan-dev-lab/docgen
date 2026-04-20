from .helpers import normalize_name
import os


def build_message(name: str) -> str:
    """Build a user-facing greeting message."""
    return f"Hello, {normalize_name(name)}"


async def compute_async(value: int) -> int:
    return value * 2


class Greeter:
    """Simple greeting service."""

    def __init__(self, name: str) -> None:
        self.name = normalize_name(name)

    def greet(self) -> str:
        return build_message(self.name)
