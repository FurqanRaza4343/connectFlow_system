export type Brand = { color: string | null; sidebar: string | null; page: string | null; logo: string | null };
export type Me = {
  user: { id: string; email: string; name: string; isPlatformAdmin: boolean; avatar: string; emailVerified: boolean; mfaEnabled?: boolean };
  tenant: {
    id: string; name: string; status: string; brand: Brand; website: string;
    plan: { id: string; name: string; maxInstances: number; maxMsgsDay: number; maxMembers: number };
  } | null;
  role: string;
};
export type Instance = { id: string; label: string; phone: string; status: string; engine: string; outreachEnabled?: boolean; overLimit?: boolean; createdAt: string };
export type Via = { id: string; label: string; phone: string; status: string } | null;
export const viaText = (v: Via | undefined) => (v ? (v.phone ? `+${v.phone.replace(/\D/g, '')}` : v.label) : '');

export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'ConnectFlow' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (j as any).detail;
    throw Object.assign(new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join(', ') : `Error ${res.status}`), { status: res.status });
  }
  return j as T;
}
