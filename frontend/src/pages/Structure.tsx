import { FormEvent, useEffect, useState } from "react";
import { Button, Card, Empty, ErrorNote, Field, Input, Select } from "../components/ui";
import { api, getOrg, getUser, type Row } from "../lib/api";

export function StructurePage() {
  const user = getUser();
  const [sports, setSports] = useState<Row[]>([]);
  const [batches, setBatches] = useState<Row[]>([]);
  const [classes, setClasses] = useState<Row[]>([]);
  const [sessions, setSessions] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [sport, setSport] = useState("");
  const [batch, setBatch] = useState({ name: "", sport_id: "", start_time: "07:00", weekdays: "0,1,2,3,4" });
  const [klass, setKlass] = useState({ name: "", section: "A", session_id: "" });
  const [session, setSession] = useState({ name: "", start_date: "", end_date: "" });

  function load() {
    if (user?.role === "super_admin" && !getOrg()) return;
    Promise.all([
      api<{ items: Row[] }>("/api/sports"),
      api<{ items: Row[] }>("/api/batches"),
      api<{ items: Row[] }>("/api/classes"),
      api<{ items: Row[] }>("/api/sessions"),
    ]).then(([s, b, c, a]) => {
      setSports(s.items); setBatches(b.items); setClasses(c.items); setSessions(a.items);
    }).catch((err) => setError(err.message));
  }
  useEffect(load, [user?.role]);

  async function onSport(event: FormEvent) {
    event.preventDefault();
    await api("/api/sports", { method: "POST", body: JSON.stringify({ name: sport }) });
    setSport("");
    load();
  }
  async function onBatch(event: FormEvent) {
    event.preventDefault();
    await api("/api/batches", {
      method: "POST",
      body: JSON.stringify({
        name: batch.name,
        sport_id: batch.sport_id || null,
        start_time: batch.start_time,
        weekdays: batch.weekdays.split(",").map((day) => Number(day.trim())).filter((day) => !Number.isNaN(day)),
      }),
    });
    setBatch({ ...batch, name: "" });
    load();
  }
  async function onSession(event: FormEvent) {
    event.preventDefault();
    await api("/api/sessions", { method: "POST", body: JSON.stringify({ ...session, is_current: true }) });
    setSession({ name: "", start_date: "", end_date: "" });
    load();
  }
  async function onClass(event: FormEvent) {
    event.preventDefault();
    await api("/api/classes", { method: "POST", body: JSON.stringify({ ...klass, session_id: klass.session_id || null, create_batch: true }) });
    setKlass({ ...klass, name: "" });
    load();
  }

  if (user?.role === "super_admin" && !getOrg()) return <Empty title="Choose an organization" body="Batches belong to one academy." />;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M03</p>
        <h1 className="font-serif text-4xl">Sports, batches, and classes</h1>
        <p className="max-w-2xl text-sm text-ink/70">Academies use sports and batches. Schools use sessions, classes, and an attendance batch created with the class. Weekdays are Monday 0 through Sunday 6.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-3 p-4">
          <h2 className="font-serif text-2xl">Sport</h2>
          <form className="flex gap-2" onSubmit={onSport}>
            <Input value={sport} onChange={(e) => setSport(e.target.value)} placeholder="Cricket" required />
            <Button type="submit">Add</Button>
          </form>
          <ul className="text-sm">{sports.map((item) => <li key={item.id}>{item.name}</li>)}</ul>
        </Card>
        <Card className="space-y-3 p-4">
          <h2 className="font-serif text-2xl">Batch</h2>
          <form className="grid gap-2" onSubmit={onBatch}>
            <Field label="Name"><Input value={batch.name} onChange={(e) => setBatch({ ...batch, name: e.target.value })} required /></Field>
            <Field label="Sport">
              <Select value={batch.sport_id} onChange={(e) => setBatch({ ...batch, sport_id: e.target.value })}>
                <option value="">None</option>
                {sports.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </Select>
            </Field>
            <Field label="Start time"><Input value={batch.start_time} onChange={(e) => setBatch({ ...batch, start_time: e.target.value })} /></Field>
            <Field label="Weekdays"><Input value={batch.weekdays} onChange={(e) => setBatch({ ...batch, weekdays: e.target.value })} /></Field>
            <Button type="submit">Add batch</Button>
          </form>
        </Card>
        <Card className="space-y-3 p-4">
          <h2 className="font-serif text-2xl">Academic session</h2>
          <form className="grid gap-2" onSubmit={onSession}>
            <Input value={session.name} onChange={(e) => setSession({ ...session, name: e.target.value })} placeholder="2026–27" required />
            <div className="grid grid-cols-2 gap-2">
              <Input type="date" value={session.start_date} onChange={(e) => setSession({ ...session, start_date: e.target.value })} />
              <Input type="date" value={session.end_date} onChange={(e) => setSession({ ...session, end_date: e.target.value })} />
            </div>
            <Button type="submit">Add session</Button>
          </form>
          <ul className="text-sm">{sessions.map((item) => <li key={item.id}>{item.name}{item.is_current ? " · current" : ""}</li>)}</ul>
        </Card>
        <Card className="space-y-3 p-4">
          <h2 className="font-serif text-2xl">Class</h2>
          <form className="grid gap-2" onSubmit={onClass}>
            <Input value={klass.name} onChange={(e) => setKlass({ ...klass, name: e.target.value })} placeholder="Class 6" required />
            <Input value={klass.section} onChange={(e) => setKlass({ ...klass, section: e.target.value })} placeholder="Section" />
            <Select value={klass.session_id} onChange={(e) => setKlass({ ...klass, session_id: e.target.value })}>
              <option value="">No session</option>
              {sessions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
            <Button type="submit">Add class and attendance batch</Button>
          </form>
          <ul className="text-sm">{classes.map((item) => <li key={item.id}>{item.name} {item.section}</li>)}</ul>
        </Card>
      </div>
      <Card className="p-4">
        <h2 className="font-serif text-2xl">Batches on the books</h2>
        {batches.length === 0 ? <p className="mt-2 text-sm text-ink/60">None yet.</p> : (
          <ul className="mt-2 divide-y divide-line text-sm">
            {batches.map((item) => (
              <li key={item.id} className="flex justify-between py-2">
                <span>{item.name}</span>
                <span className="text-ink/50">{item.sport_name || "General"} · {item.schedule?.start_time}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
