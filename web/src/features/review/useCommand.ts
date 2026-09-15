import { useRef, useState } from 'react';
import { useSession } from './SessionProvider';
import { PrivateError } from './api';
import type { Command } from './types';
import { captureNavigationContext, isNavigationContextCurrent } from './navigation';

interface CommandContext {
  slot: string;
  owner?: string;
  epoch: number;
}

interface CommandState {
  context: CommandContext;
  busy: boolean;
  error: string;
  conflict?: unknown;
}

const idleCommandState = (context: CommandContext): CommandState => ({
  context, busy: false, error: '', conflict: undefined,
});

export function sameCommandContext(left: CommandContext, right: CommandContext) {
  return left.slot === right.slot && left.owner === right.owner && left.epoch === right.epoch;
}

export function useCommand<T>(slot: string) {
  const { store, send } = useSession();
  const renderedContext: CommandContext = { slot, owner: store.owner, epoch: store.epoch };
  const context = useRef(renderedContext);
  if (!sameCommandContext(context.current, renderedContext)) context.current = renderedContext;
  const [storedState, setCommandState] = useState<CommandState>(() => idleCommandState(context.current));
  const state = storedState.context === context.current ? storedState : idleCommandState(context.current);
  const pending = store.commands.get(slot);
  const feedbackContext = context.current;
  function clearFeedback() {
    setCommandState(current => current.context === feedbackContext
      ? { ...current, error: '', conflict: undefined }
      : current);
  }
  async function execute(create: () => Command, accept: (data: T) => void, navigate?: (data: T) => void) {
    if (state.busy) return;
    const cmd = store.commands.get(slot) || create();
    const commandContext = context.current;
    const navigationContext = captureNavigationContext();
    store.commands.set(slot, cmd); store.changed();
    setCommandState({ context: commandContext, busy: true, error: '', conflict: undefined });
    try {
      const data = await send<T>(cmd);
      if (store.commands.get(commandContext.slot) === cmd) {
        store.commands.delete(commandContext.slot);
        store.changed();
      }
      if (context.current !== commandContext) return;
      accept(data);
      if (navigate && isNavigationContextCurrent(navigationContext)) navigate(data);
    } catch (failure) {
      let conflict: unknown;
      if (failure instanceof PrivateError && !failure.uncertain && failure.status !== 401) {
        if (store.commands.get(commandContext.slot) === cmd) {
          store.commands.delete(commandContext.slot);
          store.changed();
        }
        if (failure.code === 'version_conflict') conflict = failure.current;
      }
      if (context.current !== commandContext) return;
      const error = store.locked ? '' : failure instanceof Error ? failure.message : '结果未知，请重试原命令。';
      setCommandState(current => current.context === commandContext ? { ...current, error, conflict } : current);
      store.changed();
    } finally {
      if (context.current === commandContext) {
        setCommandState(current => current.context === commandContext ? { ...current, busy: false } : current);
      }
    }
  }
  return { execute, clearFeedback, busy: state.busy, pending, error: state.error, conflict: state.conflict };
}
