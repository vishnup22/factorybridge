import { Link, Outlet } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button } from "./ui";

export const SYNTHETIC_NOTICE = "SYNTHETIC DATA / SIMULATION — NOT MANUFACTURING CERTIFICATION";

export function SyntheticBanner() {
  return (
    <div role="note" className="bg-amber-400 px-4 py-1 text-center text-xs font-bold tracking-wide text-amber-950">
      {SYNTHETIC_NOTICE}
    </div>
  );
}

export function Layout() {
  const { me, logout } = useAuth();
  return (
    <div className="min-h-screen">
      <SyntheticBanner />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid h-7 w-7 place-items-center rounded bg-slate-900 text-xs text-white">FB</span>
            FactoryBridge
          </Link>
          {me && (
            <div className="flex items-center gap-3 text-sm">
              <Badge tone={me.ai_mode === "LIVE" ? "violet" : "slate"} title="Document extraction provider">
                AI: {me.ai_mode === "LIVE" ? "live model" : "fixture (no model calls)"}
              </Badge>
              <span className="hidden text-slate-600 sm:inline">{me.organization.name as string}</span>
              <span className="text-slate-800">{me.user.email}</span>
              <Badge tone="blue">{me.user.role}</Badge>
              <Button variant="ghost" onClick={logout}>
                Sign out
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-slate-500">
        FactoryBridge is an independent demonstration with fictional data. It is not affiliated with any manufacturer, does not
        connect to industrial equipment, and does not certify manufacturing readiness.
      </footer>
    </div>
  );
}
