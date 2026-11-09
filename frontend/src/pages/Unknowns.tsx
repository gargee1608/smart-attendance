import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Select } from "../components/ui";
import { api, getOrg, getUser, type Row } from "../lib/api";

export function UnknownsPage() {
  const user = getUser();
  const [items, setItems] = useState<Row[]>([]);
  const [people, setPeople] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [assign, setAssign] = useState<Record<string, string>>({});

  function load() {
    if (user?.role === "super_admin" && !getOrg()) return;
    api<{ items: Row[] }>("/api/unknown?status=all").then((data) => setItems(data.items)).catch((err) => setError(err.message));
    api<{ items: Row[] }>("/api/persons").then((data) => setPeople(data.items)).catch(() => undefined);
  }
  useEffect(load, [user?.role]);

  async function resolve(id: string, body: Row) {
    try {
      await api(`/api/unknown/${id}/resolve`, { method: "POST", body: JSON.stringify(body) });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the review");
    }
  }

  if (user?.role === "super_admin" && !getOrg()) return <Empty title="Choose an organization" body="Unrecognized faces are queued per organization." />;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">M06</p>
        <h1 className="font-serif text-4xl">Unrecognized faces</h1>
        <p className="max-w-2xl text-sm text-ink/70">Repeat observations share one provisional id. Nothing here is a name, and nothing here is an accusation. Snapshots are not added to the gallery unless you explicitly register them and then confirm enrollment.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>
      {items.length === 0 ? <Empty title="Queue is clear" body="When a live, good-quality face misses the confidence threshold, it will wait here." /> : (
        <div className="grid gap-3">
          {items.map((item) => (
            <Card key={item.id} className="space-y-3 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-serif text-2xl">{item.code}</p>
                  <p className="text-sm text-ink/60">{item.observation_count} observation{item.observation_count === 1 ? "" : "s"} · {item.latest_camera || "no camera"} · {item.last_seen?.replace("T", " ").slice(0, 16)}</p>
                </div>
                <Badge tone={item.status === "open" ? "amber" : "ink"}>{item.status}</Badge>
              </div>
              {item.status === "open" && (
                <div className="flex flex-wrap gap-2">
                  <Select className="max-w-xs" value={assign[item.id] || ""} onChange={(e) => setAssign({ ...assign, [item.id]: e.target.value })}>
                    <option value="">Assign to existing person</option>
                    {people.map((person) => <option key={person.id} value={person.id}>{person.full_name} · {person.code}</option>)}
                  </Select>
                  <Button variant="quiet" disabled={!assign[item.id]} onClick={() => resolve(item.id, { action: "assign", person_id: assign[item.id] })}>Assign</Button>
                  <Button variant="quiet" onClick={() => {
                    const full_name = window.prompt("Name for the new profile");
                    if (full_name) resolve(item.id, { action: "register", full_name, person_type: "student", use_observations_as_enrollment: false });
                  }}>Register new</Button>
                  <Button variant="quiet" onClick={() => {
                    const full_name = window.prompt("Visitor name", "Authorized visitor");
                    if (full_name) resolve(item.id, { action: "visitor", full_name });
                  }}>Mark visitor</Button>
                  <Button variant="quiet" onClick={() => resolve(item.id, { action: "dismiss" })}>Dismiss</Button>
                  <Button variant="danger" onClick={() => resolve(item.id, { action: "delete" })}>Delete</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
