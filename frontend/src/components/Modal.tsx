import { ReactNode, useEffect } from 'react';

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    addEventListener('keydown', h);
    return () => removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="modal" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card dlg" style={{ maxWidth: wide ? 860 : 560 }} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row between"><h3 style={{ margin: 0 }}>{title}</h3><button className="ghost" onClick={onClose} aria-label="Close">✕</button></div>
        {children}
      </div>
    </div>
  );
}
