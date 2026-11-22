"""Every regex in the package must stay linear on hostile input.

`re` holds the GIL for a whole match, so one fetched page, manifest or title that sends
a pattern into quadratic backtracking stalls the entire process, playback included.
A hand-written timing test only covers the pattern someone thought of; this one
collects every regex in the package and drives each with the two inputs that expose
the usual shapes:

- a prefix of a match, repeated: many starts, none of which can finish, so a pattern
  whose span can run past the next start re-scans the rest of the input from each
  one (`<h1[^>]*>`, a greedy URL body before a required `.m3u8`);
- a prefix of a match, then one long run of a piece of it: one start whose run
  adjacent quantifiers can split many ways (`\\s*\\.?\\s*$`).

Each input is ended three ways, because what stops a match decides whether it fails
late: a character `.` consumes, a newline (where `.*$` gives up on every line but the
last), and a character one of the pattern's negated classes refuses.

It is a heuristic: a pattern whose bad case needs some other shape gets through, so
it backs up thinking about each new pattern rather than replacing it. The lesson in
AGENTS.md lists the shapes and their fixes.
"""

import ast
import importlib
import pkgutil
import re
import re._parser as P  # pyright: ignore[reportMissingTypeStubs]
import signal
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import webcam_aggregator

_SRC = Path(webcam_aggregator.__file__).parent
# Each `re` function that takes a pattern, and where its positional flags sit.
_FLAGS_POSITION = {"compile": 1, "search": 2, "match": 2, "fullmatch": 2, "findall": 2}
_FLAGS_POSITION |= {"finditer": 2, "split": 3, "sub": 4, "subn": 4}
# The last size is long enough that a quadratic pattern costs seconds and a linear one
# milliseconds, so the limit sits far from both and a busy CI runner doesn't flip the
# result. The smaller sizes come first so a cubic or exponential pattern fails in
# seconds instead of hanging the suite.
_SIZES = (1_000, 10_000, 100_000)
_LIMIT_SECONDS = 0.25
# Which inputs are worth trying at the next size (judged on wall time). Any that costs
# this much at the smallest size: plenty of linear ones do, which only costs a little
# time at the middle size.
_GROW_FROM_SECONDS = 0.00002
# From the middle size, one whose cost grew by this factor, ten times the length
# giving about ten times the cost when linear and a hundred when quadratic; or one
# already this slow, which the largest size would take past the limit anyway.
_GROWTH_FACTOR = 30
_GROW_PAST_SECONDS = _LIMIT_SECONDS / 10


def _literal_patterns() -> dict[tuple[str, int], str]:
    """`re.<fn>("<literal>", …, flags)` calls anywhere in the package source."""
    found: dict[tuple[str, int], str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr in _FLAGS_POSITION
                and node.args
            ):
                continue
            try:
                pattern = ast.literal_eval(node.args[0])
            except ValueError:
                continue  # built at runtime: caught by _compiled_patterns instead
            flags = 0
            for kw in node.keywords:
                if kw.arg == "flags":
                    flags = _flags(kw.value)
            position = _FLAGS_POSITION[node.func.attr]
            if len(node.args) > position:
                flags = _flags(node.args[position])
            rel = path.relative_to(_SRC.parent)
            found.setdefault((pattern, flags), f"{rel}:{node.lineno}")
    return found


def _flags(node: ast.expr) -> int:
    """`re.I | re.S`-style flags, read from the AST rather than evaluated."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flags(node.left) | _flags(node.right)
    if isinstance(node, ast.Attribute):
        return int(getattr(re, node.attr))
    raise AssertionError(f"unreadable regex flags: {ast.unparse(node)}")


def _compiled_patterns() -> dict[tuple[str, int], str]:
    """Compiled patterns held by a module or class, including any built at runtime
    (an f-string over a shared path fragment) that no source literal spells out."""
    found: dict[tuple[str, int], str] = {}
    prefix = webcam_aggregator.__name__ + "."
    for mod_info in pkgutil.walk_packages(webcam_aggregator.__path__, prefix):
        if mod_info.name.endswith("__main__"):
            continue
        module = importlib.import_module(mod_info.name)
        holders: list[tuple[str, Any]] = [(mod_info.name, module)]
        holders += [
            (f"{mod_info.name}.{k}", v)
            for k, v in vars(module).items()
            if isinstance(v, type) and v.__module__ == mod_info.name
        ]
        for owner, holder in holders:
            for name, value in vars(holder).items():
                for rx in _patterns_in(value, depth=3):
                    key = (rx.pattern, rx.flags & ~re.UNICODE)
                    found.setdefault(key, f"{owner}.{name}")
    return found


def _patterns_in(value: object, depth: int) -> list[re.Pattern[str]]:
    """Patterns in `value`, looking inside tuples, lists and dict values (a rule table
    such as categories._TITLE_COMPILED holds its patterns that way)."""
    if isinstance(value, re.Pattern):
        pattern: object = value.pattern
        return [re.compile(pattern, value.flags)] if isinstance(pattern, str) else []
    if depth == 0:
        return []
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (tuple, list)):
        return [rx for item in value for rx in _patterns_in(item, depth - 1)]
    return []


def _all_patterns() -> list[tuple[str, int, str]]:
    found = _compiled_patterns() | _literal_patterns()
    return sorted((p, f, where) for (p, f), where in found.items())


def _sample(
    seq: Any, variant: int, pieces: set[str], cuts: list[int], out: list[str]
) -> None:
    """Append to `out` one string the parsed pattern `seq` matches (as near as a
    lookaround-blind walk gets), taking alternative and class member `variant` (mod
    their counts). Every group, alternative and repeated item's own sample goes into
    `pieces`, the material for long runs, and the length of `out` after each node
    (bar the inside of a literal run) into `cuts`, where a prefix can usefully end."""
    for index, (op, av) in enumerate(seq):
        kind = str(op)
        size = len("".join(out))
        if kind == "LITERAL":
            out.append(chr(av))
        elif kind == "NOT_LITERAL":
            out.append("y" if chr(av) == "x" else "x")
        elif kind == "ANY":
            out.append("a")
        elif kind == "IN":
            out.append(_class_member(av, variant))
        elif kind == "CATEGORY":
            out.append(_category_member(av))
        elif kind == "AT" and str(av) == "AT_BOUNDARY":
            out.append(" ")
        elif kind.endswith("_REPEAT"):
            low, _high, item = av
            inner: list[str] = []
            _sample(item, variant, pieces, [], inner)
            one = "".join(inner)
            out.append(one * max(low, 1))
            pieces.add(one)
        elif kind == "BRANCH":
            alternatives = av[1]
            _sample(
                alternatives[variant % len(alternatives)], variant, pieces, cuts, out
            )
        elif kind == "SUBPATTERN":
            _sample(av[3], variant, pieces, cuts, out)
        elif kind == "ATOMIC_GROUP":
            _sample(av, variant, pieces, cuts, out)
        grown = "".join(out)[size:]
        if kind in ("BRANCH", "SUBPATTERN", "ATOMIC_GROUP"):
            pieces.add(grown)
        followed_by_literal = (
            index + 1 < len(seq) and str(seq[index + 1][0]) == "LITERAL"
        )
        if not (kind == "LITERAL" and followed_by_literal):
            cuts.append(len("".join(out)))


def _category_member(category: Any) -> str:
    name = str(category)
    if "NOT" in name:
        return "a" if "DIGIT" in name or "SPACE" in name else " "
    return "1" if "DIGIT" in name else " " if "SPACE" in name else "a"


def _class_member(items: Any, variant: int) -> str:
    """A character inside the class `items` (outside it, for a negated one), picked by
    `variant` so the samples between them try more than one."""
    if not any(str(op) == "NEGATE" for op, _ in items):
        op, av = items[variant % len(items)]
        if str(op) == "LITERAL":
            return chr(av)
        if str(op) == "RANGE":
            return chr(av[0])
        return _category_member(av)
    banned = _banned(items)
    allowed = [c for c in "xa1/._- " if c not in banned]
    return allowed[variant % len(allowed)] if allowed else "\x01"


def _banned(items: Any) -> list[str]:
    """The characters a negated class `items` excludes, in pattern order."""
    banned: list[str] = []
    for op, av in items:
        kind = str(op)
        if kind == "LITERAL":
            banned.append(chr(av))
        elif kind == "RANGE":
            banned.extend(chr(c) for c in range(av[0], min(av[1], 0x7F) + 1))
        elif kind == "CATEGORY" and "SPACE" in str(av):
            banned.extend(" \t\n\r\f\v")
        elif kind == "CATEGORY" and "DIGIT" in str(av):
            banned.extend("0123456789")
        elif kind == "CATEGORY" and "WORD" in str(av):
            banned.extend(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
            )
    return banned


def _stoppers(seq: Any) -> list[str]:
    """Characters some negated class in the pattern refuses: ending an input on one
    can stop a span short of the end, where `$` then fails."""
    found: list[str] = []
    for op, av in seq:
        kind = str(op)
        if kind == "IN" and any(str(o) == "NEGATE" for o, _ in av):
            found.extend(_banned(av))
        elif kind.endswith("_REPEAT"):
            found.extend(_stoppers(av[2]))
        elif kind == "BRANCH":
            for alternative in av[1]:
                found.extend(_stoppers(alternative))
        elif kind == "SUBPATTERN":
            found.extend(_stoppers(av[3]))
    return list(dict.fromkeys(found))


def _hostile_inputs(pattern: str, flags: int, size: int) -> dict[str, str]:
    """About `size` characters each: every useful prefix of every sample, repeated or
    followed by a long run of one piece, then ended by a character `.` eats, by a
    newline (which `.` and `$` stop at), or by one a class in the pattern refuses."""
    tree = P.parse(pattern, flags)
    prefixes: set[str] = set()
    pieces: set[str] = set()
    for variant in range(6):
        cuts: list[int] = []
        out: list[str] = []
        _sample(tree, variant, pieces, cuts, out)
        sample = "".join(out)
        prefixes.update(sample[:cut] for cut in cuts if cut)
    pieces.discard("")
    endings = ["\x00", "\nx"] + [c + "x" for c in _stoppers(tree)[:3]]
    inputs: dict[str, str] = {}
    for prefix in prefixes:
        for ending in endings:
            body = prefix * (size // len(prefix))
            inputs[f"{prefix!r} repeated, then {ending!r}"] = body + ending
            for piece in pieces:
                run = piece * (size // len(piece))
                inputs[f"{prefix!r} + run of {piece!r}, then {ending!r}"] = (
                    prefix + run + ending
                )
    return inputs


class _Runaway(Exception):
    pass


@contextmanager
def _cut_off_after(seconds: float) -> Generator[None]:
    """Interrupt a match still running after `seconds` of CPU. The regex engine checks
    for signals mid-match, so an exponential pattern fails here instead of hanging the
    suite; xdist runs each test on its worker's main thread, where signals land."""

    def _raise(_signum: int, _frame: object) -> None:
        raise _Runaway

    previous = signal.signal(signal.SIGPROF, _raise)
    signal.setitimer(signal.ITIMER_PROF, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)


def _timed(rx: re.Pattern[str], text: str) -> tuple[float, float]:
    """(CPU, wall) seconds for one full scan; a runaway scan reads as twice the limit.
    The limit is checked on CPU, so a worker the scheduler parks doesn't read as a slow
    pattern. Whether to grow an input is judged on wall time, because CPU time can be
    counted in whole kernel ticks, far coarser than the gate."""
    try:
        with _cut_off_after(_LIMIT_SECONDS * 2):
            cpu, wall = time.process_time(), time.perf_counter()
            rx.findall(text)
            return time.process_time() - cpu, time.perf_counter() - wall
    except _Runaway:
        return _LIMIT_SECONDS * 2, _LIMIT_SECONDS * 2


def _first_slow_input(pattern: str, flags: int) -> str | None:
    """A description of the first hostile input the pattern is too slow on, or None.
    Every input runs at the smallest size; only one that already costs a measurable
    amount at a size is worth the next, which is what keeps this cheap."""
    rx = re.compile(pattern, flags)
    small, medium, large = _SIZES
    first: dict[str, float] = {}
    for label, text in _hostile_inputs(pattern, flags, small).items():
        cpu, first[label] = _timed(rx, text)
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {small} chars"
    middle = _hostile_inputs(pattern, flags, medium)
    worth_growing: list[str] = []
    for label in (label for label, wall in first.items() if wall >= _GROW_FROM_SECONDS):
        cpu, wall = _timed(rx, middle[label])
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {medium} chars"
        if wall >= _GROW_PAST_SECONDS or wall >= first[label] * _GROWTH_FACTOR:
            worth_growing.append(label)
    biggest = _hostile_inputs(pattern, flags, large)
    for label in worth_growing:
        cpu, _wall = _timed(rx, biggest[label])
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {large} chars"
    return None


@pytest.mark.parametrize(
    ("pattern", "flags"),
    [pytest.param(p, f, id=where) for p, f, where in _all_patterns()],
)
def test_every_regex_stays_linear_on_hostile_input(pattern: str, flags: int) -> None:
    assert _first_slow_input(pattern, flags) is None


def test_the_collector_sees_inline_and_runtime_built_patterns() -> None:
    """The guard is only as good as its reach: a literal inside a function body and
    a pattern assembled from an f-string must both be collected."""
    wheres = {where for _p, _f, where in _all_patterns()}
    assert any(w.startswith("webcam_aggregator/sources/base.py:") for w in wheres)
    assert "webcam_aggregator.sources.skyline._SITEMAP_CAM" in wheres
