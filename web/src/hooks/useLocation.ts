import { useCallback, useEffect, useRef, useState } from 'react';
import { parseState, stateSearch, type ResearchState } from '../state';
import { allowNavigation } from '../features/review/navigation';

export function useLocation(allowPrivate = false) {
  const [location, setLocation] = useState(() => parseState(window.location.search, allowPrivate));
  const accepted = useRef(window.location.href);
  useEffect(() => {
    const initial = parseState(window.location.search, allowPrivate);
    const canonicalSearch = stateSearch(initial.state, allowPrivate);
    if (canonicalSearch !== window.location.search) {
      window.history.replaceState({}, '', `${canonicalSearch}${window.location.hash}`);
    }
    accepted.current = window.location.href;
    const sync = () => {
      if (!allowNavigation()) { window.history.pushState({}, '', accepted.current); return; }
      accepted.current = window.location.href;
      setLocation(parseState(window.location.search, allowPrivate));
    };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, [allowPrivate]);
  const patch = useCallback((change: Partial<ResearchState>, replace = false) => {
    const state = { ...parseState(window.location.search, allowPrivate).state, ...change };
    const search = stateSearch(state, allowPrivate);
    if (search !== window.location.search) {
      if (!replace && !allowNavigation()) return false;
      window.history[replace ? 'replaceState' : 'pushState']({}, '', `${search}${window.location.hash}`);
      accepted.current = window.location.href;
      setLocation({ state, notices: [] });
    }
    return true;
  }, [allowPrivate]);
  return { ...location, patch };
}
