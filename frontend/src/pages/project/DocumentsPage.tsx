import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { isInFlight, useDocuments, useInvalidateProject, useUpload } from "../../api/hooks";
import { can, useAuth } from "../../auth/AuthContext";
import { Button, Card, Empty, ErrorBox, fmtTime, Loading, Notice, StatusBadge, Table } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

export function DocumentsPage() {
  const p = useProjectOutlet();
  const { role } = useAuth();
  const docs = useDocuments(p.id);
  const upload = useUpload(p.id);
  const invalidate = useInvalidateProject(p.id);
  const input = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const active = isInFlight(docs.data);
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) void invalidate();
    wasActive.current = active;
  }, [active, invalidate]);

  async function onFile(file: File | undefined) {
    if (!file) return;
    setNotice(null);
    const d = await upload.mutateAsync(file);
    setNotice(d.duplicate ? `${d.filename} was already uploaded — showing the existing document.` : `${d.filename} queued for processing.`);
    if (input.current) input.current.value = "";
  }

  return (
    <div className="space-y-4">
      {can(role, "customer", "engineer") && (
        <Card title="Upload requirements">
          <p className="mb-3 text-sm text-slate-600">
            Text-based PDF (max 10 MB) or a BOM CSV. Files are stored privately; extraction runs in the background and every
            value must be reviewed by an engineer before it becomes part of a baseline.
          </p>
          <div className="flex items-center gap-3">
            <input
              ref={input}
              type="file"
              accept=".pdf,.csv,application/pdf,text/csv"
              aria-label="Requirements file"
              onChange={(e) => onFile(e.target.files?.[0]).catch(() => undefined)}
              className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-sm file:text-white"
            />
            {upload.isPending && <span className="text-sm text-slate-500">Uploading…</span>}
          </div>
          <div className="mt-3 space-y-2">
            <ErrorBox error={upload.error} title="Upload rejected" />
            {notice && <Notice>{notice}</Notice>}
          </div>
        </Card>
      )}
      <Card title="Documents">
        {docs.isLoading ? (
          <Loading />
        ) : docs.error ? (
          <ErrorBox error={docs.error} />
        ) : !docs.data?.length ? (
          <Empty>No documents yet.</Empty>
        ) : (
          <Table head={["File", "Type", "Status", "Pages", "Uploaded", ""]}>
            {docs.data.map((d) => (
              <tr key={d.id}>
                <td className="px-2 py-2 font-medium">{d.filename}</td>
                <td className="px-2 py-2 text-slate-600">{d.media_type === "text/csv" ? "CSV" : "PDF"}</td>
                <td className="px-2 py-2">
                  <StatusBadge status={d.processing_status} />
                  {d.error_code && (
                    <div className="mt-1 text-xs text-red-700">
                      {d.error_code}
                      {d.error_detail ? ` — ${d.error_detail}` : ""}
                    </div>
                  )}
                </td>
                <td className="px-2 py-2">{d.page_count ?? "—"}</td>
                <td className="px-2 py-2 text-slate-500">{fmtTime(d.created_at)}</td>
                <td className="px-2 py-2 text-right">
                  {can(role, "engineer", "admin") && ["REVIEW_REQUIRED", "ACCEPTED", "FAILED"].includes(d.processing_status) && (
                    <Link to={`${d.id}/review`}>
                      <Button variant={d.processing_status === "REVIEW_REQUIRED" ? "primary" : "secondary"}>
                        {d.processing_status === "REVIEW_REQUIRED" ? "Review extraction" : "View extraction"}
                      </Button>
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
