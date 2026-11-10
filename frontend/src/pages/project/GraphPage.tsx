import { useState } from "react";
import { useArtifacts, useGraph, useRules } from "../../api/hooks";
import { GraphView } from "../../components/GraphView";
import { Badge, Card, Empty, ErrorBox, fmtTime, Json, Loading, Mono, shortHash, StatusBadge } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

export function GraphPage() {
  const p = useProjectOutlet();
  const graph = useGraph(p.id);
  const rules = useRules(p.id);
  const [selected, setSelected] = useState<string | null>(null);

  if (graph.isLoading) return <Loading />;
  if (graph.error) return <ErrorBox error={graph.error} />;
  if (!graph.data?.nodes.length) return <Empty>No dependency graph yet. Apply the plan template from the overview.</Empty>;

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="xl:col-span-2">
        <Card title="Dependency graph" actions={<span className="text-xs text-slate-500">edge = target must be revalidated when source changes</span>}>
          <GraphView graph={graph.data} onSelect={setSelected} />
        </Card>
      </div>
      <div className="space-y-4">
        {selected ? <NodeInspector pid={p.id} nodeKey={selected} /> : <Empty>Select a node to inspect its current artifact and validation.</Empty>}
        <Card title="Compatibility rules">
          {rules.data?.length ? (
            <ul className="space-y-2 text-xs">
              {rules.data.map((r) => (
                <li key={r.rule_code}>
                  <div className="flex items-center gap-2">
                    <Mono>{r.rule_code}</Mono>
                    <Badge>{r.check_kind}</Badge>
                    {r.is_critical && <Badge tone="red">critical</Badge>}
                  </div>
                  <div className="text-slate-600">{r.description}</div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No rules.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}

function NodeInspector({ pid, nodeKey }: { pid: string; nodeKey: string }) {
  const history = useArtifacts(pid, nodeKey, true);
  if (history.isLoading) return <Loading />;
  if (history.error) return <ErrorBox error={history.error} />;
  const revs = history.data ?? [];
  const current = revs.find((a) => a.is_current);
  return (
    <Card title={<Mono>{nodeKey}</Mono>}>
      {!revs.length ? (
        <Empty>{nodeKey.startsWith("BOM_LINE") || nodeKey.startsWith("READINESS") ? "Not an artifact node." : "No artifact record — readiness is blocked (ARTIFACT_MISSING)."}</Empty>
      ) : (
        <div className="space-y-3 text-sm">
          {current && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{current.title}</span>
                <StatusBadge status={current.status} />
                <Badge>rev {current.revision}</Badge>
              </div>
              <div className="text-xs text-slate-500">
                validated against baseline <Mono>{shortHash(current.based_on_hash)}</Mono>
              </div>
              <ValidationList v={current.validation_json} />
              <Json value={current.content_json} />
            </>
          )}
          <div>
            <div className="text-xs font-medium text-slate-500">History</div>
            <ul className="mt-1 space-y-1 text-xs">
              {revs.map((a) => (
                <li key={a.id} className="flex items-center gap-2">
                  rev {a.revision} <StatusBadge status={a.status} /> <span className="text-slate-500">{fmtTime(a.created_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}

export function ValidationList({ v }: { v: Record<string, unknown> }) {
  const findings = (v.findings as { code: string; message: string }[] | undefined) ?? [];
  if (!findings.length) return <p className="text-xs text-emerald-700">✓ All validations pass</p>;
  return (
    <ul className="space-y-1 text-xs text-red-800">
      {findings.map((f, i) => (
        <li key={i}>
          ✗ <strong>{f.code}</strong> {f.message}
        </li>
      ))}
    </ul>
  );
}
