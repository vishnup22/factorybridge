import { useState, type FormEvent } from "react";
import { Navigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { SyntheticBanner } from "../components/Layout";
import { Button, ErrorBox, Field, Input } from "../components/ui";

const DEMO_ACCOUNTS = [
  ["customer@demo.example", "Customer — submits requirements, sees status"],
  ["engineer@demo.example", "Manufacturing engineer — reviews, changes, resolves"],
  ["approver@demo.example", "Approver — releases or rejects a baseline"],
  ["approver2@demo.example", "Second approver — separation of duties"],
  ["admin@demo.example", "Admin — users and audit"],
];

export function LoginPage() {
  const { me, login } = useAuth();
  const [email, setEmail] = useState("engineer@demo.example");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen">
      <SyntheticBanner />
      <div className="mx-auto grid max-w-4xl gap-8 px-4 py-16 md:grid-cols-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">FactoryBridge</h1>
          <p className="mt-2 text-sm text-slate-600">
            Change intelligence for small-batch hardware: turn requirements into a versioned baseline, see exactly which
            manufacturing artifacts a change invalidates, and keep readiness blocked until the right person re-approves.
          </p>
          <ul className="mt-6 space-y-2 text-sm">
            {DEMO_ACCOUNTS.map(([e, label]) => (
              <li key={e}>
                <button type="button" className="text-left text-sky-700 hover:underline" onClick={() => setEmail(e)}>
                  {e}
                </button>
                <span className="block text-xs text-slate-500">{label}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-slate-500">
            Demo passwords are generated at seed time and shared out-of-band; they are never shown in the UI.
          </p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="font-semibold">Sign in</h2>
          <Field label="Email">
            <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="Password">
            <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <ErrorBox error={error} title="Sign-in failed" />
          <Button type="submit" busy={busy} className="w-full justify-center">
            Sign in
          </Button>
        </form>
      </div>
    </div>
  );
}
