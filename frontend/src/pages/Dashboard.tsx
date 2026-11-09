import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty } from "../components/ui";
import { api, getOrg, getUser, type Row } from "../lib/api";

export function DashboardPage() {
  const user = getUser();
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user?.role === "super_admin" && !getOrg()) {
      setError("Choose a working organization in the sidebar to see its register.");
      return;
    }
    api("/api/dashboard").then(setData).catch((err) => setError(err.message));
  }, [user?.role]);

  if (error) return <Empty title="Register not open" body={error} />;
  if (!data) return <p className="text-sm text-ink/60">Loading today’s counts…</p>;
  const counts = [
    ["People", data.counts.people],
    ["Enrolled faces", data.counts.enrolled],
    ["Present today", data.counts.present_today],
    ["Open reviews", data.counts.open_unknowns],
    ["Active batches", data.counts.batches],
  ];
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.16em] text-ink/50">Today</p>
        <h1 className="font-serif text-4xl">The floor so far</h1>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {counts.map(([label, value]) => (
          <Card key={String(label)} className="p-4">
            <p className="text-xs uppercase tracking-wide text-ink/50">{label}</p>
            <p className="mt-2 font-serif text-4xl">{value}</p>
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-2xl">Needs a person</h2>
          <Link className="text-sm text-clay" to="/unknown">Open the review queue</Link>
        </div>
        {data.notifications?.length ? (
          <ul className="mt-3 divide-y divide-line text-sm">
            {data.notifications.map((note: Row) => (
              <li key={note.id} className="py-2">{note.message}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-ink/60">No unrecognized faces are waiting.</p>
        )}
      </Card>
    </div>
  );
}
