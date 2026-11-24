"""Extractor registry: routes a target URL to an extractor by ordered predicates."""

from __future__ import annotations

from collections.abc import Callable

Predicate = Callable[[str], bool]


class Registry:
    """Ordered (predicate, extractor name) rules; the first match wins."""

    def __init__(self, rules: list[tuple[Predicate, str]]) -> None:
        """Take the ordered `rules`."""
        self._rules: list[tuple[Predicate, str]] = rules  # ordered; first match wins

    def match(self, target_url: str) -> str | None:
        """Return the name of the first extractor whose predicate matches, or None."""
        for predicate, name in self._rules:
            if predicate(target_url):
                return name
        return None
