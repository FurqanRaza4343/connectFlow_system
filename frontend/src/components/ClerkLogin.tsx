import { useEffect, useRef, useState } from 'react';
import { ClerkProvider, SignIn, SignUp, useAuth, useClerk } from '@clerk/react';
import { errMessage, Me } from '../api';
import { Logo } from './Logo';

type Props = { publishableKey: string; onDone: (m: Me) => void; invite?: string; admin?: boolean };

/** Clerk proves who the person is; our server then hands out its own session cookie (POST /api/auth/clerk). */
function Inner({ onDone, invite, admin }: Omit<Props, 'publishableKey'>) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const clerk = useClerk();
  const [mode, setMode] = useState<'in' | 'up'>(location.hash.startsWith('#signup') || invite ? 'up' : 'in');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || started.current) return;
    started.current = true;
    setBusy(true);
    (async () => {
      try {
        const token = await getToken();
        const res = await fetch('/api/auth/clerk', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'ConnectFlow', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ invite: invite || '', company: '' }),
        });
        const j: unknown = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(errMessage(j, res.status));
        if (invite) history.replaceState(null, '', location.pathname + location.hash);
        onDone(j as Me);
      } catch (e) {
        setErr((e as Error).message);
        setBusy(false);
      }
    })();
  }, [isLoaded, isSignedIn, getToken, invite, onDone]);

  useEffect(() => { const h = () => setMode(location.hash.startsWith('#signup') ? 'up' : 'in'); addEventListener('hashchange', h); return () => removeEventListener('hashchange', h); }, []);
  const box = (
    <div className="clerk-box">
      {err ? (
        <div className="card auth">
          <h1>We could not sign you in</h1>
          <div className="err" role="alert">{err}</div>
          <button className="primary big" onClick={() => { started.current = false; setErr(''); void clerk.signOut(); }}>Sign out and try again</button>
        </div>
      ) : busy || isSignedIn ? (
        <div className="center muted">Signing you in…</div>
      ) : !isLoaded ? (
        <div className="center muted">Loading…</div>
      ) : (
        <>
          {mode === 'in' ? <SignIn routing="hash" signUpUrl="#signup" /> : <SignUp routing="hash" signInUrl="#login" />}
        </>
      )}
    </div>
  );
  if (admin) return <div className="ad-login"><div className="ad-box"><span className="ad-brand"><img src="/logo.svg" alt="" />ConnectFlow <span className="ad-pill">ADMIN</span></span>{box}</div></div>;
  return (
    <div className="auth-page">
      <aside className="auth-brand">
        <a className="auth-home" href={import.meta.env.VITE_SITE_URL || '/'}><Logo size={36} /></a>
        <div className="auth-pitch">
          <h2>Find leads.<br />Win them on <em>WhatsApp.</em></h2>
          <p>ConnectFlow researches every prospect, writes the first message, and protects your number. You approve — it sends.</p>
        </div>
        <small className="muted">© {new Date().getFullYear()} ConnectFlow</small>
      </aside>
      <main className="auth-form-wrap">{box}</main>
    </div>
  );
}

export default function ClerkLogin({ publishableKey, ...rest }: Props) {
  return <ClerkProvider publishableKey={publishableKey}><Inner {...rest} /></ClerkProvider>;
}
