import { useEffect, useRef } from 'react';

declare global { interface Window { turnstile?: { render: (el: HTMLElement, o: Record<string, unknown>) => string; remove: (id: string) => void; reset: (id?: string) => void } } }

const SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

/** Cloudflare Turnstile widget. Rendered only when the server exposes a site key (captcha is optional). */
export function Turnstile({ siteKey, onToken }: { siteKey: string; onToken: (token: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let id: string | undefined; let gone = false;
    const draw = () => {
      if (gone || !box.current || !window.turnstile) return;
      id = window.turnstile.render(box.current, { sitekey: siteKey, theme: 'auto', callback: (t: string) => onToken(t), 'expired-callback': () => onToken(''), 'error-callback': () => onToken('') });
    };
    if (window.turnstile) draw();
    else {
      let s = document.querySelector<HTMLScriptElement>(`script[src="${SRC}"]`);
      if (!s) { s = document.createElement('script'); s.src = SRC; s.async = true; document.head.appendChild(s); }
      s.addEventListener('load', draw);
    }
    return () => { gone = true; if (id && window.turnstile) window.turnstile.remove(id); };
  }, [siteKey]);
  return <div ref={box} className="turnstile" />;
}
