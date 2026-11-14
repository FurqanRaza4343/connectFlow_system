import { lazy, ReactNode, Suspense, useEffect, useState } from 'react';
import { Me } from '../api';
import { authConfig } from '../clerk';

const ClerkLogin = lazy(() => import('./ClerkLogin'));

/** The sign-in screen: Clerk when the server has a Clerk key, otherwise the built-in password form (`fallback`). */
export function AuthGate({ onDone, fallback, invite, admin }: { onDone: (m: Me) => void; fallback: ReactNode; invite?: string; admin?: boolean }) {
  const [key, setKey] = useState<string | null>(null);
  useEffect(() => { authConfig().then((c) => setKey(c.clerkPublishableKey || '')); }, []);
  if (key === null) return <div className="center muted">Loading…</div>;
  if (!key) return <>{fallback}</>;
  return <Suspense fallback={<div className="center muted">Loading…</div>}><ClerkLogin publishableKey={key} onDone={onDone} invite={invite} admin={admin} /></Suspense>;
}
