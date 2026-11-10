import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { newIdempotencyKey, useChange, useGraph, useImpact, usePublish, useWithdraw } from "../../api/hooks";
import type { ImpactedNode } from "../../api/types";
import { can, useAuth } from "../../auth/AuthContext";
import { GraphView } from "../../components/GraphView";
import { Badge, Button, Card, Empty, ErrorBox, fmtTime, Loading, Mono, Notice, shortHash, StatusBadge, Table } from "../../components/ui";
import { DiffTable } from "./BomPage";
import { useProjectOutlet } from "./useProjectOutlet";

export function ChangeDetailPage() {
  const p = useProjectOutlet();
  const { cid = "" } = useParams();
  const { role } = useAuth();
  const staff = can(role, "engineer", "approver", "admin");
  const change = useChange(p.id, cid);
  const impact = useImpact(p.id, cid);
  const graph = useGraph(p.id, staff);
  const publish = usePublish(p.id, cid);
  const withdraw = useWithdraw(p.id, cid);
  const [key] = useState(newIdempotencyKey);

  const highlight = useMemo(() => new Set(impact.data?.impacted.map((i) => i.node) ?? []), [impact.data]);
  const changed = useMemo(() => new Set(impact.data?.changed_nodes ?? []), [impact.data]);

  if (change.isLoading) return <Loading />;
  if (change.error || !change.data) return <ErrorBox error={change.error} />;
  const c = change.data;
  const proposed = c.status === "PROPOSED";

  return (
    <div className="space-y-4">
      <Link to=".." relative="path" className="text-sm text-sky-700 hover:underline">
        ← Changes
      </Link>
      <Card
        title={
          <span className="flex items-center gap-2">
            {c.code} <StatusBadge status={c.status} />
          </span>
        }
        actions={
          proposed &&
          can(role, "engineer") && (
            <>
              <Button variant="secondary" onClick={() => withdraw.mutate()} busy={withdraw.isPending}>
                Withdraw
              </Button>
              <Button
                variant="danger"
                busy={publish.isPending}
                onClick={() => publish.mutate({ expected_baseline_version: p.baseline_version, key })}
              >
                Publish as R{(p.current_revision_number ?? 0) + 1}
              </Button>
            </>
          )
        }
      >
        <div className="space-y-2 text-sm">
          <p>{c.reason}</p>
          <p className="text-xs text-slate-500">
            Proposed by {c.submitted_by_email} · {fmtTime(c.created_at)} · base R{c.from_revision_number}
            {c.to_revision_number && ` → published as R${c.to_revision_number} (${fmtTime(c.published_at)})`}
          </p>
          {proposed && (
            <Notice tone="amber">
              Preview only — nothing has changed yet. Publishing creates R{(p.current_revision_number ?? 0) + 1}, invalidates the artifacts
              below, opens exceptions, and stales any approval of the current baseline.
            </Notice>
          )}
          <ErrorBox error={publish.error} title="Publish failed" />
          <ErrorBox error={withdraw.error} title="Withdraw failed" />
          {publish.data && <Notice tone="red">Published. Readiness is now {publish.data.readiness}.</Notice>}
        </div>
      </Card>

      {impact.isLoading ? (
        <Loading label="Computing impact…" />
      ) : impact.error ? (
        <ErrorBox error={impact.error} />
      ) : (
        impact.data && (
          <>
            <Card
              title={`Impact — ${impact.data.impacted.length} node${impact.data.impacted.length === 1 ? "" : "s"} affected`}
              actions={
                <>
                  <Badge tone={impact.data.persisted ? "green" : "amber"}>{impact.data.persisted ? "persisted assessment" : "live preview"}</Badge>
                  <Badge>{impact.data.rule_version}</Badge>
                </>
              }
            >
              <div className="mb-3 flex flex-wrap gap-4 text-xs text-slate-600">
                <span>
                  {impact.data.from_revision} → {impact.data.to_revision}
                </span>
                <span>
                  baseline <Mono>{shortHash(impact.data.baseline_hash_before)}</Mono> → <Mono>{shortHash(impact.data.baseline_hash_after)}</Mono>
                </span>
                <span>
                  readiness after publish: <StatusBadge status={impact.data.readiness} />
                </span>
              </div>
              <ImpactTable rows={impact.data.impacted} />
              {impact.data.unaffected_nodes.length > 0 && (
                <p className="mt-3 text-xs text-slate-500">
                  Not affected: {impact.data.unaffected_nodes.join(", ")}
                </p>
              )}
            </Card>
            <Card title="BOM line changes">
              <DiffTable lines={impact.data.line_diffs} />
            </Card>
            {staff && graph.data && (
              <Card title="Propagation path">
                <p className="mb-2 text-xs text-slate-500">Blue: changed BOM lines. Red outline: nodes that must be revalidated.</p>
                <GraphView graph={graph.data} highlight={highlight} changed={changed} height={460} />
              </Card>
            )}
          </>
        )
      )}
    </div>
  );
}

export function ImpactTable({ rows }: { rows: ImpactedNode[] }) {
  if (!rows.length) return <Empty>No downstream impact.</Empty>;
  return (
    <Table head={["Node", "Severity", "Reason", "Path", "Required action"]}>
      {rows.map((r) => (
        <tr key={r.node} data-testid="impact-row">
          <td className="px-2 py-1.5">
            <Mono>{r.node}</Mono>
            {!r.artifact_exists && r.node_type !== "READINESS_APPROVAL" && (
              <div>
                <Badge tone="red">record missing</Badge>
              </div>
            )}
          </td>
          <td className="px-2 py-1.5">
            <StatusBadge status={r.severity} />
          </td>
          <td className="px-2 py-1.5 text-xs">
            <div className="font-medium">{r.reason_code}</div>
            {r.evaluation && (
              <div className="text-slate-600">
                rule {String((r.evaluation as Record<string, unknown>).rule_code ?? "")}:{" "}
                <StatusBadge status={String((r.evaluation as Record<string, unknown>).result ?? "")} />{" "}
                {String((r.evaluation as Record<string, unknown>).message ?? "")}
              </div>
            )}
          </td>
          <td className="px-2 py-1.5 font-mono text-[11px] text-slate-600">{r.path.join(" → ")}</td>
          <td className="px-2 py-1.5 text-xs">{r.required_action}</td>
        </tr>
      ))}
    </Table>
  );
}
