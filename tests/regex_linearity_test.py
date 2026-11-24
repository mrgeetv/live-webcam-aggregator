r"""Every regex in the package must stay linear on hostile input.

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
import tracemalloc
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
# The slowest few inputs at the smallest size go on to the middle one whatever they
# cost, so a quadratic that is cheap per step still gets looked at.
_ALWAYS_GROW = 20
# From the middle size, one whose cost grew by this factor, ten times the length
# giving about ten times the cost when linear and a hundred when quadratic; or one
# already this slow, which the largest size would take past the limit anyway. At the
# largest, that growth fails the pattern once the scan takes long enough to time well.
_GROWTH_FACTOR = 30
_GROW_PAST_SECONDS = _LIMIT_SECONDS / 10
_QUADRATIC_FLOOR_SECONDS = 0.01
# A linear scan allocates a byte or so per character; one keeping backtracking state
# for every iteration of a repeat, over a hundred.
_BYTES_PER_CHAR = 16
# How many of the characters a pattern's negated classes refuse to end inputs with.
_MAX_STOPPERS = 6


def _re_function(node: ast.AST) -> str | None:
    """Return the `re` function a call node calls with a pattern, or None."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.func.attr in _FLAGS_POSITION
    ):
        return node.func.attr
    return None


def _pattern_argument(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return next((kw.value for kw in node.keywords if kw.arg == "pattern"), None)


def _literal_patterns() -> dict[tuple[str, int], str]:
    """`re.<fn>("<literal>", …, flags)` calls anywhere in the package source."""
    found: dict[tuple[str, int], str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            function = _re_function(node)
            if not isinstance(node, ast.Call) or function is None:
                continue
            argument = _pattern_argument(node)
            if argument is None:
                continue
            try:
                pattern = ast.literal_eval(argument)
            except ValueError:
                # Built at runtime, which the collector test only allows at module
                # level, where _compiled_patterns finds the compiled result.
                continue
            flags = 0
            for kw in node.keywords:
                if kw.arg == "flags":
                    flags = _flags(kw.value)
            position = _FLAGS_POSITION[function]
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
    """Return the compiled patterns held by a module or class.

    That includes any built at runtime (an f-string over a shared path fragment) that no
    source literal spells out.
    """
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
    """Return the patterns in `value`, looking inside tuples, lists and dict values.

    A rule table such as categories._TITLE_COMPILED holds its patterns that way.
    """
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
    seq: Any,
    variant: int,
    pieces: set[str],
    cuts: list[int],
    out: list[str],
    runs_at: list[tuple[int, int]],
) -> None:
    """Append to `out` one string the parsed pattern `seq` matches.

    It is as near as a lookaround-blind walk gets, taking alternative and class member
    `variant` (mod their counts). Every literal run, group, alternative and repeated
    item goes into `pieces`, the material for long runs; the length of `out` after each
    node (bar the inside of a literal run) into `cuts`, where a prefix can usefully end;
    and where each repeat starts and ends into `runs_at`, since a long run is absorbed
    at one and the text between any two (the key in `<[^<>]*KEY[^<>]*>`, even with a
    small repeat inside it) is worth repeating.
    """
    run_start = None
    for index, (op, av) in enumerate(seq):
        kind = str(op)
        size = len("".join(out))
        if kind == "LITERAL":
            run_start = size if run_start is None else run_start
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
            _sample(item, variant, pieces, [], inner, [])
            one = "".join(inner)
            out.append(one * max(low, 1))
            pieces.add(one)
            runs_at.append((size, size + len(one) * max(low, 1)))
        elif kind == "BRANCH":
            for alternative in av[1]:
                spare: list[str] = []
                _sample(alternative, variant, pieces, [], spare, [])
                pieces.add("".join(spare))
            chosen = av[1][variant % len(av[1])]
            _sample(chosen, variant, pieces, cuts, out, runs_at)
        elif kind == "SUBPATTERN":
            _sample(av[3], variant, pieces, cuts, out, runs_at)
        elif kind == "ATOMIC_GROUP":
            _sample(av, variant, pieces, cuts, out, runs_at)
        text = "".join(out)
        if kind in ("BRANCH", "SUBPATTERN", "ATOMIC_GROUP"):
            pieces.add(text[size:])
        followed_by_literal = (
            index + 1 < len(seq) and str(seq[index + 1][0]) == "LITERAL"
        )
        if kind == "LITERAL" and followed_by_literal:
            continue
        if run_start is not None:
            pieces.add(text[run_start:])
            run_start = None
        cuts.append(len(text))


def _widest_choice(seq: Any) -> int:
    """Return the most alternatives (or class members) one choice in the pattern offers.

    The samples between them then visit every one.
    """
    widest = 1
    for op, av in seq:
        kind = str(op)
        if kind == "IN":
            widest = max(widest, len(av))
        elif kind == "BRANCH":
            widest = max(widest, len(av[1]), *(_widest_choice(a) for a in av[1]))
        elif kind.endswith("_REPEAT"):
            widest = max(widest, _widest_choice(av[2]))
        elif kind == "SUBPATTERN":
            widest = max(widest, _widest_choice(av[3]))
        elif kind == "ATOMIC_GROUP":
            widest = max(widest, _widest_choice(av))
    return widest


def _keeps_state_per_iteration(seq: Any) -> bool:
    """Return True if the pattern has a non-possessive multi-character repeat.

    That is a repeat of more than one character at a time. The engine keeps backtracking
    state for every iteration of one, so a long run through it costs memory in
    proportion, however fast it is.
    """
    for op, av in seq:
        kind = str(op)
        if kind in ("MAX_REPEAT", "MIN_REPEAT") and av[1] > 1:
            item = av[2]
            single = len(item) == 1 and str(item[0][0]) in (
                "LITERAL",
                "NOT_LITERAL",
                "ANY",
                "IN",
                "CATEGORY",
            )
            if not single or _keeps_state_per_iteration(item):
                return True
        elif kind == "POSSESSIVE_REPEAT" and _keeps_state_per_iteration(av[2]):
            return True
        elif kind == "BRANCH" and any(_keeps_state_per_iteration(a) for a in av[1]):
            return True
        elif kind == "SUBPATTERN" and _keeps_state_per_iteration(av[3]):
            return True
        elif kind == "ATOMIC_GROUP" and _keeps_state_per_iteration(av):
            return True
    return False


def _category_member(category: Any) -> str:
    name = str(category)
    if "NOT" in name:
        return "a" if "DIGIT" in name or "SPACE" in name else " "
    return "1" if "DIGIT" in name else " " if "SPACE" in name else "a"


def _class_member(items: Any, variant: int) -> str:
    """Return a character inside the class `items` (outside it, for a negated one).

    It is picked by `variant` so the samples between them try more than one.
    """
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
    """Return the characters a negated class `items` excludes, in pattern order."""
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
    """Return the characters some negated class in the pattern refuses.

    Ending an input on one can stop a span short of the end, where `$` then fails.
    """
    found: list[str] = []
    for op, av in seq:
        kind = str(op)
        if kind == "IN" and any(str(o) == "NEGATE" for o, _ in av):
            found.extend(_banned(av))
        elif kind == "NOT_LITERAL":
            found.append(chr(av))
        elif kind.endswith("_REPEAT"):
            found.extend(_stoppers(av[2]))
        elif kind == "BRANCH":
            for alternative in av[1]:
                found.extend(_stoppers(alternative))
        elif kind == "SUBPATTERN":
            found.extend(_stoppers(av[3]))
    return list(dict.fromkeys(found))


# An input: a prefix of a sample match, repeated (no piece) or followed by a long run of
# one piece, then an ending.
_Shape = tuple[str, str, str]


def _hostile_shapes(pattern: str, flags: int) -> dict[str, _Shape]:
    """Return the hostile inputs built from every useful prefix of every sample.

    Each prefix is repeated or followed by a long run of one piece, then ended by a
    character `.` eats, by a newline (which `.` and `$` stop at), or by one a class in
    the pattern refuses. Keyed by a readable label, in a stable order so a failure names
    the same input every run.
    """
    tree = P.parse(pattern, flags)
    prefixes: set[str] = set()
    before_runs: set[str] = set()
    pieces: set[str] = set()
    for variant in range(max(6, _widest_choice(tree))):
        cuts: list[int] = []
        runs_at: list[tuple[int, int]] = []
        out: list[str] = []
        _sample(tree, variant, pieces, cuts, out, runs_at)
        sample = "".join(out)
        prefixes.update(sample[:cut] for cut in cuts if cut)
        before_runs.update(sample[:start] for start, _end in runs_at)
        runs_at.sort()
        pieces.update(
            sample[end:start]
            for i, (_s, end) in enumerate(runs_at)
            for start, _e in runs_at[i + 1 :]
            if start > end
        )
    pieces.discard("")
    # Symbols before letters and digits: a negated class's own delimiters are the
    # characters that stop it, and a range like [^a-z0-9] would crowd them out.
    stoppers = sorted(_stoppers(tree), key=str.isalnum)[:_MAX_STOPPERS]
    endings = ["\x00", "\nx"] + [c + "x" for c in stoppers]
    shapes: dict[str, _Shape] = {}
    for ending in endings:
        for prefix in sorted(prefixes):
            shapes[f"{prefix!r} repeated, then {ending!r}"] = (prefix, "", ending)
        for prefix in sorted(before_runs):
            for piece in sorted(pieces):
                label = f"{prefix!r} + run of {piece!r}, then {ending!r}"
                shapes[label] = (prefix, piece, ending)
    return shapes


def _build(shape: _Shape, size: int) -> str:
    prefix, piece, ending = shape
    if not piece:
        return prefix * (size // len(prefix)) + ending
    return prefix + piece * (size // len(piece)) + ending


class _Runaway(Exception):
    pass


@contextmanager
def _cut_off_after(seconds: float) -> Generator[None]:
    """Interrupt a match still running after `seconds` of CPU.

    The regex engine checks for signals mid-match, so an exponential pattern fails here
    instead of hanging the suite; xdist runs each test on its worker's main thread,
    where signals land.
    """

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
    pattern. Growth is judged on wall time, because CPU time can be counted in whole
    kernel ticks, far coarser than the smaller sizes take.
    """
    try:
        with _cut_off_after(_LIMIT_SECONDS * 2):
            cpu, wall = time.process_time(), time.perf_counter()
            rx.findall(text)
            return time.process_time() - cpu, time.perf_counter() - wall
    except _Runaway:
        return _LIMIT_SECONDS * 2, _LIMIT_SECONDS * 2


def _best_wall(rx: re.Pattern[str], text: str) -> float:
    """Return the quickest of a few wall-clock runs.

    One scheduler hiccup then can't pass for growth.
    """
    return min(_timed(rx, text)[1] for _ in range(3))


def _bytes_per_char(rx: re.Pattern[str], text: str) -> float:
    """Return the peak memory per character for one scan that keeps none of its matches.

    Only what the engine itself holds counts.
    """
    tracemalloc.start()
    try:
        for _match in rx.finditer(text):
            pass
        return tracemalloc.get_traced_memory()[1] / len(text)
    finally:
        tracemalloc.stop()


def _first_slow_input(pattern: str, flags: int) -> str | None:
    """Describe the first hostile input the pattern is too slow on, or None.

    For a repeat that keeps state per iteration the failure is being too memory-hungry
    rather than too slow.

    Every input runs at the smallest size. The slowest few there, and any over a floor,
    are tried at the middle size, and those that grew like a quadratic at the largest,
    where the verdict is the CPU limit or that same growth measured again on the
    quickest of three runs at each size.
    """
    rx = re.compile(pattern, flags)
    small, medium, large = _SIZES
    shapes = _hostile_shapes(pattern, flags)
    first: dict[str, float] = {}
    for label, shape in shapes.items():
        cpu, first[label] = _timed(rx, _build(shape, small))
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {small} chars"
    slowest = sorted(first, key=first.__getitem__)[-_ALWAYS_GROW:]
    worth = [k for k in first if first[k] >= _GROW_FROM_SECONDS or k in slowest]
    grew: list[str] = []
    for label in worth:
        cpu, wall = _timed(rx, _build(shapes[label], medium))
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {medium} chars"
        if wall >= _GROW_PAST_SECONDS or wall >= first[label] * _GROWTH_FACTOR:
            grew.append(label)
    for label in grew:
        text = _build(shapes[label], large)
        cpu, _wall = _timed(rx, text)
        if cpu >= _LIMIT_SECONDS:
            return f"{cpu:.2f}s on {label} at {large} chars"
        wall = _best_wall(rx, text)
        before = _best_wall(rx, _build(shapes[label], medium))
        if wall >= _QUADRATIC_FLOOR_SECONDS and wall >= before * _GROWTH_FACTOR:
            return f"{wall / before:.0f}x the time for 10x the length on {label}"
    if _keeps_state_per_iteration(P.parse(pattern, flags)):
        for label, shape in shapes.items():
            if (
                shape[1]
                and (per := _bytes_per_char(rx, _build(shape, medium)))
                > _BYTES_PER_CHAR
            ):
                return f"{per:.0f} bytes per character on {label} at {medium} chars"
    return None


@pytest.mark.parametrize(
    ("pattern", "flags"),
    [pytest.param(p, f, id=where) for p, f, where in _all_patterns()],
)
def test_every_regex_stays_linear_on_hostile_input(pattern: str, flags: int) -> None:
    assert _first_slow_input(pattern, flags) is None


# One of each shape the guard exists to catch. If it stops flagging these (say a change
# in the private parser it walks leaves it sampling nothing) it would pass every real
# pattern vacuously.
_KNOWN_BAD = {
    "span past the next start": r"<h1[^>]*>([^<]{1,120})</h1>",
    "body before a required suffix": r"https?://[^\"'\s]+\.m3u8",
    "attribute keyword repeated in one tag": r'<[^<>]*rel="bookmark"[^<>]*>([^<]+)',
    "leading whitespace before a suffix": r"\s*live cam\s*$",
    "adjacent quantifiers over one run": r" - [A-Z][a-z]+\s*\.?\s*$",
    "spans either side of a keyword": r"\|[^|]*(?:Wildlife|Trust)[^|]*$",
    ".*$ before a trailing newline": r"【Share-Ju】.*$",
    "one character fitting two branches": r"""(["'])((?:\\.|(?!\1).)*)\1""",
    "backtracking state per iteration": r'"src":"((?:[^"\\]|\\.)*)"',
}


@pytest.mark.parametrize("pattern", list(_KNOWN_BAD.values()), ids=list(_KNOWN_BAD))
def test_the_guard_still_catches_each_known_bad_shape(pattern: str) -> None:
    assert _first_slow_input(pattern, 0) is not None


def test_every_regex_in_the_package_is_where_the_collector_looks() -> None:
    """Every `re` call with a non-literal pattern sits at module level.

    Nothing imports from `re`. The collector reads literal patterns from the source and
    compiled ones from module and class attributes; a pattern built inside a function,
    or imported from `re` by name, would escape both and go untested.
    """
    stray: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(_SRC.parent)
        stray += [
            f"{rel}:{node.lineno} imports from re"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "re"
        ]
        for scope in ast.walk(tree):
            if not isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            for node in ast.walk(scope):
                if not isinstance(node, ast.Call) or _re_function(node) is None:
                    continue
                argument = _pattern_argument(node)
                if argument is None:
                    continue
                try:
                    ast.literal_eval(argument)
                except ValueError:
                    stray.append(f"{rel}:{node.lineno} builds its pattern at runtime")
    assert stray == []


def test_the_collector_sees_inline_and_runtime_built_patterns() -> None:
    """The collector reaches every pattern shape the guard must test.

    A literal inside a function body, a pattern assembled from an f-string and one held
    in a rule table must all be collected.
    """
    wheres = {where for _p, _f, where in _all_patterns()}
    assert any(w.startswith("webcam_aggregator/sources/base.py:") for w in wheres)
    assert "webcam_aggregator.sources.skyline._SITEMAP_CAM" in wheres
    assert "webcam_aggregator.categories._TITLE_COMPILED" in wheres
