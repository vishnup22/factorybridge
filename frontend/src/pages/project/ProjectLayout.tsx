import { NavLink, Outlet, useParams } from "react-router";
import type { Project, Role } from "../../api/types";
import { useProject } from "../../api/hooks";
import { can, useAuth } from "../../auth/AuthContext";
import { cx, ErrorBox, Loading, Mono, shortHash, StatusBadge } from "../../components/ui";

const TABS: { to: string; label: string; roles?: Role[] }[] = [
  { to: "", label: "Overview" },
  { to: "documents", label: "Documents" },
  { to: "bom", label: "BOM history" },
  { to: "changes", label: "Changes" },
  { to: "graph", label: "Impact graph", roles: ["engineer", "approver", "admin"] },
  { to: "exceptions", label: "Exceptions" },
  { to: "approvals", label: "Approvals" },
  { to: "audit", label: "Audit", roles: ["engineer", "approver", "admin"] },
];

const BANNER: Record<string, { cls: string; text: string }> = {
  READY: { cls: "border-emerald-300 bg-emerald-50 text-emerald-900", text: "Released for this exact baseline by an authorized approver." },
  BLOCKED: { cls: "border-red-300 bg-red-50 text-red-900", text: "Not releasable — resolve the blockers below." },
  REVIEW_REQUIRED: { cls: "border-amber-300 bg-amber-50 text-amber-900", text: "No blockers remain — awaiting approval of the current baseline." },
  EXTRACTING: { cls: "border-sky-300 bg-sky-50 text-sky-900", text: "Documents are being processed or awaiting review." },
  DRAFT: { cls: "border-slate-300 bg-slate-50 text-slate-800", text: "No baseline yet — upload requirements to begin." },
};

export function ReadinessBanner({ project }: { project: Project }) {
  const b = BANNER[project.status] ?? BANNER.DRAFT;
  return (
    <div data-testid="readiness-banner" className={cx("flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3", b.cls)}>
      <span className="text-xs font-semibold uppercase tracking-wide">Readiness</span>
      <StatusBadge status={project.status} />
      <span className="text-sm">{b.text}</span>
      <span className="ml-auto flex items-center gap-3 text-xs">
        {project.current_revision_number && <span>Baseline R{project.current_revision_number}</span>}
        <span>v{project.baseline_version}</span>
        {project.baseline_hash && <Mono title={project.baseline_hash}>{shortHash(project.baseline_hash)}</Mono>}
      </span>
    </div>
  );
}

export function ProjectLayout() {
  const { pid = "" } = useParams();
  const { role } = useAuth();
  const project = useProject(pid);

  if (project.isLoading) return <Loading />;
  if (project.error || !project.data) return <ErrorBox error={project.error ?? "Project not found"} />;
  const p = project.data;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">{p.name}</h1>
        <p className="text-sm text-slate-600">
          SKU {p.sku} · target {p.target_quantity} units{p.description ? ` · ${p.description}` : ""}
        </p>
      </div>
      <ReadinessBanner project={p} />
      <nav className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.filter((t) => !t.roles || can(role, ...t.roles)).map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.to === ""}
            className={({ isActive }) =>
              cx(
                "-mb-px border-b-2 px-3 py-2 text-sm",
                isActive ? "border-slate-900 font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900",
              )
            }
          >
            {t.label}
            {t.to === "exceptions" && p.open_blocker_count > 0 && (
              <span className="ml-1.5 rounded-full bg-red-600 px-1.5 text-xs text-white">{p.open_blocker_count}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <Outlet context={p} />
    </div>
  );
}
