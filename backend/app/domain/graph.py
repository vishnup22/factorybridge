"""Dependency DAG: node identity, cycle detection, deterministic downstream traversal.

An edge `source -> target` means "target must be revalidated if source changes". It is NOT a
compatibility judgement; deterministic validators decide compatibility separately.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

NODE_TYPES = (
    "BOM_LINE",
    "SUPPLIER_SELECTION",
    "COMPATIBILITY_CHECK",
    "WORK_INSTRUCTION",
    "QC_PLAN",
    "READINESS_APPROVAL",
)
READINESS_NODE = "READINESS_APPROVAL:PROJECT"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    dependency_type: str
    rule_code: str


def node_type(node_key: str) -> str:
    return node_key.split(":", 1)[0]


def validate_node_key(node_key: str) -> bool:
    parts = node_key.split(":", 1)
    return len(parts) == 2 and parts[0] in NODE_TYPES and bool(parts[1]) and parts[1] == parts[1].strip()


def bom_node(line_key: str) -> str:
    return f"BOM_LINE:{line_key}"


def adjacency(edges: list[Edge]) -> dict[str, list[Edge]]:
    adj: dict[str, list[Edge]] = {}
    for e in edges:
        adj.setdefault(e.source, []).append(e)
    for k in adj:
        adj[k].sort(key=lambda e: (e.target, e.dependency_type, e.rule_code))
    return adj


def find_cycle(edges: list[Edge]) -> list[str] | None:
    """Return a node path forming a cycle, or None. Iterative DFS with deterministic order."""
    adj = adjacency(edges)
    nodes = sorted({e.source for e in edges} | {e.target for e in edges})
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(nodes, WHITE)
    for start in nodes:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            out = adj.get(node, [])
            if idx < len(out):
                stack[-1] = (node, idx + 1)
                nxt = out[idx].target
                if color[nxt] == GRAY:
                    on_path = [n for n, _ in stack]
                    return [*on_path[on_path.index(nxt) :], nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, 0))
            else:
                color[node] = BLACK
                stack.pop()
    return None


@dataclass(frozen=True)
class Reach:
    node: str
    path: tuple[str, ...]
    rule_codes: tuple[str, ...]
    depth: int


def traverse_downstream(edges: list[Edge], seeds: list[str]) -> list[Reach]:
    """Multi-source BFS. Returns every node reachable from seeds (excluding seeds), each with the
    shortest explanatory path. Ties are broken by sorted seed order and sorted neighbor order, so
    results are fully deterministic."""
    adj = adjacency(edges)
    ordered_seeds = sorted(set(seeds))
    visited: dict[str, Reach] = {s: Reach(s, (s,), (), 0) for s in ordered_seeds}
    queue: deque[str] = deque(ordered_seeds)
    out: list[Reach] = []
    while queue:
        node = queue.popleft()
        cur = visited[node]
        for e in adj.get(node, []):
            if e.target in visited:
                continue
            r = Reach(e.target, (*cur.path, e.target), (*cur.rule_codes, e.rule_code), cur.depth + 1)
            visited[e.target] = r
            out.append(r)
            queue.append(e.target)
    return sorted(out, key=lambda r: (r.depth, r.node))
