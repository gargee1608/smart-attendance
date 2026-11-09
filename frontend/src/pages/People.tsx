import { FormEvent, useEffect, useState } from "react";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, Input, Select } from "../components/ui";
import { api, getOrg, getUser, type Row } from "../lib/api";

export function PeoplePage() {
  const user = getUser();
  const [items, setItems] = useState<Row[]>([]);
  const [batches, setBatches] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Row | null>(null);
  const [form, setForm] = useState({ person_type: "student", full_name: "", phone: "", email: "", guardian_name: "", guardian_phone: "", sport_specialization: "", batch_id: "" });

  function load() {
    if (user?.role === "super_admin" && !getOrg()) return;
    api<{ items: Row[] }>("/api/persons").then((data) => setItems(data.items)).catch((err) => setError(err.message));
    api<{ items: Row[] }>("/api/batches").then((data) => setBatches(data.items)).catch(() => setBatches([]));
  }
  useEffect(load, [user?.role]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/persons", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          batch_ids: form.batch_id ? [form.batch_id] : [],
        }),
      });
      setOpen(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the person");
    }
  }

  async function upload(person: Row, files: FileList | null) {
    if (!files?.length) return;
    const body = new FormData();
    Array.from(files).forEach((file) => body.append("files", file));
    try {
      const result = await api(`/api/persons/${person.id}/photos`, { method: "POST", body });
      setSelected(result.person);
      setError(result.rejected?.length ? result.rejected.map((item: Row) => `${item.file}: ${item.reason}`).join(" · ") : "");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
  }

  async function confirm(person: Row) {
    const updated = await api(`/api/persons/${person.id}/confirm-enrollment`, { method: "POST" });
    setSelected(updated);
    load();
  }

  if (user?.role === "super_admin" && !getOrg()) {
    return <Empty title="Choose an organization" body="Pick the academy in the sidebar before adding people." />;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M02 · M04</p>
          <h1 className="font-serif text-4xl">People</h1>
          <p className="max-w-2xl text-sm text-ink/70">A profile is not enrolled until someone on staff confirms the photos. Embeddings are stored apart from the image files.</p>
        </div>
        {user?.role !== "student" && <Button onClick={() => setOpen(true)}>Add person</Button>}
      </header>
      <ErrorNote>{error}</ErrorNote>
      {items.length === 0 ? <Empty title="No one on the roll yet" body="Add a student, coach, or teacher, then upload more than one photo if you can." /> : (
        <div className="grid gap-3">
          {items.map((person) => (
            <Card key={person.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div>
                <p className="font-serif text-2xl">{person.full_name}</p>
                <p className="text-sm text-ink/60">{person.code} · {person.person_type} · {person.embedding_count} embedding{person.embedding_count === 1 ? "" : "s"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={person.enrollment_status === "enrolled" ? "moss" : "amber"}>{person.enrollment_status.replaceAll("_", " ")}</Badge>
                {user?.role !== "student" && <Button variant="quiet" onClick={() => setSelected(person)}>Photos</Button>}
              </div>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen} title="New person">
        <form className="grid gap-3" onSubmit={create}>
          <Field label="Role">
            <Select value={form.person_type} onChange={(e) => setForm({ ...form, person_type: e.target.value })}>
              <option value="student">Student</option>
              <option value="coach">Coach</option>
              <option value="teacher">Teacher</option>
              <option value="visitor">Visitor</option>
            </Select>
          </Field>
          <Field label="Full name"><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></Field>
          <Field label="Phone"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
          <Field label="Email"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Guardian"><Input value={form.guardian_name} onChange={(e) => setForm({ ...form, guardian_name: e.target.value })} /></Field>
          <Field label="Guardian phone"><Input value={form.guardian_phone} onChange={(e) => setForm({ ...form, guardian_phone: e.target.value })} /></Field>
          <Field label="Sport specialization"><Input value={form.sport_specialization} onChange={(e) => setForm({ ...form, sport_specialization: e.target.value })} /></Field>
          <Field label="Batch">
            <Select value={form.batch_id} onChange={(e) => setForm({ ...form, batch_id: e.target.value })}>
              <option value="">None yet</option>
              {batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}
            </Select>
          </Field>
          <Button type="submit">Save profile</Button>
        </form>
      </Dialog>
      <Dialog open={!!selected} onOpenChange={(value) => !value && setSelected(null)} title={selected?.full_name || "Photos"}>
        {selected && (
          <div className="space-y-3 text-sm">
            <p>{selected.code} is {selected.enrollment_status.replaceAll("_", " ")}. Upload several angles. A photo is kept only when one live face clears the quality check.</p>
            <Input type="file" accept="image/*" multiple capture="environment" onChange={(e) => upload(selected, e.target.files)} />
            <Button onClick={() => confirm(selected)} disabled={!selected.embedding_count || selected.enrollment_status === "enrolled"}>
              Confirm enrollment
            </Button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
