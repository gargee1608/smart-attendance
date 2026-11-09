import { FormEvent, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Select } from "../components/ui";
import { api, getUser, setOrg, type Row } from "../lib/api";

const types = [
  ["sports_academy", "Sports academy"],
  ["school", "School"],
  ["college", "College"],
  ["coaching_institute", "Coaching institute"],
  ["private_org", "Private organization"],
];

export function OrganizationsPage() {
  const user = getUser();
  const [items, setItems] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", org_type: "sports_academy", contact_person: "", email: "", phone: "", address: "" });

  function load() {
    api<{ items: Row[] }>("/api/orgs").then((data) => setItems(data.items)).catch((err) => setError(err.message));
  }
  useEffect(load, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const org = await api<Row>("/api/orgs", { method: "POST", body: JSON.stringify(form) });
      setOrg(org.id);
      setForm({ ...form, name: "" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the organization");
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M01</p>
        <h1 className="font-serif text-4xl">Organizations</h1>
        <p className="max-w-2xl text-sm text-ink/70">Each academy gets a code such as ORG-0001. Photos, embeddings, attendance, and reports stay inside that code.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      {user?.role === "super_admin" && (
        <Card className="p-4">
          <h2 className="font-serif text-2xl">Onboard an organization</h2>
          <form className="mt-4 grid gap-3 md:grid-cols-2" onSubmit={create}>
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
            <Field label="Type">
              <Select value={form.org_type} onChange={(e) => setForm({ ...form, org_type: e.target.value })}>
                {types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </Select>
            </Field>
            <Field label="Contact person"><Input value={form.contact_person} onChange={(e) => setForm({ ...form, contact_person: e.target.value })} /></Field>
            <Field label="Email"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Phone"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
            <Field label="Address"><Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field>
            <div className="md:col-span-2"><Button type="submit">Create organization</Button></div>
          </form>
        </Card>
      )}
      {items.length === 0 ? <Empty title="No organizations yet" body="A super admin creates the first academy, school, or institute here." /> : (
        <div className="grid gap-3">
          {items.map((org) => (
            <Card key={org.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div>
                <p className="font-serif text-2xl">{org.name}</p>
                <p className="text-sm text-ink/60">{org.code} · {org.org_type.replaceAll("_", " ")} · {org.contact_person || "No contact yet"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={org.status === "active" ? "moss" : "clay"}>{org.status}</Badge>
                {user?.role === "super_admin" && (
                  <Button variant="quiet" onClick={() => { setOrg(org.id); window.location.assign("/"); }}>Work inside</Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
