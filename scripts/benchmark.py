"""Reproducible micro-benchmarks. Prints measured numbers only; nothing is estimated.

  cd backend
  uv run python ../scripts/benchmark.py                       # impact engine on a 2000-node / 5000-edge DAG
  uv run python ../scripts/benchmark.py --api http://127.0.0.1:8000 --credentials var/demo_credentials.txt

The API part reads an existing project that has a published change (run scripts/demo_flow.py first) and
measures sequential GET latency from this machine, including HTTP and auth overhead.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.bom import BomLine  # noqa: E402
from app.domain.graph import READINESS_NODE, Edge, find_cycle  # noqa: E402
from app.domain.impact import assess_impact  # noqa: E402

TYPES = ["SUPPLIER_SELECTION", "COMPATIBILITY_CHECK", "WORK_INSTRUCTION", "QC_PLAN"]


def pct(samples: list[float], p: float) -> float:
    s = sorted(samples)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def summary(samples_s: list[float]) -> dict[str, float]:
    ms = [x * 1000 for x in samples_s]
    return {
        "runs": len(ms),
        "p50_ms": round(statistics.median(ms), 2),
        "p95_ms": round(pct(ms, 95), 2),
        "max_ms": round(max(ms), 2),
    }


def synthetic_dag(n_nodes: int, n_edges: int, n_bom: int, seed: int) -> tuple[list[BomLine], list[Edge]]:
    rng = random.Random(seed)  # noqa: S311 - reproducible benchmark graph, not security
    lines = [
        BomLine(f"L{i:04d}", f"P-{i:04d}", "Synthetic part", Decimal(1), "each", "A", {"voltage_v": 24})
        for i in range(n_bom)
    ]
    nodes = [f"BOM_LINE:{ln.line_key}" for ln in lines]
    nodes += [f"{TYPES[i % len(TYPES)]}:N{i:05d}" for i in range(n_nodes - n_bom - 1)]
    nodes.append(READINESS_NODE)
    edges: set[tuple[int, int]] = set()
    # Edges only go from a lower to a higher index, so the graph is acyclic by construction.
    while len(edges) < n_edges:
        a = rng.randrange(0, len(nodes) - 1)
        b = rng.randrange(max(a + 1, n_bom), len(nodes))
        edges.add((a, b))
    return lines, [Edge(nodes[a], nodes[b], "ASSEMBLY", "BENCH") for a, b in sorted(edges)]


def bench_impact(runs: int) -> dict[str, object]:
    lines, edges = synthetic_dag(n_nodes=2000, n_edges=5000, n_bom=200, seed=20261109)
    changed = list(lines)
    for i in range(0, 50, 10):
        old = changed[i]
        changed[i] = BomLine(
            old.line_key, old.part_number + "-X", old.description, old.quantity, old.unit, "B", {"voltage_v": 48}
        )
    artifact_nodes = {e.target for e in edges}
    impact_t, cycle_t, impacted = [], [], 0
    for _ in range(runs):
        t = time.perf_counter()
        result = assess_impact(lines, changed, edges, {}, artifact_nodes)
        impact_t.append(time.perf_counter() - t)
        impacted = len(result.impacted)
        t = time.perf_counter()
        assert find_cycle(edges) is None
        cycle_t.append(time.perf_counter() - t)
    return {
        "graph": {"nodes": 2000, "edges": len(edges), "changed_bom_lines": 5, "impacted_nodes": impacted},
        "assess_impact": summary(impact_t),
        "cycle_check_full_graph": summary(cycle_t),
    }


def bench_api(base: str, creds_path: str | None, runs: int) -> dict[str, object]:
    import httpx

    password = os.environ.get("FB_DEMO_PASSWORD")
    if not password and creds_path:
        for line in Path(creds_path).read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "engineer@demo.example":
                password = parts[1]
    if not password:
        raise SystemExit("Provide --credentials or FB_DEMO_PASSWORD")
    with httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=30) as h:
        token = h.post("/auth/login", json={"email": "engineer@demo.example", "password": password}).json()
        h.headers["Authorization"] = f"Bearer {token['access_token']}"
        target = None
        for p in h.get("/projects").json()["data"]:
            changes = h.get(f"/projects/{p['id']}/changes").json()["data"]
            published = [c for c in changes if c["status"] == "PUBLISHED"]
            if published:
                target = (p["id"], published[0]["id"])
                break
        if target is None:
            raise SystemExit("No project with a published change; run scripts/demo_flow.py first")
        pid, cid = target
        out: dict[str, object] = {"project_id": pid}
        for name, path in (
            ("GET project", f"/projects/{pid}"),
            ("GET impact", f"/projects/{pid}/changes/{cid}/impact"),
            ("GET dependency-graph", f"/projects/{pid}/dependency-graph"),
        ):
            h.get(path).raise_for_status()  # warm-up
            samples = []
            for _ in range(runs):
                t = time.perf_counter()
                h.get(path).raise_for_status()
                samples.append(time.perf_counter() - t)
            out[name] = summary(samples)
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--api")
    ap.add_argument("--credentials")
    args = ap.parse_args()
    report: dict[str, object] = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "impact_engine": bench_impact(args.runs),
    }
    if args.api:
        report["api"] = bench_api(args.api, args.credentials, max(args.runs, 50))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
