import { Camera, ClipboardList, FileSpreadsheet, KeyRound, LayoutDashboard, LogOut, School, Shield, Sparkles, UserRound, Users } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, clearSession, getOrg, getUser, setOrg, type Row } from "../lib/api";
import { cn } from "../lib/utils";

const links = [
  { to: "/", label: "Today", icon: LayoutDashboard, roles: ["super_admin", "org_admin", "coach", "teacher", "student"] },
  { to: "/organizations", label: "Organizations", icon: School, roles: ["super_admin", "org_admin"] },
  { to: "/people", label: "People", icon: Users, roles: ["super_admin", "org_admin", "coach", "teacher", "student"] },
  { to: "/structure", label: "Batches & classes", icon: ClipboardList, roles: ["super_admin", "org_admin", "coach", "teacher"] },
  { to: "/attendance", label: "Attendance", icon: UserRound, roles: ["super_admin", "org_admin", "coach", "teacher", "student"] },
  { to: "/unknown", label: "Review queue", icon: Shield, roles: ["super_admin", "org_admin", "coach", "teacher"] },
  { to: "/cameras", label: "Cameras", icon: Camera, roles: ["super_admin", "org_admin", "coach", "teacher"] },
  { to: "/reports", label: "Reports & import", icon: FileSpreadsheet, roles: ["super_admin", "org_admin", "coach", "teacher"] },
  { to: "/insights", label: "Notes & suggestions", icon: Sparkles, roles: ["super_admin", "org_admin", "coach", "teacher"] },
  { to: "/access", label: "Access & audit", icon: KeyRound, roles: ["super_admin", "org_admin"] },
];

export function Shell() {
  const navigate = useNavigate();
  const user = getUser();
  const [orgs, setOrgs] = useState<Row[]>([]);
  const role = user?.role || "";

  useEffect(() => {
    if (!user) navigate("/login");
  }, [user, navigate]);

  useEffect(() => {
    if (role === "super_admin") {
      api<{ items: Row[] }>("/api/orgs").then((data) => setOrgs(data.items)).catch(() => setOrgs([]));
    }
  }, [role]);

  if (!user) return null;

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
      <aside className="bg-pine text-[#f4efe4] lg:min-h-screen">
        <div className="flex items-end justify-between gap-3 px-4 py-4 lg:block">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-[#f4efe4]/60">Academy register</p>
            <p className="font-serif text-2xl leading-none">Smart Attendance</p>
          </div>
          <button
            className="rounded-md border border-white/15 px-2 py-1 text-xs lg:mt-6 lg:w-full"
            onClick={() => {
              clearSession();
              navigate("/login");
            }}
          >
            <span className="inline-flex items-center gap-1"><LogOut size={14} /> Sign out</span>
          </button>
        </div>
        <p className="px-4 pb-3 text-xs text-[#f4efe4]/70">{user.full_name} · {role.replaceAll("_", " ")}</p>
        {role === "super_admin" && (
          <div className="px-4 pb-3">
            <label className="text-[11px] uppercase tracking-wide text-[#f4efe4]/60">Working organization</label>
            <select
              className="mt-1 w-full rounded-md border border-white/15 bg-[#1a2620] px-2 py-2 text-sm"
              value={getOrg()}
              onChange={(event) => {
                setOrg(event.target.value);
                window.location.reload();
              }}
            >
              <option value="">All organizations</option>
              {orgs.map((org) => (
                <option key={org.id} value={org.id}>{org.code} · {org.name}</option>
              ))}
            </select>
          </div>
        )}
        <nav className="flex gap-1 overflow-auto px-2 pb-3 lg:block lg:space-y-1 lg:px-3">
          {links.filter((link) => link.roles.includes(role)).map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) => cn(
                "flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm",
                isActive ? "bg-[#f4efe4] text-pine" : "text-[#f4efe4]/80 hover:bg-white/5",
              )}
            >
              <link.icon size={16} /> {link.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="px-4 py-5 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
