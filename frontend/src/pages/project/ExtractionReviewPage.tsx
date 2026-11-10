import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { newIdempotencyKey, useAccept, useDocument, useExtraction } from "../../api/hooks";
import type { Candidate, CandidateDecision } from "../../api/types";
import { can, useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Mono, Notice, StatusBadge, Textarea } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

type Action = "ACCEPT" | "EDIT" | "REJECT";
interface Draft {
  action: Action | null;
  value: string;
  note: string;
}
const EMPTY_DRAFT: Draft = { action: null, value: "", note: "" };

export function canAcceptAsIs(c: Candidate): boolean {
  const codes = new Set((c.issues_json as { code?: string }[]).map((i) => i.code));
  return c.evidence_status === "VERIFIED" && c.review_state !== "CONFLICT" && !codes.has("MANDATORY_FIELD_MISSING");
}

export function buildDecisions(cands: Candidate[], drafts: Record<string, Draft>): { decisions: CandidateDecision[]; errors: string[] } {
  const decisions: CandidateDecision[] = [];
  const errors: string[] = [];
  for (const c of cands) {
    const d = drafts[c.id];
    if (!d?.action) {
      errors.push(`${c.field_path}: no decision`);
      continue;
    }
    if (d.action === "EDIT") {
      let value: unknown;
      try {
        value = JSON.parse(d.value);
      } catch {
        errors.push(`${c.field_path}: corrected value is not valid JSON`);
        continue;
      }
      if (d.note.trim().length < 5) {
        errors.push(`${c.field_path}: correction note required`);
        continue;
      }
      decisions.push({ candidate_id: c.id, action: "EDIT", value, note: d.note.trim() });
    } else {
      if (d.action === "ACCEPT" && !canAcceptAsIs(c)) {
        errors.push(`${c.field_path}: cannot be accepted as-is (edit with a note or reject)`);
        continue;
      }
      decisions.push({ candidate_id: c.id, action: d.action });
    }
  }
  return { decisions, errors };
}

export function ExtractionReviewPage() {
  const p = useProjectOutlet();
  const { docId = "" } = useParams();
  const { role } = useAuth();
  const navigate = useNavigate();
  const doc = useDocument(docId);
  const ex = useExtraction(docId, can(role, "engineer", "admin"));
  const accept = useAccept(p.id, docId);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [reason, setReason] = useState("Reviewed each value against the cited source excerpt");
  const [localErrors, setLocalErrors] = useState<string[]>([]);
  const [key] = useState(newIdempotencyKey);

  const cands = useMemo(() => ex.data?.candidates ?? [], [ex.data]);
  const reviewable = ex.data?.run?.status === "REVIEW_REQUIRED" && can(role, "engineer");

  if (ex.isLoading || doc.isLoading) return <Loading />;
  if (ex.error) return <ErrorBox error={ex.error} />;
  if (!ex.data) return null;
  const run = ex.data.run;

  const set = (id: string, patch: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [id]: { ...EMPTY_DRAFT, ...prev[id], ...patch } }));

  function acceptAllVerified() {
    const next = { ...drafts };
    for (const c of cands) if (canAcceptAsIs(c) && !next[c.id]?.action) next[c.id] = { action: "ACCEPT", value: "", note: "" };
    setDrafts(next);
  }

  async function submit() {
    const { decisions, errors } = buildDecisions(cands, drafts);
    setLocalErrors(errors);
    if (errors.length) return;
    const r = await accept.mutateAsync({ decisions, reason, key });
    navigate(`/projects/${p.id}/${r.change_id ? `changes/${r.change_id}` : "bom"}`);
  }

  return (
    <div className="space-y-4">
      <Link to={`/projects/${p.id}/documents`} className="text-sm text-sky-700 hover:underline">
        ← Documents
      </Link>
      <Card
        title={`Extraction — ${ex.data.document.filename}`}
        actions={
          run && (
            <>
              <StatusBadge status={run.provider_mode} />
              <StatusBadge status={run.status} />
            </>
          )
        }
      >
        {!run ? (
          <Empty>No extraction run yet — the worker has not processed this document.</Empty>
        ) : (
          <div className="space-y-3 text-sm">
            {run.provider_mode === "FIXTURE" && (
              <Notice tone="violet">
                Fixture mode: values were produced by a deterministic parser, not a language model. No model was called.
              </Notice>
            )}
            {run.provider_mode === "LIVE" && (
              <Notice tone="violet">
                Model-extracted candidates ({run.model}). Evidence was re-verified against the PDF text by the server; nothing below is
                authoritative until you review it.
              </Notice>
            )}
            {run.status === "FAILED" && (
              <Notice tone="red">
                Extraction failed: <strong>{run.error_code}</strong> — {run.error_detail}. No values were produced.
              </Notice>
            )}
            <div className="flex flex-wrap gap-4 text-xs text-slate-600">
              <span>model: {run.model}</span>
              <span>prompt: {run.prompt_version}</span>
              <span>schema: {run.schema_version}</span>
              {run.input_tokens != null && <span>tokens: {run.input_tokens} in / {run.output_tokens} out</span>}
              {run.latency_ms != null && <span>latency: {run.latency_ms} ms</span>}
              {run.cost_usd_estimate != null && <span>est. cost: ${Number(run.cost_usd_estimate).toFixed(4)} (configured rates, not billed)</span>}
            </div>
          </div>
        )}
      </Card>

      {cands.length > 0 && (
        <Card
          title={`Candidates (${cands.length})`}
          actions={reviewable && <Button variant="secondary" onClick={acceptAllVerified}>Accept all verified</Button>}
        >
          <ul className="space-y-3">
            {cands.map((c) => (
              <CandidateRow key={c.id} c={c} draft={drafts[c.id]} onChange={(patch) => set(c.id, patch)} editable={reviewable} />
            ))}
          </ul>
        </Card>
      )}

      {reviewable && (
        <Card title="Submit review">
          <div className="space-y-3">
            <Field label="Review reason (recorded in the audit log)">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            {localErrors.length > 0 && (
              <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                <ul className="list-disc pl-4">
                  {localErrors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
            <ErrorBox error={accept.error} title="Review rejected" />
            <Button onClick={() => submit().catch(() => undefined)} busy={accept.isPending}>
              {p.current_revision_number ? "Create proposed change from reviewed values" : "Publish reviewed values as R1"}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

function CandidateRow({ c, draft, onChange, editable }: { c: Candidate; draft?: Draft; onChange: (p: Partial<Draft>) => void; editable: boolean }) {
  const issues = c.issues_json as { code?: string; message?: string }[];
  const acceptable = canAcceptAsIs(c);
  const shown = c.corrected_value_json ?? c.value_json;
  return (
    <li className="rounded-md border border-slate-200 p-3" data-testid="candidate">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="slate">{c.kind === "BOM_LINE" ? "BOM line" : "Requirement"}</Badge>
        <Mono>{c.field_path}</Mono>
        <StatusBadge status={c.evidence_status} />
        <Badge>confidence {c.confidence_label}</Badge>
        {c.review_state !== "PENDING" && <StatusBadge status={c.review_state} />}
      </div>
      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div>
          <div className="text-xs font-medium text-slate-500">Extracted value</div>
          <pre className="mt-1 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(shown, null, 1)}</pre>
        </div>
        <div>
          <div className="text-xs font-medium text-slate-500">
            Evidence {c.source_page ? `— page ${c.source_page}` : c.source_section ? `— ${c.source_section}` : ""}
          </div>
          <blockquote className="mt-1 border-l-4 border-slate-300 bg-slate-50 p-2 font-mono text-xs text-slate-700">
            {c.source_excerpt ?? <em className="text-red-700">no excerpt cited</em>}
          </blockquote>
        </div>
      </div>
      {issues.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-amber-900">
          {issues.map((i, n) => (
            <li key={n}>
              ⚠ <strong>{i.code}</strong> {i.message}
            </li>
          ))}
        </ul>
      )}
      {c.correction_note && <p className="mt-2 text-xs text-violet-800">Correction note: {c.correction_note}</p>}
      {editable && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={`Decision for ${c.field_path}`}>
            {(["ACCEPT", "EDIT", "REJECT"] as Action[]).map((a) => (
              <label key={a} className="flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name={`d-${c.id}`}
                  checked={draft?.action === a}
                  disabled={a === "ACCEPT" && !acceptable}
                  onChange={() => onChange({ action: a, value: a === "EDIT" && !draft?.value ? JSON.stringify(c.value_json, null, 1) : draft?.value })}
                />
                {a === "ACCEPT" ? "Accept" : a === "EDIT" ? "Edit" : "Reject"}
              </label>
            ))}
            {!acceptable && <span className="text-xs text-slate-500">Accept disabled: unverified evidence, conflict or missing field.</span>}
          </div>
          {draft?.action === "EDIT" && (
            <div className="grid gap-2 md:grid-cols-2">
              <Textarea rows={6} aria-label="Corrected value (JSON)" value={draft.value} onChange={(e) => onChange({ value: e.target.value })} />
              <Textarea
                rows={3}
                className="font-sans text-sm"
                aria-label="Correction note"
                placeholder="Why is this correction right? (required)"
                value={draft.note}
                onChange={(e) => onChange({ note: e.target.value })}
              />
            </div>
          )}
        </div>
      )}
    </li>
  );
}
