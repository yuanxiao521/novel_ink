import { useEffect, useRef, useState, useCallback, createContext, useContext } from 'react';
import { createPortal } from 'react-dom';

/* ==========================================================================
   Dialog —— 统一确认 / 输入弹窗（替代 window.confirm / window.prompt）
   纸墨双主题，Promise-based 调用
   ========================================================================== */

interface DialogState {
  kind: 'confirm' | 'prompt';
  title: string;
  message?: string;
  defaultValue?: string;
  danger?: boolean;
  resolve: (v: unknown) => void;
}

const DialogCtx = createContext<(s: DialogState) => void>(() => {});

/** 在根组件挂载一次，暴露 showConfirm / showPrompt。 */
export function DialogProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<DialogState | null>(null);
  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const open = useCallback((s: DialogState) => {
    setInput(s.defaultValue ?? '');
    setState(s);
  }, []);

  const close = useCallback((v: unknown) => {
    const fn = state?.resolve;
    setState(null);
    fn?.(v);
  }, [state]);

  useEffect(() => {
    if (state?.kind === 'prompt') {
      window.setTimeout(() => inputRef.current?.focus(), 60);
    }
  }, [state]);

  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(null);
      if (e.key === 'Enter' && state.kind === 'confirm') close('');
      if (e.key === 'Enter' && state.kind === 'prompt') close(input.trim() || null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state, input, close]);

  return (
    <DialogCtx.Provider value={open}>
      {children}
      {state &&
        createPortal(
          <div className="dialog-overlay" onClick={() => close(null)}>
            <div className={`dialog-box ${state.danger ? 'danger' : ''}`} onClick={(e) => e.stopPropagation()}>
              <div className="dialog-title">{state.title}</div>
              {state.message && <div className="dialog-message">{state.message}</div>}
              {state.kind === 'prompt' && (
                <input
                  ref={inputRef}
                  className="dialog-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={state.defaultValue}
                />
              )}
              <div className="dialog-actions">
                <button className="dialog-btn dialog-btn-cancel" onClick={() => close(null)}>取消</button>
                <button
                  className={`dialog-btn dialog-btn-ok ${state.danger ? 'dialog-btn-danger' : ''}`}
                  onClick={() => close(state.kind === 'prompt' ? (input.trim() || null) : '')}
                >
                  {state.danger ? '确认删除' : '确定'}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </DialogCtx.Provider>
  );
}

/** 在任意子组件中调用，返回 showConfirm / showPrompt。 */
export function useDialog() {
  const open = useContext(DialogCtx);
  return {
    showConfirm: (title: string, message?: string, danger?: boolean): Promise<boolean> =>
      new Promise<boolean>((resolve) => open({ kind: 'confirm', title, message, danger, resolve: (v) => resolve(v !== null) })),
    showPrompt: (title: string, defaultValue?: string): Promise<string | null> =>
      new Promise<string | null>((resolve) => open({ kind: 'prompt', title, defaultValue, resolve: (v) => resolve(v as string | null) })),
  };
}
