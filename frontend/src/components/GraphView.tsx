import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { useMemo } from "react";
import type { Graph } from "../api/types";

const COLUMN: Record<string, number> = {
  BOM_LINE: 0,
  SUPPLIER_SELECTION: 1,
  COMPATIBILITY_CHECK: 1,
  WORK_INSTRUCTION: 2,
  QC_PLAN: 3,
  READINESS_APPROVAL: 4,
};

const STATUS_STYLE: Record<string, { bg: string; border: string }> = {
  VALID: { bg: "#ecfdf5", border: "#10b981" },
  READY: { bg: "#ecfdf5", border: "#10b981" },
  CURRENT: { bg: "#f8fafc", border: "#94a3b8" },
  INVALID: { bg: "#fef2f2", border: "#dc2626" },
  BLOCKED: { bg: "#fef2f2", border: "#dc2626" },
  MISSING: { bg: "#fef2f2", border: "#dc2626" },
  REVALIDATION_REQUIRED: { bg: "#fffbeb", border: "#d97706" },
  REVIEW_REQUIRED: { bg: "#fffbeb", border: "#d97706" },
};

export function layoutGraph(graph: Graph, highlight: Set<string>, changed: Set<string>): { nodes: Node[]; edges: Edge[] } {
  const rows: Record<number, number> = {};
  const sorted = [...graph.nodes].sort((a, b) => (COLUMN[a.node_type] ?? 5) - (COLUMN[b.node_type] ?? 5) || a.key.localeCompare(b.key));
  const nodes: Node[] = sorted.map((n) => {
    const col = COLUMN[n.node_type] ?? 5;
    const row = (rows[col] = (rows[col] ?? -1) + 1);
    const st = STATUS_STYLE[n.status] ?? STATUS_STYLE.CURRENT;
    const hot = highlight.has(n.key);
    const isChanged = changed.has(n.key);
    return {
      id: n.key,
      position: { x: col * 250, y: row * 90 },
      data: {
        label: (
          <div className="text-left">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">{n.node_type.replaceAll("_", " ")}</div>
            <div className="text-xs font-semibold">{n.label}</div>
            <div className="text-[10px] text-slate-600">
              {n.status.replaceAll("_", " ")}
              {n.artifact_revision ? ` · rev ${n.artifact_revision}` : ""}
              {n.open_exceptions?.length ? ` · ${n.open_exceptions.length} open` : ""}
            </div>
          </div>
        ),
      },
      style: {
        width: 200,
        background: isChanged ? "#e0f2fe" : st.bg,
        border: `${hot || isChanged ? 3 : 1}px solid ${isChanged ? "#0284c7" : hot ? "#dc2626" : st.border}`,
        borderRadius: 8,
        padding: 6,
        opacity: highlight.size && !hot && !isChanged ? 0.45 : 1,
      },
      sourcePosition: "right" as Node["sourcePosition"],
      targetPosition: "left" as Node["targetPosition"],
    };
  });
  const edges: Edge[] = graph.edges.map((e, i) => {
    const hot = (highlight.has(e.target) || changed.has(e.target)) && (highlight.has(e.source) || changed.has(e.source));
    return {
      id: `${e.source}->${e.target}-${i}`,
      source: e.source,
      target: e.target,
      animated: hot,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { stroke: hot ? "#dc2626" : "#94a3b8", strokeWidth: hot ? 2 : 1 },
    };
  });
  return { nodes, edges };
}

export function GraphView({
  graph,
  highlight = new Set<string>(),
  changed = new Set<string>(),
  onSelect,
  height = 560,
}: {
  graph: Graph;
  highlight?: Set<string>;
  changed?: Set<string>;
  onSelect?: (key: string) => void;
  height?: number;
}) {
  const { nodes, edges } = useMemo(() => layoutGraph(graph, highlight, changed), [graph, highlight, changed]);
  return (
    <div style={{ height }} className="rounded-md border border-slate-200 bg-white" data-testid="graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, n) => onSelect?.(n.id)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
