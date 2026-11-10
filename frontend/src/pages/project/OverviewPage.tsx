import { Link } from "react-router";
import { useApplyTemplate, useGraph } from "../../api/hooks";
import { can, useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, Empty, ErrorBox, Notice, StatusBadge } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

const BLOCKER_LINK: Record<string, string> = {
  NO_BOM_REVISION: "documents",
  EXTRACTION_PENDING: "documents",
  NO_DEPENDENCY_GRAPH: "",
  ARTIFACT_MISSING: "graph",
  ARTIFACT_NOT_VALID: "exceptions",
  OPEN_EXCEPTION: "exceptions",
  APPROVAL_REQUIRED: "approvals",
};

export function OverviewPage() {
  const p = useProjectOutlet();
  const { role } = useAuth();
  const kpis = p.kpis as {
    bom_revision: string | null;
    open_exceptions_by_severity: Record<string, number>;
    critical_open: number;
    documents_by_status: Record<string, number>;
    approval_fresh: boolean;
    pending_approval: boolean;
    next_action?: { owner: string; action: string };
  };
  const staff = can(role, "engineer", "approver", "admin");
  const graph = useGraph(p.id, staff);
  const apply = useApplyTemplate(p.id);
  const hasGraph = (graph.data?.edges.length ?? 0) > 0;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        {kpis.next_action && (
          <Notice tone="blue">
            <strong>Next step</strong> ({kpis.next_action.owner}): {kpis.next_action.action}
          </Notice>
        )}
        <Card title={`Blockers (${p.blockers.length})`}>
          {p.blockers.length === 0 ? (
            <Empty>{p.status === "READY" ? "Released — no blockers." : "No blockers."}</Empty>
          ) : (
            <ul className="divide-y divide-slate-100" data-testid="blockers">
              {p.blockers.map((b, i) => (
                <li key={i} className="flex items-start gap-3 py-2 text-sm">
                  <StatusBadge status={b.severity} />
                  <div className="flex-1">
                    <div className="font-medium">{b.code.replaceAll("_", " ")}</div>
                    <div className="text-slate-600">{b.message}</div>
                    {b.ref && !p.customer_view && <div className="font-mono text-xs text-slate-500">{b.ref}</div>}
                  </div>
                  {BLOCKER_LINK[b.code] !== undefined && (
                    <Link className="text-xs text-sky-700 hover:underline" to={BLOCKER_LINK[b.code]}>
                      Open →
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        {staff && p.current_revision_number && !graph.isLoading && !hasGraph && (
          <Card title="Manufacturing plan">
            <p className="mb-3 text-sm text-slate-600">
              No dependency graph yet. Apply the synthetic Warehouse Scout plan: supplier selections, compatibility rules,
              work instructions and QC plan, each validated against the current baseline.
            </p>
            {can(role, "engineer") ? (
              <Button onClick={() => apply.mutate()} busy={apply.isPending}>
                Apply Warehouse Scout plan template
              </Button>
            ) : (
              <p className="text-xs text-slate-500">An engineer must apply the plan.</p>
            )}
            <div className="mt-2">
              <ErrorBox error={apply.error} />
            </div>
          </Card>
        )}
      </div>
      <div className="space-y-4">
        <Card title="Snapshot">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt className="text-slate-500">Baseline</dt>
            <dd>{kpis.bom_revision ?? "—"}</dd>
            <dt className="text-slate-500">Approval</dt>
            <dd>{kpis.approval_fresh ? <Badge tone="green">current</Badge> : kpis.pending_approval ? <Badge tone="amber">pending</Badge> : <Badge>none</Badge>}</dd>
            <dt className="text-slate-500">Critical open</dt>
            <dd>{kpis.critical_open}</dd>
            <dt className="text-slate-500">Open by severity</dt>
            <dd className="flex flex-wrap gap-1">
              {Object.entries(kpis.open_exceptions_by_severity).length
                ? Object.entries(kpis.open_exceptions_by_severity).map(([s, n]) => (
                    <Badge key={s} tone={s === "CRITICAL" ? "red" : "amber"}>
                      {s} {n}
                    </Badge>
                  ))
                : "—"}
            </dd>
            <dt className="text-slate-500">Documents</dt>
            <dd className="flex flex-wrap gap-1">
              {Object.entries(kpis.documents_by_status).length
                ? Object.entries(kpis.documents_by_status).map(([s, n]) => (
                    <Badge key={s}>
                      {s.replaceAll("_", " ")} {n}
                    </Badge>
                  ))
                : "—"}
            </dd>
          </dl>
        </Card>
        <Card title="How readiness works">
          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-600">
            <li>READY requires an approval bound to the exact current baseline hash.</li>
            <li>Any published change creates a new immutable revision and stales prior approvals.</li>
            <li>The person who authored an unreleased change cannot approve it.</li>
            <li>AI extraction only produces candidates; engineers review every value before it becomes a baseline.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
