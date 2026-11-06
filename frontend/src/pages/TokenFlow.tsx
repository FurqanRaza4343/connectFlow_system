import { FormEvent, useEffect, useState } from 'react';
import { api, Me } from '../api';
import { Logo } from '../components/Logo';

export type Flow = { kind: 'verify' | 'reset' | 'invite'; token: string };

export function tokenFromUrl(): Flow | null {
  const q = new URLSearchParams(location.search);
  for (const kind of ['verify', 'reset', 'invite'] as const) {
    const t = q.get(kind);
    if (t) return { kind, token: t };
  }
  return null;
}

/** Pages opened from an email link: confirm email, choose a new password, accept an invitation. */
export function TokenFlow({ flow, me, onDone }: { flow: Flow; me: Me | null; onDone: (m?: Me) => void }) {
  const [state, setState] = useState<'work' | 'ok' | 'err' | 'form'>(flow.kind === 'verify' ? 'work' : 'form');
  const [msg, setMsg] = useState('');
  const [pw, setPw] = useState('');
  const [name, setName] = useState('');
  const [invite, setInvite] = useState<{ email: string; workspace: string; role: string; hasAccount: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (flow.kind === 'verify') api('/api/auth/verify', 'POST', { token: flow.token }).then(() => setState('ok')).catch((e) => { setMsg(e.message); setState('err'); });
    if (flow.kind === 'invite') api<any>(`/api/team/invites/preview?token=${encodeURIComponent(flow.token)}`).then(setInvite).catch((e) => { setMsg(e.message); setState('err'); });
  }, [flow]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setMsg('');
    try {
      if (flow.kind === 'reset') { await api('/api/auth/reset', 'POST', { token: flow.token, password: pw }); setState('ok'); }
      else { const m = await api<any>('/api/team/invites/accept', 'POST', { token: flow.token, name, password: pw }); if (m.mfaRequired) { setMsg('You joined the workspace. Sign in with your password and two-factor code.'); setTimeout(() => onDone(), 2200); } else onDone(m as Me); }
    } catch (x: any) { setMsg(x.message); } finally { setBusy(false); }
  }

  const title = flow.kind === 'verify' ? 'Confirming your email' : flow.kind === 'reset' ? 'Choose a new password' : 'Join your team';
  const done = state === 'ok';
  return (
    <div className="center">
      <div className="card auth tf">
        <Logo size={34} />
        <h1>{done ? (flow.kind === 'verify' ? 'Email confirmed ✓' : 'Password updated ✓') : title}</h1>
        {state === 'work' && <p className="muted">One moment…</p>}
        {state === 'err' && <><div className="err">{msg}</div><button className="primary big" onClick={() => onDone()}>Back to sign in</button></>}
        {done && <button className="primary big" onClick={() => onDone()}>{me ? 'Open dashboard' : 'Sign in'}</button>}
        {state === 'form' && flow.kind === 'invite' && invite && (
          <form onSubmit={submit} className="stack">
            <p className="muted">You’ve been invited to <b>{invite.workspace}</b> as <b>{invite.role}</b> ({invite.email}).</p>
            {invite.hasAccount
              ? <><p className="muted">You already have an account with this email. Sign in first, then open this link again.</p>{msg && <div className="err">{msg}</div>}<button className="primary big" type="submit">Join workspace</button></>
              : <><label>Your name<input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></label>
                  <label>Choose a password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={10} required autoComplete="new-password" placeholder="At least 10 characters" /></label>
                  {msg && <div className="err">{msg}</div>}<button className="primary big" disabled={busy}>{busy ? <span className="spin" /> : 'Create account & join'}</button></>}
          </form>
        )}
        {state === 'form' && flow.kind === 'reset' && (
          <form onSubmit={submit} className="stack">
            <label>New password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={10} required autoComplete="new-password" placeholder="At least 10 characters" /></label>
            {msg && <div className="err">{msg}</div>}
            <button className="primary big" disabled={busy}>{busy ? <span className="spin" /> : 'Update password'}</button>
          </form>
        )}
      </div>
    </div>
  );
}
