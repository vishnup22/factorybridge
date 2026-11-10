import { Fragment, useState } from "react";
import { useAudit } from "../../api/hooks";
import { Button, Card, Empty, ErrorBox, fmtTime, Json, Loading, Mono, Select, Table } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

const ACTIONS = [
  "",
  "BOM_REVISION_PUBLISHED",
  "CHANGE_PUBLISHED",
  "EXCEPTION_OPENED,EXCEPTION_UPDATED,EXCEPTION_REOPENED",
  "EXCEPTION_RESOLVED,EXCEPTION_WAIVED",
  "APPROVAL_REQUESTED,APPROVAL_DECIDED,APPROVAL_STALED",
  "PROJECT_READINESS_CHANGED",
  "EXTRACTION_COMPLETED,EXTRACTION_FAILED,EXTRACTION_REVIEWED",
];

export function AuditPage() {
  const p = useProjectOutlet();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const audit = useAudit(p.id, page, action || undefined);
  const total = Number(audit.data?.meta?.total ?? 0);
  const pages = Math.max(1, Math.ceil(total / 50));

  return (
    <Card
      title={`Audit log (${total} events, append-only)`}
      actions={
        <Select
          aria-label="Filter by action"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
          className="w-72"
        >
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {a ? a.split(",").join(" / ") : "All actions"}
            </option>
          ))}
        </Select>
      }
    >
      {audit.isLoading ? (
        <Loading />
      ) : audit.error ? (
        <ErrorBox error={audit.error} />
      ) : !audit.data?.data.length ? (
        <Empty>No events.</Empty>
      ) : (
        <>
          <Table head={["#", "Time", "Actor", "Action", "Resource", ""]}>
            {audit.data.data.map((e) => (
              <Fragment key={e.id}>
                <tr>
                  <td className="px-2 py-1.5 text-xs text-slate-500">{e.seq}</td>
                  <td className="px-2 py-1.5 text-xs">{fmtTime(e.created_at)}</td>
                  <td className="px-2 py-1.5 text-xs">{e.actor_label}</td>
                  <td className="px-2 py-1.5 text-xs font-medium">{e.action}</td>
                  <td className="px-2 py-1.5 text-xs">
                    {e.resource_type} <Mono>{(e.resource_id ?? "").slice(0, 8)}</Mono>
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <Button variant="ghost" onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
                      {expanded === e.id ? "Hide" : "Details"}
                    </Button>
                  </td>
                </tr>
                {expanded === e.id && (
                  <tr>
                    <td colSpan={6} className="bg-slate-50 px-2 py-2">
                      <div className="grid gap-2 md:grid-cols-2">
                        <div>
                          <div className="text-xs text-slate-500">before</div>
                          <Json value={e.before_json} />
                        </div>
                        <div>
                          <div className="text-xs text-slate-500">after</div>
                          <Json value={e.after_json} />
                        </div>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">correlation {e.correlation_id ?? "—"}</div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </Table>
          <div className="mt-3 flex items-center justify-end gap-2 text-sm">
            <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Newer
            </Button>
            <span>
              page {page} / {pages}
            </span>
            <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Older
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}
