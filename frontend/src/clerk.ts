/** Small helpers that do NOT pull the Clerk SDK into the main bundle (it is loaded lazily by components/ClerkLogin). */
export type AuthConfig = { google: boolean; turnstileSiteKey?: string; clerkPublishableKey?: string; legacyLogin?: boolean };

let cached: Promise<AuthConfig> | null = null;
export function authConfig(): Promise<AuthConfig> {
  cached ??= fetch('/api/auth/config', { credentials: 'same-origin' }).then((r) => r.json() as Promise<AuthConfig>).catch(() => ({ google: false }) as AuthConfig);
  return cached;
}

/** Ends the Clerk session too (our own cookie is cleared by /api/auth/logout). Without this the sign-in page would log the person straight back in. */
export async function clerkSignOut(): Promise<void> {
  try { await (window as unknown as { Clerk?: { signOut: () => Promise<void> } }).Clerk?.signOut(); } catch { /* not signed in with Clerk */ }
}
