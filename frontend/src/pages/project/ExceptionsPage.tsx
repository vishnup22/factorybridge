import { useState } from "react";
import { Link } from "react-router";
import { useArtifacts, useExceptions, useResolve, useRevision, useSuppliers, useWaive } from "../../api/hooks";
import type { ExceptionItem } from "../../api/types";
import { can, useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, cx, Empty, ErrorBox, Field, fmtTime, Input, Loading, Mono, Notice, Select, StatusBadge, Textarea } from "../../components/ui";
import { ValidationList } from "./GraphPage";
import { lineKeyOf, refreshReferences } from "./resolution";
import { useProjectOutlet } from "./useProjectOutlet";

const FILTERS = ["OPEN", "RESOLVED", "WAIVED", ""] as const;

export function ExceptionsPage() {
  const p = useProjectOutlet();
  const [status, setStatus] = useState<(typeof FILTERS)[number]>("OPEN");
  const list = useExceptions(p.id, status || undefined);
  return (
    <Card
      title="Manufacturing exceptions"
      actions={
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f || "all"}
              onClick={() => setStatus(f)}
              className={cx("rounded px-2 py-1 text-xs", status === f ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}
            >
              {f || "All"}
            </button>
          ))}
        </div>
      }
    >
      {list.isLoading ? (
        <Loading />
      ) : list.error ? (
        <ErrorBox error={list.error} />
      ) : !list.data?.length ? (
        <Empty>{status === "OPEN" ? "No open exceptions." : "Nothing here."}</Empty>
      ) : (
        <ul className="space-y-3">
          {list.data.map((e) => (
            <ExceptionCard key={e.id} e={e} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ExceptionCard({ e }: { e: ExceptionItem }) {
  const { role } = useAuth();
  const [open, setOpen] = useState<"resolve" | "waive" | null>(null);
  const isOpen = e.status === "OPEN" || e.status === "REOPENED" || e.status === "IN_PROGRESS";
  const waivable = e.severity !== "CRITICAL";
  return (
    <li className="rounded-md border border-slate-200 p-3" data-testid="exception">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={e.severity} />
        <span className="font-medium">{e.reason_code.replaceAll("_", " ")}</span>
        <StatusBadge status={e.status} />
        {e.reopened_count > 0 && <Badge tone="red">reopened ×{e.reopened_count}</Badge>}
        {e.node_key && <Mono>{e.node_key}</Mono>}
        <span className="ml-auto text-xs text-slate-500">
          owner: {e.owner_role} · {fmtTime(e.updated_at)}
        </span>
      </div>
      <p className="mt-1 text-sm">{e.description}</p>
      <p className="text-xs text-slate-600">Required: {e.required_action}</p>
      {e.resolution_note && <p className="mt-1 text-xs text-emerald-800">Resolution: {e.resolution_note}</p>}
      {isOpen && (
        <div className="mt-2 flex gap-2">
          {e.node_key && can(role, "engineer") && (
            <Button variant="secondary" onClick={() => setOpen(open === "resolve" ? null : "resolve")}>
              Resolve with evidence
            </Button>
          )}
          {!e.node_key && can(role, "engineer") && (
            <Link to="../documents" relative="path">
              <Button variant="secondary">Resolve in extraction review</Button>
            </Link>
          )}
          {can(role, "approver") && waivable && (
            <Button variant="ghost" onClick={() => setOpen(open === "waive" ? null : "waive")}>
              Waive…
            </Button>
          )}
        </div>
      )}
      {open === "resolve" && e.node_key && <ResolveForm e={e} onDone={() => setOpen(null)} />}
      {open === "waive" && <WaiveForm e={e} onDone={() => setOpen(null)} />}
    </li>
  );
}

function ResolveForm({ e, onDone }: { e: ExceptionItem; onDone: () => void }) {
  const p = useProjectOutlet();
  const node = e.node_key!;
  const type = node.split(":")[0];
  const line = lineKeyOf(node);
  const bom = useRevision(p.id, p.current_revision_number ?? null);
  const arts = useArtifacts(p.id);
  const current = arts.data?.find((a) => a.node_key === node);
  const part = bom.data?.items.find((i) => i.line_key === line)?.part_number;
  const suppliers = useSuppliers(type === "SUPPLIER_SELECTION" ? part : undefined);
  const resolve = useResolve(p.id);
  const [note, setNote] = useState("");
  const [supplier, setSupplier] = useState("");
  const [json, setJson] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  if (bom.isLoading || arts.isLoading) return <Loading />;
  const draft = json ?? JSON.stringify(current?.content_json ?? {}, null, 2);

  async function submit() {
    setLocalError(null);
    let content: Record<string, unknown> | null = {};
    if (type === "SUPPLIER_SELECTION") {
      if (!supplier) return setLocalError("Choose a supplier option.");
      content = { line_key: line, supplier_option_code: supplier };
    } else if (type === "WORK_INSTRUCTION" || type === "QC_PLAN") {
      try {
        content = JSON.parse(draft);
      } catch {
        return setLocalError("Artifact content is not valid JSON.");
      }
    }
    if (note.trim().length < 5) return setLocalError("Add a resolution note (at least 5 characters).");
    await resolve.mutateAsync({ id: e.id, resolution_note: note.trim(), artifact_content: content });
    onDone();
  }

  return (
    <div className="mt-3 space-y-3 rounded-md bg-slate-50 p-3">
      {current && (
        <div className="text-xs">
          Current artifact rev {current.revision} <StatusBadge status={current.status} />
          <ValidationList v={current.validation_json} />
        </div>
      )}
      {type === "SUPPLIER_SELECTION" && (
        <Field label={`Supplier option for ${line} (${part ?? "?"})`} hint="Only synthetic catalog entries. Approval status and quantity are checked server-side.">
          <Select value={supplier} onChange={(ev) => setSupplier(ev.target.value)}>
            <option value="">Select…</option>
            {suppliers.data?.map((s) => (
              <option key={s.id} value={s.option_code}>
                {s.option_code} — {s.vendor_name} · {s.part_number} rev {s.part_revision} · {s.approved_for_part ? "approved" : "NOT approved"} · qty{" "}
                {s.available_qty}
              </option>
            ))}
          </Select>
        </Field>
      )}
      {type === "COMPATIBILITY_CHECK" && (
        <Notice>
          The server re-evaluates this rule against the current baseline. It resolves only if the rule now passes — a failing rule
          requires a BOM change (for example, a matching controller or chassis).
        </Notice>
      )}
      {(type === "WORK_INSTRUCTION" || type === "QC_PLAN") && (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-slate-700">New artifact revision (JSON)</span>
            <Button
              variant="secondary"
              type="button"
              onClick={() => {
                if (!bom.data) return;
                try {
                  setJson(JSON.stringify(refreshReferences(JSON.parse(draft), bom.data.items, arts.data ?? []), null, 2));
                } catch {
                  setLocalError("Artifact content is not valid JSON.");
                }
              }}
            >
              Refresh pinned references from current baseline
            </Button>
          </div>
          <Textarea rows={12} aria-label="Artifact content" value={draft} onChange={(ev) => setJson(ev.target.value)} />
          <p className="text-xs text-slate-500">
            References must pin the current part numbers, revisions and attributes; checks on a pinned attribute must expect its current value.
          </p>
        </div>
      )}
      <Field label="Resolution note">
        <Input value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="What was revalidated and how" />
      </Field>
      {localError && <ErrorBox error={localError} title="Check the form" />}
      <ErrorBox error={resolve.error} title="Resolution rejected" />
      <Button onClick={() => submit().catch(() => undefined)} busy={resolve.isPending}>
        Submit revalidation
      </Button>
    </div>
  );
}

function WaiveForm({ e, onDone }: { e: ExceptionItem; onDone: () => void }) {
  const p = useProjectOutlet();
  const waive = useWaive(p.id);
  const [rationale, setRationale] = useState("");
  return (
    <div className="mt-3 space-y-2 rounded-md bg-amber-50 p-3">
      <p className="text-xs text-amber-900">Waivers are recorded with your identity and rationale. Critical and hard-compatibility issues cannot be waived.</p>
      <Textarea rows={3} className="font-sans text-sm" aria-label="Waiver rationale" value={rationale} onChange={(ev) => setRationale(ev.target.value)} />
      <ErrorBox error={waive.error} title="Waiver rejected" />
      <Button
        variant="danger"
        busy={waive.isPending}
        disabled={rationale.trim().length < 10}
        onClick={() =>
          waive
            .mutateAsync({ id: e.id, rationale: rationale.trim() })
            .then(onDone)
            .catch(() => undefined)
        }
      >
        Waive exception
      </Button>
    </div>
  );
}
