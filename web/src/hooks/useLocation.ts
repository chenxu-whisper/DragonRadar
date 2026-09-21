import { useCallback, useEffect, useRef, useState } from 'react';
import { parseState, stateSearch, type ResearchState, type View } from '../state';
import { allowNavigation } from '../features/review/navigation';

export function useLocation(allowPrivate = false, reset = false) {
  const read = useCallback(() => parseState(window.location.search, allowPrivate, reset, window.location.pathname), [allowPrivate, reset]);
  const urlFor = useCallback((state: ResearchState) => {
    const path = reset ? state.view === 'stock' && state.security ? `/stock/${encodeURIComponent(state.security)}` : '/' : '';
    return `${path}${stateSearch(state, allowPrivate, reset)}${window.location.hash}`;
  }, [allowPrivate, reset]);
  const [location, setLocation] = useState(read);
  const pages = useRef<Partial<Record<View, ResearchState>>>({});
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
      accepted.current = window.location.href;
      setLocation(read());
    };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, [read, urlFor]);
  const patch = useCallback((change: Partial<ResearchState>, replace = false) => {
    const current = read().state;
    const state = { ...current, ...change };
    const url = urlFor(state);
    if (new URL(url, window.location.href).href !== window.location.href) {
      if (!replace && !allowNavigation()) return false;
      if (reset && current.view !== 'stock') pages.current[current.view] = current;
      window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
      accepted.current = window.location.href;
      setLocation({ state, notices: [] });
    }
    return true;
  }, [read, urlFor, reset]);
  const destination = (view: View): ResearchState => ({
    ...(pages.current[view] || parseState('', allowPrivate, reset).state),
    view, snapshot: location.state.snapshot, date: location.state.date, security: '',
  });
  return { ...location, patch, destination, urlFor };
}
