import { initDemo } from './demo';

export function initHome(reduced: boolean) {
  steps(reduced);
  carousel();
  useCases();
  chartInView();
  initDemo(reduced);
}

// ---- "How it works": one stage, four panels. Scroll picks the step on desktop; click always works. ----
function steps(reduced: boolean) {
  const list = document.getElementById('steps');
  const stage = document.getElementById('stage');
  if (!list || !stage) return;
  const items = Array.from(list.querySelectorAll<HTMLElement>('.step'));
  const panels = Array.from(stage.querySelectorAll<HTMLElement>('.sp'));
  let cur = -1;
  const show = (i: number) => {
    if (i === cur) return;
    cur = i;
    items.forEach((s, k) => { s.classList.toggle('on', k === i); s.setAttribute('aria-selected', String(k === i)); });
    panels.forEach((p, k) => p.classList.toggle('on', k === i));
  };
  let userPicked = false;
  items.forEach((s, i) => s.addEventListener('click', () => { userPicked = true; show(i); }));
  show(0);

  const desktop = matchMedia('(min-width: 901px)');
  let raf = 0;
  const pick = () => {
    raf = 0;
    if (!desktop.matches || userPicked) return;
    const mid = innerHeight * 0.5;
    let best = 0, bd = Infinity;
    items.forEach((s, i) => { const r = s.getBoundingClientRect(); const d = Math.abs(r.top + r.height / 2 - mid); if (d < bd) { bd = d; best = i; } });
    const first = items[0].getBoundingClientRect().top, last = items[items.length - 1].getBoundingClientRect().bottom;
    if (first < innerHeight && last > 0) show(best);
  };
  addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(pick); }, { passive: true });
  addEventListener('wheel', () => { userPicked = false; }, { passive: true });

  // mobile: gently auto-advance while the stage is on screen, until the visitor taps a step
  if (!reduced) {
    let timer: number | undefined;
    new IntersectionObserver((es) => {
      clearInterval(timer);
      if (es[0].isIntersecting && !desktop.matches) timer = window.setInterval(() => { if (!userPicked) show((cur + 1) % items.length); }, 4200);
    }, { threshold: 0.5 }).observe(stage);
  }
}

// ---- Feature carousel: arrows, drag, progress ----
function carousel() {
  const track = document.getElementById('track');
  if (!track) return;
  const prev = document.getElementById('car-prev') as HTMLButtonElement;
  const next = document.getElementById('car-next') as HTMLButtonElement;
  const prog = document.getElementById('car-progress') as HTMLElement;
  const step = () => (track.querySelector('.fcard') as HTMLElement).getBoundingClientRect().width + 16;
  const update = () => {
    const max = track.scrollWidth - track.clientWidth;
    prev.disabled = track.scrollLeft < 4;
    next.disabled = track.scrollLeft > max - 4;
    const w = track.clientWidth / track.scrollWidth;
    prog.style.width = `${w * 100}%`;
    prog.style.transform = `translateX(${(track.scrollLeft / track.clientWidth) * 100}%)`;
  };
  prev.addEventListener('click', () => track.scrollBy({ left: -step(), behavior: 'smooth' }));
  next.addEventListener('click', () => track.scrollBy({ left: step(), behavior: 'smooth' }));
  track.addEventListener('scroll', update, { passive: true });
  addEventListener('resize', update);
  track.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') next.click(); if (e.key === 'ArrowLeft') prev.click(); });
  update();

  // card animations only run while their card is actually on screen (keeps scrolling smooth on phones)
  const cards = Array.from(track.querySelectorAll<HTMLElement>('.fcard'));
  const io = new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle('off', !e.isIntersecting)), { root: null, rootMargin: '120px' });
  cards.forEach((c) => io.observe(c));

  // mouse drag (touch already scrolls natively)
  let down = false, sx = 0, sl = 0, moved = false;
  track.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') return; down = true; moved = false; sx = e.clientX; sl = track.scrollLeft; });
  addEventListener('pointermove', (e) => {
    if (!down) return;
    const dx = e.clientX - sx;
    if (Math.abs(dx) > 4) { moved = true; track.classList.add('drag'); }
    track.scrollLeft = sl - dx;
  });
  addEventListener('pointerup', () => { if (!down) return; down = false; track.classList.remove('drag'); if (moved) track.scrollTo({ left: Math.round(track.scrollLeft / step()) * step(), behavior: 'smooth' }); });
}

// ---- Use-case tabs ----
function useCases() {
  const tabs = Array.from(document.querySelectorAll<HTMLElement>('.uc-tab'));
  if (!tabs.length) return;
  tabs.forEach((t) => t.addEventListener('click', () => {
    tabs.forEach((x) => x.setAttribute('aria-selected', String(x === t)));
    document.querySelectorAll<HTMLElement>('.uc-panel').forEach((p) => p.classList.toggle('on', p.dataset.ucp === t.dataset.uc));
  }));
}

// ---- Chart bars animate when scrolled into view ----
function chartInView() {
  const c = document.getElementById('chart');
  if (!c) return;
  new IntersectionObserver((es, io) => { if (es[0].isIntersecting) { c.classList.add('in'); io.disconnect(); } }, { threshold: 0.35 }).observe(c);
}
