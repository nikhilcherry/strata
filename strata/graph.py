"""Graph algorithms over the import graph.

Tarjan's strongly-connected components, iteratively. Real dependency graphs are
deep enough that the textbook recursive formulation blows Python's stack on
about the third repository you point it at.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


def strongly_connected(edges: Dict[str, Set[str]], nodes: Optional[Iterable[str]] = None) -> List[List[str]]:
    """Return every cycle group, largest first. Singletons are omitted.

    A component of size > 1 means every file in it can reach every other, which
    is the precise definition of a dependency knot.
    """
    if nodes is None:
        collected: Set[str] = set(edges)
        for targets in edges.values():
            collected.update(targets)
        nodes = collected

    index_of: Dict[str, int] = {}
    low_of: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    components: List[List[str]] = []
    counter = 0

    for root in sorted(nodes):
        if root in index_of:
            continue

        # (node, iterator over successors) — an explicit call stack.
        work: List[Tuple[str, Iterator[str]]] = [(root, iter(sorted(edges.get(root, ()))))]
        index_of[root] = low_of[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, successors = work[-1]
            advanced = False
            for target in successors:
                if target not in index_of:
                    index_of[target] = low_of[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, iter(sorted(edges.get(target, ())))))
                    advanced = True
                    break
                if target in on_stack:
                    low_of[node] = min(low_of[node], index_of[target])
            if advanced:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low_of[parent] = min(low_of[parent], low_of[node])

            if low_of[node] == index_of[node]:
                component: List[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))

    components.sort(key=lambda c: (-len(c), c[0]))
    return components


def cycle_path(edges: Dict[str, Set[str]], component: Sequence[str]) -> List[str]:
    """A concrete round trip through ``component``, for showing a human.

    Knowing that eight files form a knot is less useful than being able to read
    ``a -> b -> c -> a``.
    """
    members = set(component)
    start = component[0]
    previous: Dict[str, str] = {}
    queue = deque([start])
    seen = {start}

    while queue:
        node = queue.popleft()
        for target in sorted(edges.get(node, ())):
            if target not in members:
                continue
            if target == start:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                path.reverse()
                return path + [start]
            if target not in seen:
                seen.add(target)
                previous[target] = node
                queue.append(target)
    return list(component)


def fan_in_counts(edges: Dict[str, Set[str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for targets in edges.values():
        for target in targets:
            counts[target] = counts.get(target, 0) + 1
    return counts


def reachable_from(edges: Dict[str, Set[str]], roots: Iterable[str]) -> Set[str]:
    """Everything transitively imported by ``roots``, including the roots."""
    seen: Set[str] = set()
    queue = deque(roots)
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        queue.extend(edges.get(node, ()))
    return seen
