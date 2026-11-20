import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { parseState, stateSearch, type ResearchState, type View } from '../state';
import { allowNavigation } from '../features/review/navigation';

export function useLocation(allowPrivate = false, reset = false, allowResetReview = true) {
  const read = useCallback(() => parseState(window.location.search, allowPrivate, reset, window.location.pathname, allowResetReview), [allowPrivate, reset, allowResetReview]);
  const urlFor = useCallback((state: ResearchState) => {
    const path = reset ? state.view === 'stock' && state.security ? `/stock/${encodeURIComponent(state.security)}` : '/' : '';
    return `${path}${stateSearch(state, allowPrivate, reset, allowResetReview)}${window.location.hash}`;
  }, [allowPrivate, reset, allowResetReview]);
  const [location, setLocation] = useState(read);
  const pages = useRef<Partial<Record<View, ResearchState>>>({});
  const scrolls = useRef(new Map<string, number>());
  const pendingScroll = useRef<number | undefined>(undefined);
  const scrollKey = useCallback((state: ResearchState) => {
    const url = new URL(urlFor(state), window.location.href);
    for (const name of [...url.searchParams.keys()]) if (name.startsWith('return_')) url.searchParams.delete(name);
    return `${url.pathname}${url.search}`;
  }, [urlFor]);
  useLayoutEffect(() => {
    const target = pendingScroll.current;
    if (target === undefined) return;
    const stop = () => {
      pendingScroll.current = undefined;
      observer.disconnect();
    };
    const restore = () => {
      if (document.querySelector('dialog[open]')) return;
      const loading = document.querySelector('.resource-skeleton')
        || [...document.querySelectorAll('[role="status"]')].some(element => /正在加载/.test(element.textContent || ''));
      const available = document.documentElement.scrollHeight - window.innerHeight;
      if (target > available && !loading) {
        stop();
        return;
      }
      window.scrollTo(0, target);
      if (!loading && Math.abs(window.scrollY - target) < 2) stop();
    };
    // Lazy views may mount before their archived rows provide the saved height.
    const observer = new MutationObserver(restore);
    observer.observe(document.body, { childList: true, subtree: true });
    const frame = requestAnimationFrame(restore);
    window.addEventListener('pointerdown', stop, true);
    window.addEventListener('wheel', stop, { passive: true });
    window.addEventListener('keydown', stop, true);
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame);
      window.removeEventListener('pointerdown', stop, true);
      window.removeEventListener('wheel', stop);
      window.removeEventListener('keydown', stop, true);
    };
  }, [location.state]);
  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = 'manual';
    const record = () => {
      if (pendingScroll.current === undefined) scrolls.current.set(scrollKey(read().state), window.scrollY);
    };
    window.addEventListener('scroll', record, { passive: true });
    return () => {
      window.removeEventListener('scroll', record);
      window.history.scrollRestoration = previous;
    };
  }, [read, scrollKey]);
  useEffect(() => {
    if (reset && location.state.view !== 'stock') pages.current[location.state.view] = location.state;
  }, [location.state, reset]);
  const accepted = useRef(window.location.href);
  useEffect(() => {
    const initial = read();
    const canonical = urlFor(initial.state);
    if (new URL(canonical, window.location.href).href !== window.location.href) {
      window.history.replaceState({}, '', canonical);
    }
    accepted.current = window.location.href;
    const sync = () => {
      if (!allowNavigation()) { window.history.pushState({}, '', accepted.current); return; }
      const next = read();
      pendingScroll.current = scrolls.current.get(scrollKey(next.state)) ?? 0;
      accepted.current = window.location.href;
      setLocation(next);
    };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, [read, urlFor, scrollKey]);
  const patch = useCallback((change: Partial<ResearchState>, replace = false) => {
    const current = read().state;
    const state = { ...current, ...change };
    const url = urlFor(state);
    if (new URL(url, window.location.href).href !== window.location.href) {
      if (!replace && !allowNavigation()) return false;
      if (reset && current.view !== 'stock') pages.current[current.view] = current;
      scrolls.current.set(scrollKey(current), window.scrollY);
      pendingScroll.current = scrolls.current.get(scrollKey(state))
        ?? (current.view === state.view ? window.scrollY : 0);
      window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
      accepted.current = window.location.href;
      setLocation({ state, notices: [] });
    }
    return true;
  }, [read, urlFor, reset, scrollKey]);
  const destination = (view: View): ResearchState => ({
    ...(pages.current[view] || parseState('', allowPrivate, reset, '', allowResetReview).state),
    view, snapshot: location.state.snapshot, date: location.state.date, security: '',
  });
  return { ...location, patch, destination, urlFor };
}
