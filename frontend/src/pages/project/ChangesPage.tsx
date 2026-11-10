import { Link } from "react-router";
import { useChanges } from "../../api/hooks";
import { can, useAuth } from "../../auth/AuthContext";
import { Button, Card, Empty, ErrorBox, fmtTime, Loading, StatusBadge, Table } from "../../components/ui";
import { useProjectOutlet } from "./useProjectOutlet";

export function ChangesPage() {
  const p = useProjectOutlet();
  const { role } = useAuth();
  const changes = useChanges(p.id);
  return (
    <Card
      title="Engineering changes"
      actions={
        can(role, "engineer", "approver", "customer") &&
        p.current_revision_number && (
          <Link to="new">
            <Button>Propose change</Button>
          </Link>
        )
      }
    >
      {changes.isLoading ? (
        <Loading />
      ) : changes.error ? (
        <ErrorBox error={changes.error} />
      ) : !changes.data?.length ? (
        <Empty>No engineering changes yet.</Empty>
      ) : (
        <Table head={["Change", "Status", "Revisions", "Reason", "Submitted by", "Created"]}>
          {[...changes.data].reverse().map((c) => (
            <tr key={c.id}>
              <td className="px-2 py-2">
                <Link to={c.id} className="font-medium text-sky-700 hover:underline">
                  {c.code}
                </Link>
              </td>
              <td className="px-2 py-2">
                <StatusBadge status={c.status} />
              </td>
              <td className="px-2 py-2 text-sm">
                R{c.from_revision_number} → {c.to_revision_number ? `R${c.to_revision_number}` : "(unpublished)"}
              </td>
              <td className="max-w-md truncate px-2 py-2 text-sm text-slate-600" title={c.reason}>
                {c.reason}
              </td>
              <td className="px-2 py-2 text-sm">{c.submitted_by_email}</td>
              <td className="px-2 py-2 text-sm text-slate-500">{fmtTime(c.created_at)}</td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
