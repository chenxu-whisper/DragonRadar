import { useCallback, useEffect, useState } from 'react';
import { parseState, stateSearch, type ResearchState } from '../state';

export function useLocation() {
  const [location, setLocation] = useState(() => parseState(window.location.search));
  useEffect(() => {
    const sync = () => setLocation(parseState(window.location.search));
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  const patch = useCallback((change: Partial<ResearchState>, replace = false) => {
    const state = { ...parseState(window.location.search).state, ...change };
    const search = stateSearch(state);
    if (search !== window.location.search) {
      window.history[replace ? 'replaceState' : 'pushState']({}, '', search);
      setLocation({ state, notices: [] });
    }
  }, []);
  return { ...location, patch };
}
