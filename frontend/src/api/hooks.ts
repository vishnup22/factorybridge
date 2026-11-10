import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { data, newIdempotencyKey, request } from "./client";
import type {
  AcceptResponse,
  Approval,
  Artifact,
  AuditEvent,
  BomDiff,
  BomRevision,
  BomRevisionDetail,
  Candidate,
  CandidateDecision,
  Change,
  ChangeOperation,
  DocumentDetail,
  DocumentOut,
  Envelope,
  ExceptionItem,
  Extraction,
  Graph,
  Impact,
  Project,
  ProjectSummary,
  Rule,
  Supplier,
  User,
} from "./types";

/** Every project-scoped query key starts with ["p", pid] so one invalidation refreshes the whole view. */
export const pk = (pid: string, ...rest: unknown[]) => ["p", pid, ...rest];

export function useInvalidateProject(pid: string) {
  const qc = useQueryClient();
  return useCallback(() => qc.invalidateQueries({ queryKey: ["p", pid] }), [qc, pid]);
}

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => data<ProjectSummary[]>("/projects") });

export const useProject = (pid: string) =>
  useQuery({ queryKey: pk(pid, "detail"), queryFn: () => data<Project>(`/projects/${pid}`) });

const IN_FLIGHT = new Set(["PENDING", "PROCESSING"]);

export const isInFlight = (docs: DocumentOut[] | undefined) => docs?.some((d) => IN_FLIGHT.has(d.processing_status)) ?? false;

export const useDocuments = (pid: string) =>
  useQuery({
    queryKey: pk(pid, "documents"),
    queryFn: () => data<DocumentOut[]>(`/projects/${pid}/documents`),
    refetchInterval: (q) => (isInFlight(q.state.data) ? 1500 : false),
  });

export const useDocument = (docId: string) =>
  useQuery({ queryKey: ["doc", docId], queryFn: () => data<DocumentDetail>(`/documents/${docId}`) });

export const useExtraction = (docId: string, enabled: boolean) =>
  useQuery({ queryKey: ["extraction", docId], queryFn: () => data<Extraction>(`/documents/${docId}/extraction`), enabled });

export const useRevisions = (pid: string) =>
  useQuery({ queryKey: pk(pid, "revisions"), queryFn: () => data<BomRevision[]>(`/projects/${pid}/bom/revisions`) });

export const useRevision = (pid: string, rev: number | null) =>
  useQuery({
    queryKey: pk(pid, "revision", rev),
    queryFn: () => data<BomRevisionDetail>(`/projects/${pid}/bom/revisions/${rev}`),
    enabled: rev !== null,
  });

export const useDiff = (pid: string, from: number | null, to: number | null) =>
  useQuery({
    queryKey: pk(pid, "diff", from, to),
    queryFn: () => data<BomDiff>(`/projects/${pid}/bom/diff`, { query: { from: from ?? undefined, to: to ?? undefined } }),
    enabled: from !== null && to !== null && from !== to,
  });

export const useChanges = (pid: string) =>
  useQuery({ queryKey: pk(pid, "changes"), queryFn: () => data<Change[]>(`/projects/${pid}/changes`) });

export const useChange = (pid: string, cid: string) =>
  useQuery({ queryKey: pk(pid, "change", cid), queryFn: () => data<Change>(`/projects/${pid}/changes/${cid}`) });

export const useImpact = (pid: string, cid: string) =>
  useQuery({ queryKey: pk(pid, "impact", cid), queryFn: () => data<Impact>(`/projects/${pid}/changes/${cid}/impact`) });

export const useGraph = (pid: string, enabled = true) =>
  useQuery({ queryKey: pk(pid, "graph"), queryFn: () => data<Graph>(`/projects/${pid}/dependency-graph`), enabled });

export const useArtifacts = (pid: string, nodeKey?: string, history = false) =>
  useQuery({
    queryKey: pk(pid, "artifacts", nodeKey, history),
    queryFn: () =>
      data<Artifact[]>(`/projects/${pid}/artifacts`, { query: { node_key: nodeKey, include_history: history ? "true" : undefined } }),
  });

export const useRules = (pid: string) =>
  useQuery({ queryKey: pk(pid, "rules"), queryFn: () => data<Rule[]>(`/projects/${pid}/compatibility-rules`) });

export const useExceptions = (pid: string, status?: string) =>
  useQuery({
    queryKey: pk(pid, "exceptions", status),
    queryFn: () => data<ExceptionItem[]>(`/projects/${pid}/exceptions`, { query: { status } }),
  });

export const useApprovals = (pid: string) =>
  useQuery({ queryKey: pk(pid, "approvals"), queryFn: () => data<Approval[]>(`/projects/${pid}/approvals`) });

export const useAudit = (pid: string, page: number, action?: string) =>
  useQuery({
    queryKey: pk(pid, "audit", page, action),
    queryFn: () => request<Envelope<AuditEvent[]>>(`/projects/${pid}/audit`, { query: { page, page_size: 50, action } }),
  });

export const useSuppliers = (part?: string) =>
  useQuery({ queryKey: ["suppliers", part], queryFn: () => data<Supplier[]>("/catalog/suppliers", { query: { part_number: part } }) });

export const useUsers = (enabled: boolean) =>
  useQuery({ queryKey: ["users"], queryFn: () => data<User[]>("/admin/users"), enabled });

// ---------------- mutations ----------------
function useProjectMutation<TVars, TResult>(pid: string, fn: (v: TVars) => Promise<TResult>) {
  const invalidate = useInvalidateProject(pid);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; sku: string; target_quantity: number; description: string }) =>
      data<Project>("/projects", { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export const useUpload = (pid: string) =>
  useProjectMutation(pid, (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return data<DocumentDetail>(`/projects/${pid}/documents`, { form, method: "POST" });
  });

export function useAccept(pid: string, docId: string) {
  const qc = useQueryClient();
  const invalidate = useInvalidateProject(pid);
  return useMutation({
    mutationFn: (v: { decisions: CandidateDecision[]; reason: string; key: string }) =>
      data<AcceptResponse>(`/documents/${docId}/extraction/accept`, {
        body: { decisions: v.decisions, reason: v.reason },
        idempotencyKey: v.key,
      }),
    onSettled: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["extraction", docId] });
      qc.invalidateQueries({ queryKey: ["doc", docId] });
    },
  });
}

export const useApplyTemplate = (pid: string) =>
  useProjectMutation(pid, () => data<unknown>(`/projects/${pid}/plan/apply-template`, { body: { template: "warehouse_scout_v1" } }));

export const useProposeChange = (pid: string) =>
  useProjectMutation(pid, (body: { base_revision_number: number; reason: string; changes: ChangeOperation[] }) =>
    data<Change>(`/projects/${pid}/changes`, { body }),
  );

export const usePublish = (pid: string, cid: string) =>
  useProjectMutation(pid, (v: { expected_baseline_version: number; key: string }) =>
    data<Impact>(`/projects/${pid}/changes/${cid}/publish`, {
      body: { expected_baseline_version: v.expected_baseline_version },
      idempotencyKey: v.key,
    }),
  );

export const useWithdraw = (pid: string, cid: string) =>
  useProjectMutation(pid, () => data<Change>(`/projects/${pid}/changes/${cid}/withdraw`, { method: "POST" }));

export const useResolve = (pid: string) =>
  useProjectMutation(pid, (v: { id: string; resolution_note: string; artifact_content: Record<string, unknown> | null }) =>
    data<ExceptionItem>(`/projects/${pid}/exceptions/${v.id}/resolve`, {
      body: { resolution_note: v.resolution_note, artifact_content: v.artifact_content },
    }),
  );

export const useWaive = (pid: string) =>
  useProjectMutation(pid, (v: { id: string; rationale: string }) =>
    data<ExceptionItem>(`/projects/${pid}/exceptions/${v.id}/waive`, { body: { rationale: v.rationale } }),
  );

export const useRequestApproval = (pid: string) =>
  useProjectMutation(pid, (v: { expected_baseline_version: number; note: string; assigned_approver_id?: string }) =>
    data<Approval>(`/projects/${pid}/approvals/request`, { body: v }),
  );

export const useDecide = (pid: string) =>
  useProjectMutation(
    pid,
    (v: { id: string; decision: "APPROVE" | "REJECT"; reason: string; expected_baseline_hash: string; key: string }) =>
      data<Approval>(`/projects/${pid}/approvals/${v.id}/decision`, {
        body: { decision: v.decision, reason: v.reason, expected_baseline_hash: v.expected_baseline_hash },
        idempotencyKey: v.key,
      }),
  );

export { newIdempotencyKey };
export type { Candidate };
