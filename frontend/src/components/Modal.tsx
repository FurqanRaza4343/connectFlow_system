import { ReactNode, RefObject, useEffect, useRef } from 'react';

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';
const stack: symbol[] = []; // open dialogs, topmost last: only the top one reacts to Escape / Tab

/**
 * Shared dialog behaviour: focus moves inside on open, Tab stays inside, Escape closes the topmost dialog,
 * and focus returns to whatever opened it when the dialog goes away.
 */
export function useDialog(ref: RefObject<HTMLElement>, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const me = Symbol('dialog');
    stack.push(me);
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    (el?.querySelector<HTMLElement>('input:not([type=checkbox]),textarea,select') ?? el?.querySelector<HTMLElement>(FOCUSABLE) ?? el)?.focus();
    const h = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== me) return;
      if (e.key === 'Escape') { close.current(); return; }
      if (e.key !== 'Tab' || !el) return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((x) => x.offsetParent !== null || x === document.activeElement);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !el.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    };
    addEventListener('keydown', h);
    return () => {
      removeEventListener('keydown', h);
      const i = stack.indexOf(me); if (i >= 0) stack.splice(i, 1);
      if (prev && document.contains(prev)) prev.focus();
    };
  }, [ref]);
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useDialog(ref, onClose);
  return (
    <div className="modal" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} className="card dlg" style={{ maxWidth: wide ? 860 : 560 }} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row between"><h3 style={{ margin: 0 }}>{title}</h3><button className="ghost" onClick={onClose} aria-label="Close">✕</button></div>
        {children}
      </div>
    </div>
  );
}
