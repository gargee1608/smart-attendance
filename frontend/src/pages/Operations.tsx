import { FormEvent, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Textarea } from "../components/ui";
import { api, downloadCsv, getOrg, getUser, type Row } from "../lib/api";

function needsOrg() {
  const user = getUser();
  return user?.role === "super_admin" && !getOrg();
}

export function CamerasPage() {
  const [items, setItems] = useState<Row[]>([]);
  const [form, setForm] = useState({ name: "", rtsp_url: "", location: "" });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  function load() {
    api<{ items: Row[] }>("/api/cameras").then((data) => setItems(data.items)).catch((err) => setError(err.message));
  }
  useEffect(load, []);
  if (needsOrg()) return <Empty title="Choose an organization" body="Cameras belong to one organization." />;
  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/cameras", { method: "POST", body: JSON.stringify(form) });
      setForm({ name: "", rtsp_url: "", location: "" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the camera");
    }
  }
  async function capture(id: string) {
    setMessage("");
    try {
      const result = await api(`/api/cameras/${id}/capture`, { method: "POST" });
      const known = result.faces.filter((face: Row) => face.result === "known").length;
      const unknown = result.faces.filter((face: Row) => face.result === "unknown").length;
      setMessage(`Read one frame. ${known} known, ${unknown} unnamed.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Capture failed");
    }
  }
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">Camera</p>
        <h1 className="font-serif text-4xl">RTSP cameras</h1>
        <p className="max-w-2xl text-sm text-ink/70">Add the camera URL, then capture one frame. The server must be able to reach the camera. Use rtsp://user:password@192.168.1.20:554/stream.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      {message && <p className="text-sm">{message}</p>}
      <Card className="p-4">
        <form className="grid gap-3 md:grid-cols-3" onSubmit={create}>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
          <Field label="RTSP URL"><Input value={form.rtsp_url} onChange={(e) => setForm({ ...form, rtsp_url: e.target.value })} placeholder="rtsp://" required /></Field>
          <Field label="Location"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
          <Button type="submit">Add camera</Button>
        </form>
      </Card>
      {items.length === 0 ? <Empty title="No cameras" body="A phone photo check-in still works without an IP camera." /> : items.map((camera) => (
        <Card key={camera.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <p className="font-serif text-2xl">{camera.name}</p>
            <p className="text-sm text-ink/60">{camera.location || "No location"} · {camera.rtsp_url}</p>
          </div>
          <Button variant="quiet" onClick={() => capture(camera.id)}>Capture one frame</Button>
        </Card>
      ))}
    </div>
  );
}

export function ReportsPage() {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [csvText, setCsvText] = useState("code,full_name,person_type,email,phone,dob,batch\n,Asha Rao,student,asha@example.com,555,2012-03-01,");
  const [report, setReport] = useState<Row | null>(null);
  const [error, setError] = useState("");
  if (needsOrg()) return <Empty title="Choose an organization" body="Reports are per organization." />;
  async function upload(event: FormEvent) {
    event.preventDefault();
    const body = new FormData();
    body.append("file", new Blob([csvText], { type: "text/csv" }), "people.csv");
    try {
      setReport(await api("/api/imports/persons", { method: "POST", body }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    }
  }
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M10 · M11</p>
        <h1 className="font-serif text-4xl">Reports and import</h1>
        <p className="max-w-2xl text-sm text-ink/70">CSV export is ready. PDF and Excel are still to do. Imported people stay unenrolled until someone confirms photos.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      <Card className="flex flex-wrap items-end gap-3 p-4">
        <Field label="Month"><Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></Field>
        <Button onClick={() => downloadCsv(month).catch((err) => setError(err.message))}>Download attendance CSV</Button>
      </Card>
      <Card className="space-y-3 p-4">
        <h2 className="font-serif text-2xl">Import people</h2>
        <form className="space-y-3" onSubmit={upload}>
          <Textarea rows={6} value={csvText} onChange={(e) => setCsvText(e.target.value)} />
          <Button type="submit" variant="quiet">Import CSV</Button>
        </form>
        {report && <p className="text-sm">Created {report.created}. Errors {report.errors?.length || 0}. {report.errors?.[0]?.error || ""}</p>}
      </Card>
    </div>
  );
}

export function InsightsPage() {
  const [people, setPeople] = useState<Row[]>([]);
  const [personId, setPersonId] = useState("");
  const [metrics, setMetrics] = useState('{"batting_runs": 42}');
  const [comment, setComment] = useState("");
  const [suggestions, setSuggestions] = useState<Row[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (needsOrg()) return;
    api<{ items: Row[] }>("/api/persons").then((data) => setPeople(data.items)).catch((err) => setError(err.message));
    api<{ items: Row[] }>("/api/ai/comments").then((data) => setSuggestions(data.items)).catch(() => undefined);
  }, []);
  if (needsOrg()) return <Empty title="Choose an organization" body="Notes stay inside one organization." />;
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M08 · M09</p>
        <h1 className="font-serif text-4xl">Notes and suggestions</h1>
        <p className="max-w-2xl text-sm text-ink/70">Sport-specific score templates are still to do. You can store a metric JSON object and a coach comment now. AI text is always a suggestion and never a coach comment.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      <Card className="grid gap-3 p-4">
        <select className="rounded-md border border-line bg-white px-3 py-2 text-sm" value={personId} onChange={(e) => setPersonId(e.target.value)}>
          <option value="">Choose a person</option>
          {people.map((person) => <option key={person.id} value={person.id}>{person.full_name}</option>)}
        </select>
        <Textarea rows={3} value={metrics} onChange={(e) => setMetrics(e.target.value)} />
        <Button variant="quiet" onClick={async () => {
          try {
            await api("/api/performance/metrics", { method: "POST", body: JSON.stringify({ person_id: personId, metrics: JSON.parse(metrics) }) });
            setError("");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save metrics");
          }
        }}>Save metric stub</Button>
        <Textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Coach comment" />
        <Button variant="quiet" onClick={async () => {
          await api("/api/performance/comments", { method: "POST", body: JSON.stringify({ person_id: personId, comment }) });
          setComment("");
        }}>Save coach comment</Button>
        <Button onClick={async () => {
          try {
            const created = await api("/api/ai/comments", { method: "POST", body: JSON.stringify({ person_id: personId }) });
            setSuggestions((current) => [created, ...current]);
          } catch (err) {
            setError(err instanceof Error ? err.message : "The model is not ready");
          }
        }}>Draft an AI suggestion</Button>
      </Card>
      {suggestions.map((item) => (
        <Card key={item.id} className="space-y-2 p-4">
          <Badge tone="amber">Suggestion only</Badge>
          <p className="text-sm">{item.suggestion}</p>
          <p className="text-xs text-ink/50">{item.model} · {item.status}</p>
        </Card>
      ))}
    </div>
  );
}

export function AccessPage() {
  const [users, setUsers] = useState<Row[]>([]);
  const [keys, setKeys] = useState<Row[]>([]);
  const [audit, setAudit] = useState<Row[]>([]);
  const [freshKey, setFreshKey] = useState("");
  const [error, setError] = useState("");
  const [userForm, setUserForm] = useState({ full_name: "", email: "", password: "", role: "coach" });
  function load() {
    if (needsOrg()) return;
    api<{ items: Row[] }>("/api/users").then((data) => setUsers(data.items)).catch((err) => setError(err.message));
    api<{ items: Row[] }>("/api/api-keys").then((data) => setKeys(data.items)).catch(() => undefined);
    api<{ items: Row[] }>("/api/audit").then((data) => setAudit(data.items)).catch(() => undefined);
  }
  useEffect(load, []);
  if (needsOrg()) return <Empty title="Choose an organization" body="Users, keys, and the audit log are per organization. Platform sign-ins are visible to the super admin after an organization is selected." />;
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M02 · M13 · M14</p>
        <h1 className="font-serif text-4xl">Access and audit</h1>
      </header>
      <ErrorNote>{error}</ErrorNote>
      <Card className="space-y-3 p-4">
        <h2 className="font-serif text-2xl">Staff account</h2>
        <form className="grid gap-2 md:grid-cols-2" onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api("/api/users", { method: "POST", body: JSON.stringify(userForm) });
            setUserForm({ ...userForm, full_name: "", email: "", password: "" });
            load();
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not create the user");
          }
        }}>
          <Input placeholder="Name" value={userForm.full_name} onChange={(e) => setUserForm({ ...userForm, full_name: e.target.value })} required />
          <Input placeholder="Email" value={userForm.email} onChange={(e) => setUserForm({ ...userForm, email: e.target.value })} required />
          <Input placeholder="Password (10+)" type="password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} required />
          <select className="rounded-md border border-line bg-white px-3 py-2 text-sm" value={userForm.role} onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}>
            {["org_admin", "coach", "teacher", "student"].map((role) => <option key={role}>{role}</option>)}
          </select>
          <Button type="submit">Create account</Button>
        </form>
        <ul className="text-sm">{users.map((item) => <li key={item.id}>{item.full_name} · {item.email} · {item.role}</li>)}</ul>
      </Card>
      <Card className="space-y-3 p-4">
        <h2 className="font-serif text-2xl">External API key</h2>
        <Button variant="quiet" onClick={async () => {
          const created = await api("/api/api-keys", { method: "POST", body: JSON.stringify({ name: "Integration", rate_limit_per_minute: 60 }) });
          setFreshKey(created.api_key);
          load();
        }}>Issue a key</Button>
        {freshKey && <p className="break-all rounded-md bg-amber/10 p-2 text-sm">Copy this key now. It will not be shown again. {freshKey}</p>}
        <ul className="text-sm">{keys.map((item) => <li key={item.id}>{item.name} · {item.key_prefix}… · {item.rate_limit_per_minute}/min {item.revoked_at ? "· revoked" : ""}</li>)}</ul>
      </Card>
      <Card className="p-4">
        <h2 className="font-serif text-2xl">Audit</h2>
        <ul className="mt-2 max-h-96 divide-y divide-line overflow-auto text-sm">
          {audit.map((item) => (
            <li key={item.id} className="py-2">
              <span className="text-ink/50">{item.created_at?.replace("T", " ").slice(0, 16)}</span> · {item.actor_role} · {item.action}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
