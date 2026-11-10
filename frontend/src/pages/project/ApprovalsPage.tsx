import { useState } from "react";
import { newIdempotencyKey, useApprovals, useDecide, useRequestApproval } from "../../api/hooks";
import type { Approval } from "../../api/types";
import { can, useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, Empty, ErrorBox, Field, fmtTime, Input, Loading, Mono, Notice, shortHash, StatusBadge } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

export function ApprovalsPage() {
  const p = useProjectOutlet();
  const { role } = useAuth();
  const approvals = useApprovals(p.id);
  const pending = approvals.data?.find((a) => a.status === "PENDING");

  return (
    <div className="space-y-4">
      {can(role, "engineer") && !pending && p.current_revision_number && <RequestCard />}
      <Card title="Approval history">
        {approvals.isLoading ? (
          <Loading />
        ) : approvals.error ? (
          <ErrorBox error={approvals.error} />
        ) : !approvals.data?.length ? (
          <Empty>No approval requests yet.</Empty>
        ) : (
          <ul className="space-y-3">
            {approvals.data.map((a) => (
              <ApprovalRow key={a.id} a={a} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function RequestCard() {
  const p = useProjectOutlet();
  const req = useRequestApproval(p.id);
  const [note, setNote] = useState("");
  const blocked = p.blockers.some((b) => b.code !== "APPROVAL_REQUIRED");
  return (
    <Card title={`Request approval of R${p.current_revision_number}`}>
      {blocked ? (
        <Notice tone="red">Resolve all blockers before requesting approval. The server enforces this.</Notice>
      ) : (
        <Notice>The approval will be bound to baseline hash {shortHash(p.baseline_hash)} (version {p.baseline_version}).</Notice>
      )}
      <div className="mt-3">
        <Field label="Note to approver" hint="An eligible approver is assigned automatically — never the author of an unreleased change.">
          <Input value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      <div className="mt-3 space-y-2">
        <ErrorBox error={req.error} title="Request rejected" />
        <Button busy={req.isPending} onClick={() => req.mutate({ expected_baseline_version: p.baseline_version, note })}>
          Request approval
        </Button>
      </div>
    </Card>
  );
}

function ApprovalRow({ a }: { a: Approval }) {
  const p = useProjectOutlet();
  const { me, role } = useAuth();
  const decide = useDecide(p.id);
  const [reason, setReason] = useState("");
  const [key] = useState(newIdempotencyKey);
  const mine = me?.user.id === a.assigned_approver_id;
  const canDecide = a.status === "PENDING" && can(role, "approver");

  return (
    <li className="rounded-md border border-slate-200 p-3" data-testid="approval">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">R{a.bom_revision_number}</span>
        <StatusBadge status={a.status} />
        {a.decision && <Badge tone={a.decision === "APPROVE" ? "green" : "red"}>decision: {a.decision}</Badge>}
        {a.is_current_baseline && <Badge tone="blue">current baseline</Badge>}
        <Mono title={a.baseline_hash}>{shortHash(a.baseline_hash)}</Mono>
        <span className="ml-auto text-xs text-slate-500">requested {fmtTime(a.created_at)} by {a.requested_by_email}</span>
      </div>
      <div className="mt-1 text-xs text-slate-600">
        Assigned to {a.assigned_approver_email}
        {a.reason && ` · “${a.reason}”`}
        {a.decision_reason && ` · decision note: “${a.decision_reason}”`}
        {a.reviewed_at && ` · decided ${fmtTime(a.reviewed_at)}`}
      </div>
      {a.status === "STALE" && (
        <p className="mt-1 text-xs text-red-800">
          Stale since {fmtTime(a.staled_at)}: {a.stale_reason}. This approval can never release a later baseline.
        </p>
      )}
      {canDecide && (
        <div className="mt-3 space-y-2 rounded-md bg-slate-50 p-3">
          {!mine && <Notice tone="amber">You are not the assigned approver; the server will refuse your decision.</Notice>}
          <Field label="Decision reason">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What you reviewed" />
          </Field>
          <ErrorBox error={decide.error} title="Decision refused" />
          <div className="flex gap-2">
            <Button
              busy={decide.isPending}
              disabled={reason.trim().length < 3}
              onClick={() => decide.mutate({ id: a.id, decision: "APPROVE", reason, expected_baseline_hash: a.baseline_hash, key })}
            >
              Approve this exact baseline
            </Button>
            <Button
              variant="danger"
              busy={decide.isPending}
              disabled={reason.trim().length < 3}
              onClick={() => decide.mutate({ id: a.id, decision: "REJECT", reason, expected_baseline_hash: a.baseline_hash, key: newIdempotencyKey() })}
            >
              Reject
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
