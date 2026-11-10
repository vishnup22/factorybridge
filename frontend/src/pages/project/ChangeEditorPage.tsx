import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useProposeChange, useRevision } from "../../api/hooks";
import { Button, Card, ErrorBox, Field, Input, Loading, Notice } from "../../components/ui";
import { buildChangeSet, emptyDraft, toDraft, type LineDraft } from "./changeSet";
import { useProjectOutlet } from "./useProjectOutlet";

export function ChangeEditorPage() {
  const p = useProjectOutlet();
  const base = useRevision(p.id, p.current_revision_number ?? null);
  const propose = useProposeChange(p.id);
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<LineDraft[] | null>(null);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<string[]>([]);

  if (base.isLoading) return <Loading />;
  if (base.error || !base.data) return <ErrorBox error={base.error ?? "No baseline"} />;
  const items = base.data.items;
  const rows = drafts ?? items.map(toDraft);
  const update = (i: number, patch: Partial<LineDraft>) => setDrafts(rows.map((r, n) => (n === i ? { ...r, ...patch } : r)));
  const { ops } = buildChangeSet(items, rows);

  async function submit() {
    const built = buildChangeSet(items, rows);
    const errs = [...built.errors];
    if (!built.ops.length) errs.push("No changes yet — edit at least one field.");
    if (reason.trim().length < 5) errs.push("Describe the reason for this change (at least 5 characters).");
    setErrors(errs);
    if (errs.length) return;
    const c = await propose.mutateAsync({ base_revision_number: base.data!.revision_number, reason: reason.trim(), changes: built.ops });
    navigate(`../${c.id}`, { relative: "path" });
  }

  return (
    <div className="space-y-4">
      <Link to=".." relative="path" className="text-sm text-sky-700 hover:underline">
        ← Changes
      </Link>
      <Card title={`Propose a change against R${base.data.revision_number}`}>
        <Notice>
          Proposing does not change the baseline. You will see the deterministic impact preview next; an engineer then publishes it as a
          new immutable revision.
        </Notice>
        <div className="mt-4 space-y-3">
          {rows.map((r, i) => (
            <div key={i} className={`grid gap-2 rounded-md border p-3 md:grid-cols-12 ${r.remove ? "border-red-200 bg-red-50/50 opacity-70" : "border-slate-200"}`}>
              <div className="md:col-span-2">
                <Field label="Line key">
                  <Input value={r.line_key} disabled={!r.isNew} onChange={(e) => update(i, { line_key: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-2">
                <Field label="Part number">
                  <Input aria-label={`${r.line_key} part number`} value={r.part_number} disabled={r.remove} onChange={(e) => update(i, { part_number: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-1">
                <Field label="Rev">
                  <Input aria-label={`${r.line_key} revision`} value={r.part_revision} disabled={r.remove} onChange={(e) => update(i, { part_revision: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-1">
                <Field label="Qty">
                  <Input aria-label={`${r.line_key} quantity`} value={r.quantity} disabled={r.remove} onChange={(e) => update(i, { quantity: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-2">
                <Field label="Description">
                  <Input value={r.description} disabled={r.remove} onChange={(e) => update(i, { description: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label="Attributes (JSON)">
                  <Input aria-label={`${r.line_key} attributes`} className="font-mono text-xs" value={r.attributes} disabled={r.remove} onChange={(e) => update(i, { attributes: e.target.value })} />
                </Field>
              </div>
              <div className="flex items-end md:col-span-1">
                <label className="flex items-center gap-1 text-xs">
                  <input type="checkbox" checked={r.remove} onChange={(e) => update(i, { remove: e.target.checked })} /> remove
                </label>
              </div>
            </div>
          ))}
          <Button variant="secondary" onClick={() => setDrafts([...rows, emptyDraft()])}>
            Add line
          </Button>
        </div>
      </Card>
      <Card title={`Change set (${ops.length} operation${ops.length === 1 ? "" : "s"})`}>
        <pre className="max-h-60 overflow-auto rounded bg-slate-50 p-2 text-xs" data-testid="change-set">
          {JSON.stringify(ops, null, 1)}
        </pre>
        <div className="mt-3 space-y-3">
          <Field label="Reason (recorded in the audit log)">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Customer revised actuator to 48V" />
          </Field>
          {errors.length > 0 && (
            <ul role="alert" className="list-disc rounded-md border border-red-200 bg-red-50 py-2 pl-8 pr-3 text-sm text-red-800">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <ErrorBox error={propose.error} title="Change rejected" />
          <Button onClick={() => submit().catch(() => undefined)} busy={propose.isPending}>
            Propose and preview impact
          </Button>
        </div>
      </Card>
    </div>
  );
}
