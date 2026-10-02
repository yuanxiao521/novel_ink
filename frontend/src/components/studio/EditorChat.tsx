import { useRef, useState } from 'react';
import { editorChat as callEditorChat } from '../../api/novel';
import type { AgentExecuted, PerceptSummary } from '../../api/novel';

/* ==========================================================================
   EditorChat —— 责编（场景级副驾）对话面板

   与按钮走**同一条工具链**（后端 ToolExecutor）：显示计划了什么、执行结果、
   以及被拒原因（破坏性操作需作者确认 → 不静默失败）。
   ========================================================================== */

interface Msg {
  role: 'user' | 'editor';
  text: string;
  executed?: AgentExecuted[];
  percept?: PerceptSummary;
  error?: string;
}

interface Props {
  sceneId: string;
  text: string;
  onApplyText: (t: string) => void;
  onRefresh: () => void;
  seedMessage?: string;
  onSeedConsumed?: () => void;
}

const QUICK = ['看看这段有没有 AI 味', '口吻像不像上场角色', '有没有偏离本场目标', '帮我处理待办批注'];

export function EditorChat({ sceneId, text, onApplyText, onRefresh, seedMessage, onSeedConsumed }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 从正文区"问责编 / 重写这段"带过来的预填消息
  if (seedMessage && input === '' && !busy) {
    setInput(seedMessage);
    onSeedConsumed?.();
  }

  const send = async (raw?: string) => {
    const message = (raw ?? input).trim();
    if (!message || busy) return;
    setMsgs((m) => [...m, { role: 'user', text: message }]);
    setInput('');
    setBusy(true);
    try {
      const r = await callEditorChat(sceneId, message, text);
      setMsgs((m) => [...m, { role: 'editor', text: r.reply, executed: r.executed, percept: r.percept }]);
      if (r.executed.some((e) => e.ok)) onRefresh();
    } catch (e) {
      setMsgs((m) => [...m, { role: 'editor', text: '对话失败（后端不可达或超时）。', error: String(e) }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  return (
    <div className="ec-wrap">
      <div className="ec-head">
        <span className="ec-title">责编 · 本场</span>
        <span className="ec-sub">读：本场正文 · 角色卡 · 批注 · 审计</span>
      </div>

      <div className="ec-msgs">
        {msgs.length === 0 && (
          <div className="ec-hint">
            我是这一场的责编。你可以直接说"哪里不对"，我会调用和按钮**同一套工具**去做，并落审计。
          </div>
        )}
        {msgs.map((m, i) => (
          <div className={`ec-msg ${m.role}`} key={i}>
            <div className="ec-who">{m.role === 'user' ? '你' : '责编'}</div>
            <div className="ec-text">{m.text}</div>
            {m.percept && (
              <div className="ec-percept">
                看到：场景 {m.percept.counts.scene ?? 0} · 角色 {m.percept.counts.characters ?? 0} · 批注{' '}
                {m.percept.counts.annotations ?? 0} · 近期审计 {m.percept.counts.recent_notes ?? 0}
                {m.percept.dropped.length > 0 && ` · 裁剪 ${m.percept.dropped.length} 项`}
              </div>
            )}
            {m.executed && m.executed.length > 0 && (
              <div className="ec-exec">
                {m.executed.map((e, j) => (
                  <div className={`ec-chip ${e.ok ? 'ok' : 'denied'}`} key={j} title={e.error || e.why || ''}>
                    <span>{e.ok ? '✓' : '✕'} {e.tool}</span>
                    {!e.ok && <em>{e.error}</em>}
                    {e.ok && typeof e.data?.text === 'string' && (e.data.text as string).length > 0 && (
                      <button onClick={() => onApplyText(e.data?.text as string)}>采纳到正文</button>
                    )}
                  </div>
                ))}
              </div>
            )}
            {m.error && <div className="ec-err">{m.error}</div>}
          </div>
        ))}
        {busy && <div className="ec-msg editor"><div className="ec-text">（责编在想…）</div></div>}
      </div>

      <div className="ec-quick">
        {QUICK.map((q) => (
          <button key={q} className="ec-quick-btn" disabled={busy} onClick={() => void send(q)}>{q}</button>
        ))}
      </div>

      <div className="ec-input">
        <input
          ref={inputRef}
          value={input}
          placeholder="哪儿不对？说给我（我会调同一套工具）"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void send(); }}
        />
        <button className="ws-btn ws-btn-primary" disabled={busy || !input.trim()} onClick={() => void send()}>发送</button>
      </div>
    </div>
  );
}
