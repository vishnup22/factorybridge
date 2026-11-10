import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useCreateProject, useProjects } from "../api/hooks";
import { can, useAuth } from "../auth/AuthContext";
import { Button, Card, Empty, ErrorBox, Field, fmtTime, Input, Loading, StatusBadge, Table, Textarea } from "../components/ui";

export function ProjectsPage() {
  const { role } = useAuth();
  const projects = useProjects();
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Projects</h1>
          <p className="text-sm text-slate-600">Each project tracks one product build and its manufacturing readiness.</p>
        </div>
        {can(role, "customer", "engineer", "admin") && <Button onClick={() => setOpen((v) => !v)}>{open ? "Cancel" : "New project"}</Button>}
      </div>
      {open && <CreateProject onDone={() => setOpen(false)} />}
      <Card>
        {projects.isLoading ? (
          <Loading />
        ) : projects.error ? (
          <ErrorBox error={projects.error} />
        ) : !projects.data?.length ? (
          <Empty>No projects yet. Create one to upload a requirements PDF or BOM CSV.</Empty>
        ) : (
          <Table head={["Project", "SKU", "Qty", "Baseline", "Readiness", "Open blockers", "Updated"]}>
            {projects.data.map((p) => (
              <tr key={p.id} className="hover:bg-slate-50">
                <td className="px-2 py-2">
                  <Link className="font-medium text-sky-700 hover:underline" to={`/projects/${p.id}`}>
                    {p.name}
                  </Link>
                </td>
                <td className="px-2 py-2">{p.sku}</td>
                <td className="px-2 py-2">{p.target_quantity}</td>
                <td className="px-2 py-2">{p.current_revision_number ? `R${p.current_revision_number}` : "—"}</td>
                <td className="px-2 py-2">
                  <StatusBadge status={p.status} />
                </td>
                <td className="px-2 py-2">{p.open_blocker_count}</td>
                <td className="px-2 py-2 text-slate-500">{fmtTime(p.updated_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function CreateProject({ onDone }: { onDone: () => void }) {
  const create = useCreateProject();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "Warehouse Scout V1", sku: "WS-100", target_quantity: 100, description: "Synthetic 100-unit pilot batch" });

  async function submit(e: FormEvent) {
    e.preventDefault();
    const p = await create.mutateAsync(form);
    onDone();
    navigate(`/projects/${p.id}`);
  }

  return (
    <Card title="New project">
      <form onSubmit={submit} className="grid gap-4 md:grid-cols-3">
        <Field label="Name">
          <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} />
        </Field>
        <Field label="Product SKU">
          <Input value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} required pattern="[A-Za-z0-9\-_.]+" />
        </Field>
        <Field label="Target quantity">
          <Input type="number" min={1} value={form.target_quantity} onChange={(e) => setForm({ ...form, target_quantity: Number(e.target.value) })} required />
        </Field>
        <div className="md:col-span-3">
          <Field label="Description">
            <Textarea rows={2} className="font-sans text-sm" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
        </div>
        <div className="md:col-span-3 flex items-center gap-3">
          <Button type="submit" busy={create.isPending}>
            Create project
          </Button>
          <ErrorBox error={create.error} />
        </div>
      </form>
    </Card>
  );
}
