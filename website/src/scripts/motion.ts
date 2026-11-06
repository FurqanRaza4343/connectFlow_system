import Lenis from 'lenis';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { initHome } from './home';
import { initPages } from './docs';

gsap.registerPlugin(ScrollTrigger);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------- mobile drawer ----------
const burger = document.getElementById('burger');
const drawer = document.getElementById('drawer');
burger?.addEventListener('click', () => {
  const open = drawer!.classList.toggle('open');
  burger.setAttribute('aria-expanded', String(open));
  document.body.style.overflow = open ? 'hidden' : '';
});

// ---------- pricing currency toggle ----------
const toggle = document.querySelector<HTMLElement>('[data-currency-toggle]');
if (toggle) {
  const set = (cur: string) => {
    toggle.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.cur === cur));
    document.querySelectorAll<HTMLElement>('[data-price]').forEach((el) => { el.textContent = cur === 'PKR' ? el.dataset.pkr! : el.dataset.usd!; });
    document.querySelectorAll<HTMLElement>('[data-cur-label]').forEach((el) => (el.textContent = cur === 'PKR' ? 'PKR' : '$'));
  };
  toggle.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => set(b.dataset.cur!)));
  set('USD');
}

// ---------- scroll progress bar + mobile sticky CTA ----------
const bar = document.getElementById('scrollbar');
const sticky = document.getElementById('sticky-cta');
const footer = document.querySelector('footer');
let ticking = false;
const onScroll = () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    const max = document.documentElement.scrollHeight - innerHeight;
    if (bar) bar.style.transform = `scaleX(${max > 0 ? Math.min(1, scrollY / max) : 0})`;
    if (sticky) {
      const nearEnd = footer ? footer.getBoundingClientRect().top < innerHeight - 40 : false;
      sticky.classList.toggle('show', scrollY > 520 && !nearEnd);
    }
    ticking = false;
  });
};
addEventListener('scroll', onScroll, { passive: true });
onScroll();

// ---------- pointer parallax: hero chips drift with the mouse (depth = pixels) ----------
const chips = Array.from(document.querySelectorAll<HTMLElement>('.chip[data-depth]'));
if (chips.length && !reduced && matchMedia('(hover: hover)').matches) {
  const t = { x: 0, y: 0 }, c = { x: 0, y: 0 };
  addEventListener('pointermove', (e) => { t.x = (e.clientX / innerWidth - 0.5) * 2; t.y = (e.clientY / innerHeight - 0.5) * 2; }, { passive: true });
  const loop = () => {
    c.x += (t.x - c.x) * 0.07; c.y += (t.y - c.y) * 0.07;
    for (const el of chips) {
      const d = Number(el.dataset.depth);
      el.style.setProperty('--px', (c.x * d).toFixed(2));
      el.style.setProperty('--py', (c.y * d * 0.8).toFixed(2));
    }
    requestAnimationFrame(loop);
  };
  loop();
}

if (!reduced) {
  // ---------- smooth scroll ----------
  // Only for mouse-driven desktops. Phones/tablets (and the browser's mobile emulation) keep the browser's own scrolling,
  // which is what makes touch feel right; fighting it with a JS scroller caused the stops and jumps.
  const smooth = matchMedia('(hover: hover) and (pointer: fine)').matches && innerWidth > 900;
  if (smooth) {
    const lenis = new Lenis({ duration: 1.0, easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)) });
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((t) => lenis.raf(t * 1000));
    gsap.ticker.lagSmoothing(0);
    document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]').forEach((a) =>
      a.addEventListener('click', (e) => {
        const id = a.getAttribute('href');
        const el = id && id.length > 1 ? document.querySelector(id) : null;
        if (el) { e.preventDefault(); lenis.scrollTo(el as HTMLElement, { offset: -80 }); }
      }),
    );
    // Only text boxes keep native scrolling. The horizontal feature track must NOT opt out: Lenis ignores sideways wheel
    // gestures by itself, while an opt-out made vertical wheel scrolling jump between two scrollers.
    document.querySelectorAll('textarea').forEach((el) => el.setAttribute('data-lenis-prevent', ''));
  }

  // ---------- hero headline ----------
  const spans = document.querySelectorAll('.hero h1 .line > span');
  if (spans.length) gsap.from(spans, { yPercent: 115, duration: 1.1, ease: 'expo.out', stagger: 0.11, delay: 0.05 });
  gsap.from('.hero [data-hero-in]', { opacity: 0, y: 20, duration: 0.9, ease: 'power3.out', stagger: 0.09, delay: 0.35 });
  if (innerWidth > 900) gsap.from('.hero .chip', { opacity: 0, scale: 0.9, duration: 0.9, ease: 'power3.out', stagger: 0.12, delay: 0.9 }); // phones fade them in with plain CSS

  // ---------- scroll reveals ----------
  revealOnScroll();
}

fitPhones();
initPages();
initHome(reduced);

// ---------- hero 3D: loaded lazily, never blocks first paint ----------
const stage = document.getElementById('hero-stage');
if (stage) {
  const start = () => import('./hero3d').then((m) => m.initHero(stage)).catch(() => { /* HTML fallback stays visible */ });
  const lowPower = (navigator.hardwareConcurrency || 4) <= 2 || ((navigator as any).deviceMemory || 4) <= 2;
  if (!reduced && !lowPower) {
    if ('requestIdleCallback' in window) (window as any).requestIdleCallback(start, { timeout: 1200 });
    else setTimeout(start, 300);
  }
}

function revealOnScroll() {
  const els = Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]'));
  const io = new IntersectionObserver((entries) => {
    const vis = entries.filter((e) => e.isIntersecting);
    vis.forEach((e, i) => { (e.target as HTMLElement).style.transitionDelay = `${Math.min(i * 70, 320)}ms`; e.target.classList.add('in'); io.unobserve(e.target); });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
  els.forEach((el) => io.observe(el));
}

/** Scale the 393x852 HTML screen to whatever width the iPhone mockup currently has. */
function fitPhones() {
  const phones = Array.from(document.querySelectorAll<HTMLElement>('.iphone'));
  const fit = () => phones.forEach((p) => p.style.setProperty('--k', String((p.clientWidth * 0.8937) / 393)));
  fit();
  if ('ResizeObserver' in window) phones.forEach((p) => new ResizeObserver(fit).observe(p));
  else addEventListener('resize', fit);
}
