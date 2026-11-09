import { FormEvent, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Select } from "../components/ui";
import { api, getOrg, getUser, type Row } from "../lib/api";

export function AttendancePage() {
  const user = getUser();
  const today = new Date().toISOString().slice(0, 10);
  const [onDate, setOnDate] = useState(today);
  const [rows, setRows] = useState<Row[]>([]);
  const [people, setPeople] = useState<Row[]>([]);
  const [batches, setBatches] = useState<Row[]>([]);
  const [result, setResult] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [manual, setManual] = useState({ person_id: "", batch_id: "", on_date: today, status: "present", note: "" });
  const [summaryPerson, setSummaryPerson] = useState("");
  const [month, setMonth] = useState(today.slice(0, 7));
  const [summary, setSummary] = useState<Row | null>(null);
  const [policy, setPolicy] = useState<Row | null>(null);

  function load() {
    if (user?.role === "super_admin" && !getOrg()) return;
    api<{ items: Row[] }>(`/api/attendance?on_date=${onDate}`).then((data) => setRows(data.items)).catch((err) => setError(err.message));
    api<{ items: Row[] }>("/api/persons").then((data) => setPeople(data.items)).catch(() => undefined);
    api<{ items: Row[] }>("/api/batches").then((data) => setBatches(data.items)).catch(() => undefined);
    api("/api/attendance/policy").then(setPolicy).catch(() => undefined);
  }
  useEffect(load, [onDate, user?.role]);

  async function checkIn(file: File | undefined, batchId: string) {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    if (batchId) body.append("batch_id", batchId);
    setResult(null);
    try {
      const data = await api("/api/attendance/check-in", { method: "POST", body });
      setResult(data);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Check-in failed");
    }
  }

  async function correct(id: string, status: string) {
    const note = window.prompt("Note for the audit log", "") || "";
    await api(`/api/attendance/${id}/corrections`, { method: "POST", body: JSON.stringify({ status, note }) });
    load();
  }

  async function mark(event: FormEvent) {
    event.preventDefault();
    await api("/api/attendance/manual", { method: "POST", body: JSON.stringify({ ...manual, batch_id: manual.batch_id || null }) });
    load();
  }

  async function loadSummary(event: FormEvent) {
    event.preventDefault();
    const data = await api(`/api/attendance/summary?person_id=${summaryPerson}&month=${month}`);
    setSummary(data);
  }

  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    const updated = await api("/api/attendance/policy", { method: "PUT", body: JSON.stringify(policy) });
    setPolicy(updated);
  }

  if (user?.role === "super_admin" && !getOrg()) return <Empty title="Choose an organization" body="Attendance is recorded inside one organization." />;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M07</p>
        <h1 className="font-serif text-4xl">Attendance</h1>
        <p className="max-w-2xl text-sm text-ink/70">A second check-in on the same day and batch does not create another row. If the face is uncertain, nobody is marked present and no name is guessed.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      {user?.role !== "student" && (
        <Card className="grid gap-4 p-4 lg:grid-cols-[1fr_1fr]">
          <CheckInForm batches={batches} onFile={checkIn} />
          <div>
            <h2 className="font-serif text-2xl">Last result</h2>
            {!result && <p className="mt-2 text-sm text-ink/60">Take or upload one photo of one person.</p>}
            {result && <Result result={result} />}
          </div>
        </Card>
      )}
      <Card className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-serif text-2xl">Register</h2>
          <Input type="date" className="max-w-48" value={onDate} onChange={(e) => setOnDate(e.target.value)} />
        </div>
        {rows.length === 0 ? <p className="mt-3 text-sm text-ink/60">Nothing recorded for this date.</p> : (
          <ul className="mt-3 divide-y divide-line text-sm">
            {rows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>{row.full_name} · {row.person_code} · {row.batch_name || "No batch"}</span>
                <span className="flex items-center gap-2">
                  <Badge tone={row.status === "present" || row.status === "manual" ? "moss" : "amber"}>{row.status}</Badge>
                  {user?.role !== "student" && (
                    <Select className="w-32" value={row.status} onChange={(e) => correct(row.id, e.target.value)}>
                      {["present", "late", "absent", "leave", "holiday", "manual"].map((status) => <option key={status}>{status}</option>)}
                    </Select>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {user?.role !== "student" && (
        <Card className="p-4">
          <h2 className="font-serif text-2xl">Manual mark</h2>
          <form className="mt-3 grid gap-3 md:grid-cols-5" onSubmit={mark}>
            <Select value={manual.person_id} onChange={(e) => setManual({ ...manual, person_id: e.target.value })} required>
              <option value="">Person</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.full_name}</option>)}
            </Select>
            <Select value={manual.batch_id} onChange={(e) => setManual({ ...manual, batch_id: e.target.value })}>
              <option value="">No batch</option>
              {batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}
            </Select>
            <Input type="date" value={manual.on_date} onChange={(e) => setManual({ ...manual, on_date: e.target.value })} required />
            <Select value={manual.status} onChange={(e) => setManual({ ...manual, status: e.target.value })}>
              {["present", "late", "absent", "leave", "holiday", "manual"].map((status) => <option key={status}>{status}</option>)}
            </Select>
            <Button type="submit">Save</Button>
          </form>
        </Card>
      )}
      <Card className="p-4">
        <h2 className="font-serif text-2xl">Monthly percentage</h2>
        <p className="text-sm text-ink/60">Denominator is scheduled weekdays, minus holidays and approved leave when the policy says so. Late counts as present unless you turn that off.</p>
        <form className="mt-3 flex flex-wrap gap-2" onSubmit={loadSummary}>
          <Select className="max-w-xs" value={summaryPerson} onChange={(e) => setSummaryPerson(e.target.value)} required>
            <option value="">Person</option>
            {people.map((person) => <option key={person.id} value={person.id}>{person.full_name}</option>)}
          </Select>
          <Input className="max-w-40" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          <Button type="submit" variant="quiet">Calculate</Button>
        </form>
        {summary && (
          <p className="mt-3 font-serif text-3xl">{summary.percentage === null ? "No scheduled days" : `${summary.percentage}%`} <span className="text-base text-ink/60">{summary.present_days}/{summary.scheduled_days} days</span></p>
        )}
      </Card>
      {policy && (user?.role === "org_admin" || user?.role === "super_admin") && (
        <Card className="p-4">
          <h2 className="font-serif text-2xl">Policy</h2>
          <form className="mt-3 grid gap-3 md:grid-cols-3" onSubmit={savePolicy}>
            <label className="text-sm"><input type="checkbox" checked={!!policy.count_late_as_present} onChange={(e) => setPolicy({ ...policy, count_late_as_present: e.target.checked })} /> Late counts as present</label>
            <label className="text-sm"><input type="checkbox" checked={!!policy.exclude_holidays} onChange={(e) => setPolicy({ ...policy, exclude_holidays: e.target.checked })} /> Exclude holidays</label>
            <label className="text-sm"><input type="checkbox" checked={!!policy.exclude_approved_leave} onChange={(e) => setPolicy({ ...policy, exclude_approved_leave: e.target.checked })} /> Exclude approved leave</label>
            <Field label="Match threshold"><Input type="number" step="0.001" value={policy.match_threshold} onChange={(e) => setPolicy({ ...policy, match_threshold: Number(e.target.value) })} /></Field>
            <Field label="Ambiguity margin"><Input type="number" step="0.001" value={policy.ambiguity_margin} onChange={(e) => setPolicy({ ...policy, ambiguity_margin: Number(e.target.value) })} /></Field>
            <div className="md:col-span-3"><Button type="submit" variant="quiet">Save policy</Button></div>
          </form>
        </Card>
      )}
    </div>
  );
}

function CheckInForm({ batches, onFile }: { batches: Row[]; onFile: (file: File | undefined, batchId: string) => void }) {
  const [batchId, setBatchId] = useState("");
  return (
    <form className="space-y-3" onSubmit={(event) => event.preventDefault()}>
      <h2 className="font-serif text-2xl">Photo check-in</h2>
      <Field label="Batch">
        <Select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
          <option value="">Their first batch</option>
          {batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}
        </Select>
      </Field>
      <Input type="file" accept="image/*" capture="environment" onChange={(e) => onFile(e.target.files?.[0], batchId)} />
    </form>
  );
}

function Result({ result }: { result: Row }) {
  if (result.result === "known") {
    return (
      <p className="mt-2 text-sm">
        <span className="font-medium">{result.person_name}</span> · confidence {(result.confidence * 100).toFixed(1)}% · {result.duplicate ? "already recorded today" : `marked ${result.attendance?.status}`}
      </p>
    );
  }
  return (
    <p className="mt-2 text-sm">
      No name assigned ({result.result.replaceAll("_", " ")}). Confidence {result.confidence == null ? "n/a" : `${(result.confidence * 100).toFixed(1)}%`}. {result.unknown_code ? `Queued as ${result.unknown_code}.` : ""}
    </p>
  );
}
