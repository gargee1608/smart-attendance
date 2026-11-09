export type Row = Record<string, any>;

const TOKEN = "sa_token";
const USER = "sa_user";
const ORG = "sa_org";

export function getToken() {
  return localStorage.getItem(TOKEN) || "";
}

export function getUser(): Row | null {
  const raw = localStorage.getItem(USER);
  return raw ? JSON.parse(raw) : null;
}

export function getOrg() {
  return localStorage.getItem(ORG) || "";
}

export function setSession(token: string, user: Row) {
  localStorage.setItem(TOKEN, token);
  localStorage.setItem(USER, JSON.stringify(user));
  if (user.org_id) localStorage.setItem(ORG, user.org_id);
}

export function setOrg(orgId: string) {
  localStorage.setItem(ORG, orgId);
}

export function clearSession() {
  localStorage.removeItem(TOKEN);
  localStorage.removeItem(USER);
  localStorage.removeItem(ORG);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailOf(data: any, fallback: string) {
  if (!data) return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((item: any) => item.msg || JSON.stringify(item)).join(" ");
  return fallback;
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const org = getOrg();
  if (org) headers.set("X-Organization-Id", org);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    clearSession();
    if (!path.endsWith("/login")) window.location.assign("/login");
    throw new ApiError(401, "Sign in again");
  }
  const type = response.headers.get("content-type") || "";
  if (!response.ok) {
    const data = type.includes("json") ? await response.json().catch(() => null) : null;
    throw new ApiError(response.status, detailOf(data, response.statusText));
  }
  if (response.status === 204) return undefined as T;
  if (type.includes("json")) return response.json();
  return (await response.text()) as T;
}

export async function downloadCsv(month: string) {
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${getToken()}`);
  if (getOrg()) headers.set("X-Organization-Id", getOrg());
  const response = await fetch(`/api/reports/attendance.csv?month=${encodeURIComponent(month)}`, { headers });
  if (!response.ok) throw new ApiError(response.status, "Could not export the register");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `attendance-${month}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
