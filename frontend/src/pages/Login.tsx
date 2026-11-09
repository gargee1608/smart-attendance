import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, ErrorNote, Field, Input } from "../components/ui";
import { api, setSession } from "../lib/api";

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setSession(result.access_token, result.user);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <section className="hidden bg-pine px-12 py-16 text-[#f6f1e6] lg:block">
        <p className="text-xs uppercase tracking-[0.2em] text-[#f6f1e6]/60">Self-hosted · one server</p>
        <h1 className="mt-6 max-w-md font-serif text-5xl leading-tight">The attendance book for an academy that keeps its own data.</h1>
        <p className="mt-6 max-w-md text-[#f6f1e6]/75">
          Faces are matched only inside the organization that enrolled them. A low score stays unnamed. Photos and embeddings live on this machine.
        </p>
      </section>
      <section className="flex items-center justify-center px-4 py-12">
        <Card className="w-full max-w-md p-6">
          <h2 className="font-serif text-3xl">Sign in</h2>
          <p className="mt-1 text-sm text-ink/70">Use the super admin created by install.sh, or an account your organization issued.</p>
          <form className="mt-6 space-y-4" onSubmit={submit}>
            <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></Field>
            <Field label="Password"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" /></Field>
            <ErrorNote>{error}</ErrorNote>
            <Button type="submit" disabled={busy} className="w-full">{busy ? "Checking…" : "Enter the register"}</Button>
          </form>
        </Card>
      </section>
    </div>
  );
}
