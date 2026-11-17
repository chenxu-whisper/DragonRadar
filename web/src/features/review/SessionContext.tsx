import { createContext, useContext } from 'react';
import type { SessionStore } from './session';
import type { Command } from './types';

export interface SessionContextValue {
  enabled: boolean;
  store: SessionStore;
  version: number;
  read: <T>(path: string, signal?: AbortSignal) => Promise<T>;
  send: <T>(command: Command) => Promise<T>;
  check: () => Promise<void>;
  logout: () => Promise<void>;
}

const inactiveStore = { epoch: 0, locked: false } as SessionStore;
const unavailable = async (): Promise<never> => { throw new Error('Private session is unavailable'); };

export const SessionContext = createContext<SessionContextValue>({
  enabled: false,
  store: inactiveStore,
  version: 0,
  read: unavailable,
  send: unavailable,
  check: unavailable,
  logout: unavailable,
});

export const useSession = () => useContext(SessionContext);
