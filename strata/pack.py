"""Circle packing, done here rather than in the browser.

The map is a hierarchy of circles: directories enclosing directories enclosing
files, each file's *area* proportional to its size. Laying it out in Python
rather than in JavaScript means the geometry is deterministic, testable, and
already finished by the time the page opens — the report has no layout pass and
no dependency on a charting library.

This is a port of the front-chain packing algorithm (Wang et al. 2006) and of
Welzl's smallest-enclosing-circle, both as refined in d3-hierarchy. Randomness
is supplied by a fixed-seed generator so the same repository always produces the
same picture.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from math import sqrt
from typing import Dict, List, Optional, Sequence, Tuple

EPSILON = 1e-6


@dataclass
class Circle:
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0


@dataclass
class PackedNode:
    path: str
    kind: str  #: "dir" or "file"
    depth: int
    x: float
    y: float
    r: float
    value: float


class _Random:
    """A tiny deterministic LCG, seeded to a constant so pictures are stable."""

    def __init__(self, seed: int = 0x2545F491) -> None:
        self._state = seed & 0xFFFFFFFF

    def next(self) -> float:
        self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
        return self._state / 0x100000000

    def shuffle(self, items: Sequence) -> List:
        result = list(items)
        for i in range(len(result) - 1, 0, -1):
            j = int(self.next() * (i + 1))
            result[i], result[j] = result[j], result[i]
        return result


def _encloses_weak(a: Circle, b: Circle) -> bool:
    dr = a.r - b.r + max(a.r, b.r, 1.0) * EPSILON
    dx = b.x - a.x
    dy = b.y - a.y
    return dr > 0 and dr * dr > dx * dx + dy * dy


def _encloses_not(a: Circle, b: Circle) -> bool:
    dr = a.r - b.r
    dx = b.x - a.x
    dy = b.y - a.y
    return dr < 0 or dr * dr < dx * dx + dy * dy


def _encloses_weak_all(a: Circle, others: Sequence[Circle]) -> bool:
    return all(_encloses_weak(a, b) for b in others)


def _basis1(a: Circle) -> Circle:
    return Circle(a.x, a.y, a.r)


def _basis2(a: Circle, b: Circle) -> Circle:
    x1, y1, r1 = a.x, a.y, a.r
    x2, y2, r2 = b.x, b.y, b.r
    x21, y21, r21 = x2 - x1, y2 - y1, r2 - r1
    length = sqrt(x21 * x21 + y21 * y21)
    if length == 0:
        return Circle(x1, y1, max(r1, r2))
    return Circle(
        (x1 + x2 + x21 / length * r21) / 2,
        (y1 + y2 + y21 / length * r21) / 2,
        (length + r1 + r2) / 2,
    )


def _basis3(a: Circle, b: Circle, c: Circle) -> Circle:
    x1, y1, r1 = a.x, a.y, a.r
    x2, y2, r2 = b.x, b.y, b.r
    x3, y3, r3 = c.x, c.y, c.r
    a2, a3 = x1 - x2, x1 - x3
    b2, b3 = y1 - y2, y1 - y3
    c2, c3 = r2 - r1, r3 - r1
    d1 = x1 * x1 + y1 * y1 - r1 * r1
    d2 = d1 - x2 * x2 - y2 * y2 + r2 * r2
    d3 = d1 - x3 * x3 - y3 * y3 + r3 * r3
    ab = a3 * b2 - a2 * b3
    if ab == 0:
        return _basis2(a, b)
    xa = (b2 * d3 - b3 * d2) / (ab * 2) - x1
    xb = (b3 * c2 - b2 * c3) / ab
    ya = (a3 * d2 - a2 * d3) / (ab * 2) - y1
    yb = (a2 * c3 - a3 * c2) / ab
    big_a = xb * xb + yb * yb - 1
    big_b = 2 * (r1 + xa * xb + ya * yb)
    big_c = xa * xa + ya * ya - r1 * r1
    if abs(big_a) > EPSILON:
        discriminant = big_b * big_b - 4 * big_a * big_c
        root = sqrt(discriminant) if discriminant > 0 else 0.0
        r = -(big_b + root) / (2 * big_a)
    elif big_b != 0:
        r = -big_c / big_b
    else:
        return _basis2(a, b)
    return Circle(x1 + xa + xb * r, y1 + ya + yb * r, r)


def _extend_basis(basis: List[Circle], p: Circle) -> List[Circle]:
    if _encloses_weak_all(p, basis):
        return [p]

    for candidate in basis:
        if _encloses_not(p, candidate) and _encloses_weak_all(_basis2(candidate, p), basis):
            return [candidate, p]

    for i in range(len(basis) - 1):
        for j in range(i + 1, len(basis)):
            left, right = basis[i], basis[j]
            if (
                _encloses_not(_basis2(left, right), p)
                and _encloses_not(_basis2(left, p), right)
                and _encloses_not(_basis2(right, p), left)
                and _encloses_weak_all(_basis3(left, right, p), basis)
            ):
                return [left, right, p]

    # Degenerate input (coincident circles); the widest basis still encloses.
    return basis[:2] + [p] if len(basis) >= 2 else [p]


def _bounding(circles: Sequence[Circle]) -> Circle:
    """A guaranteed-enclosing circle. Loose, but never wrong."""
    cx = sum(c.x for c in circles) / len(circles)
    cy = sum(c.y for c in circles) / len(circles)
    radius = max(sqrt((c.x - cx) ** 2 + (c.y - cy) ** 2) + c.r for c in circles)
    return Circle(cx, cy, radius)


def enclose(circles: Sequence[Circle], rng: Optional[_Random] = None) -> Circle:
    """Smallest circle containing all of ``circles`` (Welzl, move-to-front)."""
    if not circles:
        return Circle()
    rng = rng or _Random()
    shuffled = rng.shuffle(circles)

    basis: List[Circle] = []
    result: Optional[Circle] = None
    i = 0
    guard = 0
    limit = 64 * len(shuffled) + 64

    while i < len(shuffled):
        guard += 1
        if guard > limit:  # numerical stalemate; fall back to a safe bound
            return _bounding(circles)
        candidate = shuffled[i]
        if result is not None and _encloses_weak(result, candidate):
            i += 1
            continue
        basis = _extend_basis(basis, candidate)
        if len(basis) == 1:
            result = _basis1(basis[0])
        elif len(basis) == 2:
            result = _basis2(basis[0], basis[1])
        else:
            result = _basis3(basis[0], basis[1], basis[2])
        i = 0

    return result or _bounding(circles)


def _place(a: Circle, b: Circle, c: Circle) -> None:
    """Position ``c`` so it is tangent to both ``a`` and ``b``."""
    dx, dy = b.x - a.x, b.y - a.y
    d2 = dx * dx + dy * dy
    if d2 <= 0:
        c.x, c.y = a.x + c.r, a.y
        return
    a2 = (a.r + c.r) ** 2
    b2 = (b.r + c.r) ** 2
    if a2 > b2:
        x = (d2 + b2 - a2) / (2 * d2)
        y = sqrt(max(0.0, b2 / d2 - x * x))
        c.x = b.x - x * dx - y * dy
        c.y = b.y - x * dy + y * dx
    else:
        x = (d2 + a2 - b2) / (2 * d2)
        y = sqrt(max(0.0, a2 / d2 - x * x))
        c.x = a.x + x * dx - y * dy
        c.y = a.y + x * dy + y * dx


def _intersects(a: Circle, b: Circle) -> bool:
    dr = a.r + b.r - EPSILON
    dx, dy = b.x - a.x, b.y - a.y
    return dr > 0 and dr * dr > dx * dx + dy * dy


class _Link:
    """A node in the circular front chain."""

    __slots__ = ("circle", "prev", "next")

    def __init__(self, circle: Circle) -> None:
        self.circle = circle
        self.prev: "_Link" = self
        self.next: "_Link" = self


def _score(node: _Link) -> float:
    a, b = node.circle, node.next.circle
    ab = a.r + b.r
    if ab == 0:
        return 0.0
    dx = (a.x * b.r + b.x * a.r) / ab
    dy = (a.y * b.r + b.y * a.r) / ab
    return dx * dx + dy * dy


def pack_siblings(circles: List[Circle], rng: Optional[_Random] = None) -> float:
    """Pack ``circles`` around the origin in place; return the enclosing radius."""
    count = len(circles)
    if count == 0:
        return 0.0

    first = circles[0]
    first.x, first.y = 0.0, 0.0
    if count == 1:
        return first.r

    second = circles[1]
    first.x, second.x, second.y = -second.r, first.r, 0.0
    if count == 2:
        return first.r + second.r

    _place(second, first, circles[2])

    a, b, c = _Link(first), _Link(second), _Link(circles[2])
    a.next = c.prev = b
    b.next = a.prev = c
    c.next = b.prev = a

    index = 3
    while index < count:
        _place(a.circle, b.circle, circles[index])
        node = _Link(circles[index])

        # Walk outwards along the front chain from both ends, looking for the
        # nearest circle the newcomer would overlap.
        j, k = b.next, a.prev
        sj, sk = b.circle.r, a.circle.r
        collided = False
        while True:
            if sj <= sk:
                if _intersects(j.circle, node.circle):
                    b = j
                    a.next, b.prev = b, a
                    collided = True
                    break
                sj += j.circle.r
                j = j.next
            else:
                if _intersects(k.circle, node.circle):
                    a = k
                    a.next, b.prev = b, a
                    collided = True
                    break
                sk += k.circle.r
                k = k.prev
            if j is k.next:
                break

        if collided:
            continue  # retry the same circle against the shortened chain

        node.prev, node.next = a, b
        a.next = b.prev = node

        best = _score(a)
        cursor = node.next
        while cursor is not b:
            value = _score(cursor)
            if value < best:
                a, best = cursor, value
            cursor = cursor.next
        b = a.next
        index += 1

    chain = [b.circle]
    cursor = b.next
    while cursor is not b:
        chain.append(cursor.circle)
        cursor = cursor.next

    hull = enclose(chain, rng)
    for circle in circles:
        circle.x -= hull.x
        circle.y -= hull.y
    return hull.r


@dataclass
class _Node:
    name: str
    path: str
    value: float = 0.0
    children: Dict[str, "_Node"] = field(default_factory=dict)
    circle: Circle = field(default_factory=Circle)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _build_tree(entries: Sequence[Tuple[str, float]]) -> _Node:
    root = _Node(name="", path="")
    for path, value in entries:
        parts = [p for p in path.split("/") if p]
        if not parts:
            continue
        node = root
        for depth, part in enumerate(parts):
            child = node.children.get(part)
            if child is None:
                child = _Node(name=part, path="/".join(parts[: depth + 1]))
                node.children[part] = child
            node = child
        node.value = max(value, 1.0)
    return root


def _layout(node: _Node, padding: float, rng: _Random) -> float:
    """Recursively pack ``node``'s children; return its radius."""
    if node.is_leaf:
        node.circle.r = sqrt(max(node.value, 1.0))
        return node.circle.r

    children = [node.children[name] for name in sorted(node.children)]
    for child in children:
        _layout(child, padding, rng)

    circles = [Circle(0.0, 0.0, child.circle.r + padding) for child in children]
    radius = pack_siblings(circles, rng)
    for child, circle in zip(children, circles):
        child.circle.x, child.circle.y = circle.x, circle.y

    node.circle.r = radius + padding
    return node.circle.r


def _flatten(node: _Node, ox: float, oy: float, depth: int, out: List[PackedNode]) -> None:
    x = ox + node.circle.x
    y = oy + node.circle.y
    if depth > 0:
        out.append(
            PackedNode(
                path=node.path,
                kind="file" if node.is_leaf else "dir",
                depth=depth,
                x=x,
                y=y,
                r=node.circle.r,
                value=node.value,
            )
        )
    for name in sorted(node.children):
        _flatten(node.children[name], x, y, depth + 1, out)


def pack_tree(
    entries: Sequence[Tuple[str, float]], size: float = 1000.0, padding: float = 1.6
) -> List[PackedNode]:
    """Lay out ``(path, value)`` pairs as nested circles.

    Coordinates come back centred on ``(size/2, size/2)``, with the root circle
    just touching the edges. Leaf areas are proportional to ``value``.
    """
    if not entries:
        return []
    rng = _Random()
    root = _build_tree(entries)
    radius = _layout(root, padding, rng)

    nodes: List[PackedNode] = []
    _flatten(root, 0.0, 0.0, 0, nodes)
    if radius <= 0:
        return nodes

    scale = (size / 2.0) / radius
    centre = size / 2.0
    for node in nodes:
        node.x = round(node.x * scale + centre, 2)
        node.y = round(node.y * scale + centre, 2)
        node.r = round(node.r * scale, 2)
    return nodes


def group_by_directory(paths: Sequence[str]) -> Dict[str, List[str]]:
    """Helper for callers that want a directory index of the same tree."""
    grouped: Dict[str, List[str]] = {}
    for path in paths:
        grouped.setdefault(posixpath.dirname(path), []).append(path)
    return grouped
