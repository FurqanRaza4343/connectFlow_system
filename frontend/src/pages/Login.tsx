import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api, Me } from '../api';
import { Logo } from '../components/Logo';
import { GoogleIcon } from '../components/GoogleIcon';
import { CUSTOMER_CLS, MfaEnrol, MfaStep } from '../components/Mfa';
import { Turnstile } from '../components/Turnstile';

type Mode = 'login' | 'signup';

function strength(pw: string): { score: number; label: string } {
  let s = 0;
  if (pw.length >= 10) s++;
  if (pw.length >= 14) s++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++;
  return { score: s, label: ['Too short', 'Weak', 'Okay', 'Good', 'Strong'][s] };
}

export function Login({ onDone }: { onDone: (m: Me) => void }) {
  const [mode, setMode] = useState<Mode>(location.hash === '#signup' ? 'signup' : 'login');
  const [f, setF] = useState({ email: '', password: '', name: '', company: '' });
  const [err, setErr] = useState(new URLSearchParams(location.search).get('auth_error') || '');
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [google, setGoogle] = useState<boolean | null>(null);
  const [forgot, setForgot] = useState(false);
  const [mfaToken, setMfaToken] = useState(new URLSearchParams(location.search).get('mfa_token') || '');  // set by the email step or by Google sign-in
  const [enrol, setEnrol] = useState(false);              // platform admin signed in but must set up two-step verification first
  const [siteKey, setSiteKey] = useState('');
  const [captcha, setCaptcha] = useState('');
  const [captchaNeeded, setCaptchaNeeded] = useState(false);  // login asks for it only after repeated failures
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const pw = useMemo(() => strength(f.password), [f.password]);

  useEffect(() => { api<{ google: boolean; turnstileSiteKey?: string }>('/api/auth/config').then((c) => { setGoogle(c.google); setSiteKey(c.turnstileSiteKey || ''); }).catch(() => setGoogle(false)); }, []);
  useEffect(() => { api<{ enrolRequired: boolean }>('/api/auth/mfa/status').then((m) => { if (m.enrolRequired) setEnrol(true); }).catch(() => {}); }, []);
  useEffect(() => { if (location.search) history.replaceState(null, '', location.pathname + location.hash); }, []);

  const switchMode = (m: Mode) => { setMode(m); setErr(''); history.replaceState(null, '', `#${m}`); };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(''); setBusy(true);
    try {
      const r = await api<any>(`/api/auth/${mode}`, 'POST', mode === 'login' ? { email: f.email, password: f.password, captcha } : { ...f, captcha });
      if (r.mfaRequired) setMfaToken(r.mfaToken);
      else if (r.enrolRequired) setEnrol(true);
      else onDone(r as Me);
    } catch (x: any) {
      if (x.message === 'captcha_required' || x.message === 'captcha_failed') { setCaptchaNeeded(true); setCaptcha(''); setErr('Please complete the security check below, then try again.'); }
      else setErr(x.message);
    } finally { setBusy(false); }
  }

  const signup = mode === 'signup';
  if (forgot) return <Forgot onBack={() => setForgot(false)} />;
  if (mfaToken) return <div className="center"><div className="card auth"><Logo size={34} /><MfaStep token={mfaToken} cls={CUSTOMER_CLS} onDone={(m) => onDone(m as Me)} onCancel={() => setMfaToken('')} /></div></div>;
  if (enrol) return <div className="center"><div className="card auth"><Logo size={34} /><MfaEnrol forced cls={CUSTOMER_CLS} onDone={(m) => { if (m) onDone(m as Me); else location.reload(); }} /></div></div>;
  const showCaptcha = !!siteKey && (signup || captchaNeeded);
  return (
    <div className="auth-page">
      <aside className="auth-brand">
        <a className="auth-home" href={import.meta.env.VITE_SITE_URL || '/'}><Logo size={36} /></a>
        <div className="auth-pitch">
          <h2>Find leads.<br />Win them on <em>WhatsApp.</em></h2>
          <p>ConnectFlow researches every prospect, writes the first message, and protects your number. You approve — it sends.</p>
          <ul>
            <li><span>✓</span> Link your own WhatsApp number with a QR</li>
            <li><span>✓</span> AI-written, personalised first messages</li>
            <li><span>✓</span> Warm-up, daily caps and automatic opt-out</li>
          </ul>
        </div>
        <small className="muted">© {new Date().getFullYear()} ConnectFlow</small>
      </aside>

      <main className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit} noValidate={false}>
          <div className="auth-mobile-logo"><Logo size={32} /></div>
          <h1>{signup ? 'Create your account' : 'Welcome back'}</h1>
          <p className="muted sub">{signup ? 'Start your free trial. No credit card required.' : 'Sign in to your ConnectFlow workspace.'}</p>

          <a
            className={`google-btn${google ? '' : ' disabled'}`}
            href={google ? `/api/auth/google/start?mode=${mode}` : undefined}
            aria-disabled={!google}
            title={google === false ? 'Google sign-in is not set up on this server yet' : undefined}
            onClick={(e) => { if (!google) { e.preventDefault(); setErr('Google sign-in is not set up on this server yet.'); } }}
          >
            <GoogleIcon /> <span>{signup ? 'Sign up with Google' : 'Continue with Google'}</span>
          </a>

          <div className="divider"><span>or use email</span></div>

          {signup && (
            <div className="two">
              <label>Full name<input value={f.name} onChange={set('name')} autoComplete="name" placeholder="Ayesha Khan" /></label>
              <label>Company<input value={f.company} onChange={set('company')} required minLength={2} autoComplete="organization" placeholder="Acme Ltd" /></label>
            </div>
          )}
          <label>Work email<input type="email" value={f.email} onChange={set('email')} required autoComplete="email" placeholder="you@company.com" /></label>
          <label>
            Password
            <span className="pw">
              <input type={showPw ? 'text' : 'password'} value={f.password} onChange={set('password')} required minLength={signup ? 10 : 1}
                autoComplete={signup ? 'new-password' : 'current-password'} placeholder={signup ? 'At least 10 characters' : 'Your password'} />
              <button type="button" className="eye" onClick={() => setShowPw(!showPw)} aria-label={showPw ? 'Hide password' : 'Show password'}>{showPw ? 'Hide' : 'Show'}</button>
            </span>
          </label>
          {!signup && <button type="button" className="link forgot" onClick={() => setForgot(true)}>Forgot password?</button>}
          {signup && f.password && (
            <div className="meter" aria-live="polite">
              <div className="bars">{[1, 2, 3, 4].map((i) => <i key={i} className={i <= pw.score ? `on s${pw.score}` : ''} />)}</div>
              <small className="muted">{pw.label}</small>
            </div>
          )}

          {showCaptcha && <Turnstile siteKey={siteKey} onToken={setCaptcha} />}
          {err && <div className="err" role="alert">{err}</div>}

          <button className="primary big" disabled={busy || (showCaptcha && !captcha)}>{busy ? <span className="spin" /> : signup ? 'Create account' : 'Sign in'}</button>

          {signup && <p className="legal muted">By creating an account you agree to our Terms and Privacy Policy, and to use ConnectFlow only for lawful, consent-respecting messaging.</p>}
          <p className="switch muted">
            {signup ? 'Already have an account?' : 'New to ConnectFlow?'}{' '}
            <button type="button" className="link" onClick={() => switchMode(signup ? 'login' : 'signup')}>{signup ? 'Sign in' : 'Create an account'}</button>
          </p>
        </form>
      </main>
    </div>
  );
}


function Forgot({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [siteKey, setSiteKey] = useState('');
  const [captcha, setCaptcha] = useState('');
  useEffect(() => { api<{ turnstileSiteKey?: string }>('/api/auth/config').then((c) => setSiteKey(c.turnstileSiteKey || '')).catch(() => {}); }, []);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr('');
    try { await api('/api/auth/forgot', 'POST', { email, captcha }); setSent(true); } catch (x: any) { setErr(x.message === 'captcha_required' || x.message === 'captcha_failed' ? 'Please complete the security check and try again.' : x.message); } finally { setBusy(false); }
  }
  return (
    <div className="center">
      <form className="card auth" onSubmit={submit}>
        <Logo size={34} />
        <h1>Reset your password</h1>
        {sent ? <p className="muted">If an account exists for <b>{email}</b>, we’ve sent a reset link. It is valid for one hour.</p>
          : <><p className="muted">Enter your email and we’ll send you a link to choose a new password.</p>
              <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" /></label>
              {siteKey && <Turnstile siteKey={siteKey} onToken={setCaptcha} />}
              {err && <div className="err">{err}</div>}
              <button className="primary big" disabled={busy || (!!siteKey && !captcha)}>{busy ? <span className="spin" /> : 'Send reset link'}</button></>}
        <button type="button" className="link" onClick={onBack}>← Back to sign in</button>
      </form>
    </div>
  );
}
