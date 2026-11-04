// Small interactions used on the inner pages: code tabs + copy, docs sidebar highlight, safety checklist score.
export function initPages() {
  document.querySelectorAll<HTMLElement>('[data-ctabs]').forEach((box) => {
    const tabs = Array.from(box.querySelectorAll<HTMLButtonElement>('.ctabs-bar button[role="tab"]'));
    const panes = Array.from(box.querySelectorAll<HTMLElement>('pre'));
    tabs.forEach((t, i) => t.addEventListener('click', () => {
      tabs.forEach((x, k) => x.setAttribute('aria-selected', String(k === i)));
      panes.forEach((p, k) => p.classList.toggle('on', k === i));
    }));
    box.querySelector<HTMLButtonElement>('[data-copy]')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      const text = box.querySelector('pre.on')?.textContent || '';
      try { await navigator.clipboard.writeText(text); btn.textContent = 'Copied ✓'; } catch { btn.textContent = 'Press Ctrl+C'; }
      setTimeout(() => (btn.textContent = 'Copy'), 1600);
    });
  });

  const nav = document.querySelector<HTMLElement>('.docs-nav');
  if (nav) {
    const links = Array.from(nav.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'));
    const secs = links.map((a) => document.querySelector<HTMLElement>(a.getAttribute('href')!)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => { if (e.isIntersecting) links.forEach((a) => a.classList.toggle('on', a.getAttribute('href') === `#${e.target.id}`)); });
    }, { rootMargin: '-20% 0px -70% 0px' });
    secs.forEach((s) => io.observe(s));
  }

  const list = document.querySelector<HTMLElement>('.checklist');
  const score = document.getElementById('score');
  if (list && score) {
    const upd = () => {
      const boxes = Array.from(list.querySelectorAll<HTMLInputElement>('input'));
      const n = boxes.filter((b) => b.checked).length;
      score.innerHTML = n === boxes.length ? `<b>${n}/${boxes.length}</b> — your number is set up the way we recommend.` : `<b>${n}/${boxes.length}</b> done. Each unchecked item raises the chance of complaints or restrictions.`;
    };
    list.addEventListener('change', upd); upd();
  }
}
