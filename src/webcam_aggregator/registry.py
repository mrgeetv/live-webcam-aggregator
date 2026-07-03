from __future__ import annotations

from collections.abc import Callable

Predicate = Callable[[str], bool]


class Registry:
    def __init__(self, rules: list[tuple[Predicate, str]]) -> None:
        self._rules: list[tuple[Predicate, str]] = rules  # ordered; first match wins

    def match(self, target_url: str) -> str | None:
        for predicate, name in self._rules:
            if predicate(target_url):
                return name
        return None
