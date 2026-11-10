import { useState } from "react";
import { useDiff, useRevision, useRevisions } from "../../api/hooks";
import type { BomItem, LineDiff } from "../../api/types";
import { Badge, Card, cx, Empty, ErrorBox, fmtTime, Loading, Mono, Select, shortHash, StatusBadge, Table } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

export function formatAttrs(attrs: Record<string, unknown>): string {
  return Object.entries(attrs)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : String(v)}`)
    .join(", ");
}

export function BomPage() {
  const p = useProjectOutlet();
  const revs = useRevisions(p.id);
  const latest = revs.data?.length ? revs.data[revs.data.length - 1].revision_number : null;
  const [selected, setSelected] = useState<number | null>(null);
  const shown = selected ?? latest;
  const rev = useRevision(p.id, shown);
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const diffFrom = from ?? (latest && latest > 1 ? latest - 1 : null);
  const diffTo = to ?? latest;
  const diff = useDiff(p.id, diffFrom, diffTo);

  if (revs.isLoading) return <Loading />;
  if (revs.error) return <ErrorBox error={revs.error} />;
  if (!revs.data?.length) return <Empty>No BOM revision yet. R1 is created only from a reviewed document.</Empty>;

  return (
    <div className="grid gap-4 lg:grid-cols-4">
      <Card title="Revisions" className="lg:col-span-1">
        <ol className="space-y-1">
          {[...revs.data].reverse().map((r) => (
            <li key={r.id}>
              <button
                onClick={() => setSelected(r.revision_number)}
                className={cx(
                  "w-full rounded-md px-2 py-1.5 text-left text-sm",
                  r.revision_number === shown ? "bg-slate-900 text-white" : "hover:bg-slate-100",
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">R{r.revision_number}</span>
                  {r.is_current && <Badge tone="green">current</Badge>}
                </div>
                <div className={cx("text-xs", r.revision_number === shown ? "text-slate-300" : "text-slate-500")}>
                  {r.source_kind.replaceAll("_", " ").toLowerCase()} · {fmtTime(r.created_at)}
                </div>
              </button>
            </li>
          ))}
        </ol>
      </Card>
      <div className="space-y-4 lg:col-span-3">
        <Card
          title={shown ? `R${shown} line items` : "Line items"}
          actions={rev.data && <Mono title={rev.data.content_hash}>hash {shortHash(rev.data.content_hash)}</Mono>}
        >
          {rev.isLoading ? <Loading /> : rev.error ? <ErrorBox error={rev.error} /> : rev.data && <Items items={rev.data.items} />}
          <p className="mt-3 text-xs text-slate-500">Revisions are immutable. Changes create a new revision; prior revisions are never edited.</p>
        </Card>
        {revs.data.length > 1 && (
          <Card
            title="Compare revisions"
            actions={
              <div className="flex items-center gap-2 text-sm">
                <Select aria-label="From revision" value={diffFrom ?? ""} onChange={(e) => setFrom(Number(e.target.value))} className="w-20">
                  {revs.data.map((r) => (
                    <option key={r.id} value={r.revision_number}>
                      R{r.revision_number}
                    </option>
                  ))}
                </Select>
                →
                <Select aria-label="To revision" value={diffTo ?? ""} onChange={(e) => setTo(Number(e.target.value))} className="w-20">
                  {revs.data.map((r) => (
                    <option key={r.id} value={r.revision_number}>
                      R{r.revision_number}
                    </option>
                  ))}
                </Select>
              </div>
            }
          >
            {diff.isLoading ? <Loading /> : diff.error ? <ErrorBox error={diff.error} /> : diff.data && <DiffTable lines={diff.data.lines} />}
          </Card>
        )}
      </div>
    </div>
  );
}

function Items({ items }: { items: BomItem[] }) {
  return (
    <Table head={["Line", "Part", "Rev", "Qty", "Attributes", "Source"]}>
      {items.map((i) => {
        const prov = i.provenance_json as { source?: string; page?: number; document?: string; review_action?: string; change_code?: string };
        return (
          <tr key={i.line_key}>
            <td className="px-2 py-1.5 font-mono text-xs">{i.line_key}</td>
            <td className="px-2 py-1.5">
              <div className="font-medium">{i.part_number}</div>
              <div className="text-xs text-slate-500">{i.description}</div>
            </td>
            <td className="px-2 py-1.5">{i.part_revision}</td>
            <td className="px-2 py-1.5">
              {i.quantity} {i.unit}
            </td>
            <td className="px-2 py-1.5 font-mono text-xs">{formatAttrs(i.attributes_json)}</td>
            <td className="px-2 py-1.5 text-xs text-slate-500">
              {prov.document ? `${prov.document}${prov.page ? ` p.${prov.page}` : ""}` : prov.change_code ?? prov.source ?? "—"}
              {prov.review_action === "EDIT" && <Badge tone="violet">edited</Badge>}
            </td>
          </tr>
        );
      })}
    </Table>
  );
}

const TYPE_TONE = { ADDED: "green", REMOVED: "red", UPDATED: "amber", UNCHANGED: "slate" } as const;

export function DiffTable({ lines }: { lines: LineDiff[] }) {
  const changed = lines.filter((l) => l.change_type !== "UNCHANGED");
  if (!changed.length) return <Empty>No differences.</Empty>;
  return (
    <Table head={["Line", "Change", "Fields", "Before", "After"]}>
      {changed.map((l) => (
        <tr key={l.line_key} data-testid="diff-row">
          <td className="px-2 py-1.5 font-mono text-xs">{l.line_key}</td>
          <td className="px-2 py-1.5">
            <Badge tone={TYPE_TONE[l.change_type as keyof typeof TYPE_TONE] ?? "slate"}>{l.change_type}</Badge>
          </td>
          <td className="px-2 py-1.5 text-xs">
            {l.changed_fields.map((f) => (
              <div key={f} className="font-mono">
                {f}
              </div>
            ))}
          </td>
          <td className="px-2 py-1.5 text-xs">{l.old ? <LineSummary line={l.old} fields={l.changed_fields} /> : "—"}</td>
          <td className="px-2 py-1.5 text-xs">{l.new ? <LineSummary line={l.new} fields={l.changed_fields} highlight /> : "—"}</td>
        </tr>
      ))}
    </Table>
  );
}

function LineSummary({ line, fields, highlight }: { line: Record<string, unknown>; fields: string[]; highlight?: boolean }) {
  const pick = (path: string): unknown =>
    path.split(".").reduce<unknown>((o, k) => (o && typeof o === "object" ? (o as Record<string, unknown>)[k] : undefined), line);
  return (
    <div className="space-y-0.5 font-mono">
      {fields.map((f) => {
        const v = pick(f);
        return (
          <div key={f} className={highlight ? "text-amber-900" : "text-slate-600"}>
            {Array.isArray(v) ? v.join("|") : v === undefined ? "∅" : String(v)}
          </div>
        );
      })}
    </div>
  );
}

export { StatusBadge };
