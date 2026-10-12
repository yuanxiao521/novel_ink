import { useEffect, useRef, useState } from 'react';
import { editorChatStream, listAnnotations } from '../../api/novel';
import type { AgentExecuted, ProseAnnotation } from '../../api/novel';

/* ==========================================================================
   EditorChat —— 责编（场景级副驾）对话面板

   与按钮走**同一条工具链**（后端 ToolExecutor）。
   v1.39：走 SSE 流式——感知/计划/逐工具执行/完成，每个阶段渲染成过程卡，
   作者不再对着「（责编在想…）」黑等 10-30 秒。
   ========================================================================== */

interface Msg {
  role: 'user' | 'editor' | 'process';
  id?: string;
  text?: string;
  stages?: Stage[];
  done?: boolean;
  executed?: AgentExecuted[];
  error?: string;
}

/** 过程卡的阶段（v1.39） */
interface Stage {
  kind: 'percept' | 'plan' | 'tool_start' | 'tool_done';
  text: string;
  label?: string;      // 工具的中文显示名
  why?: string;
  ok?: boolean;
  done?: boolean;
  detail?: string;
  tools?: string[];
}

interface Props {
  sceneId: string;
  text: string;
  onApplyText: (t: string) => void;
  onRefresh: () => void;
  seedMessage?: string;
  onSeedConsumed?: () => void;
}

/** 工具名 → 中文显示名（与前端按钮文案对齐，未知工具原样显示）。 */
const TOOL_LABEL: Record<string, string> = {
  'prose.draft': '写手 · 初稿', 'prose.review': '体检员', 'prose.polish': '润色师',
  'prose.verify': '质检员', 'prose.scan_tone': 'AI 味扫描', 'prose.spot_fix': '定点修复',
  'prose.quality_loop': '质量回环', 'prose.save': '保存正文', 'prose.quality_score': '质量评分',
  'prose.review_meeting': '审稿会', 'character.voices': '角色在场感',
  'annotation.list': '读批注', 'annotation.resolve': '处理批注',
  'task.rehearsal': '请求彩排', 'task.list': '看任务', 'task.transition': '流转任务',
};

const QUICK = ['看看这段有没有 AI 味', '口吻像不像上场角色', '有没有偏离本场目标', '帮我处理待办批注'];

/** 有待处理批注时，把"处理批注"置顶并带上具体条数（副驾的待办驱动）。 */
function quickListOf(openCount: number): string[] {
  if (openCount <= 0) return QUICK;
  return [`帮我处理 ${openCount} 条待办批注（处理完标 handled）`, ...QUICK.slice(0, 3)];
}

export function EditorChat({ sceneId, text, onApplyText, onRefresh, seedMessage, onSeedConsumed }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [openAnns, setOpenAnns] = useState<ProseAnnotation[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const executedRef = useRef<AgentExecuted[]>([]);
  const finalRef = useRef('');

  // 本场待处理批注：副驾的"待办"——有批注时快捷指令一键直达
  useEffect(() => {
    if (!sceneId) { setOpenAnns([]); return; }
    listAnnotations(sceneId, 'open')
      .then(setOpenAnns)
      .catch(() => setOpenAnns([]));
  }, [sceneId, msgs.length]);

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
    executedRef.current = [];
    finalRef.current = '';

    // 过程卡：责编的每个阶段都可见（v1.39）——感知/计划/逐工具/完成
    const procId = 'proc-' + Date.now();
    setMsgs((m) => [...m, { role: 'process', id: procId, stages: [] }]);
    const patch = (fn: (st: Stage[]) => Stage[]) =>
      setMsgs((m) => m.map((x) => (x.id === procId ? { ...x, stages: fn(x.stages ?? []) } : x)));

    try {
      await editorChatStream(sceneId, message, text, (s) => {
        if (s.event === 'percept') {
          const c = (s.data as { counts?: Record<string, number> }).counts ?? {};
          patch((st) => [...st, { kind: 'percept', text: `读到：场景 ${c.scene ?? 0} · 角色 ${c.characters ?? 0} · 批注 ${c.annotations ?? 0} · 近期审计 ${c.recent_notes ?? 0}` }]);
        } else if (s.event === 'plan') {
          const d = s.data as { reply: string; actions: Array<{ tool: string; why?: string }> };
          patch((st) => [...st, { kind: 'plan', text: d.reply || '（无说明）', tools: (d.actions || []).map((a) => a.tool) }]);
        } else if (s.event === 'tool_start') {
          const d = s.data as { tool: string; why?: string };
          patch((st) => [...st, { kind: 'tool_start', text: d.tool, label: TOOL_LABEL[d.tool] ?? d.tool, why: d.why }]);
        } else if (s.event === 'tool_done') {
          const d = s.data as { tool: string; ok: boolean; summary?: string; error?: string; result?: AgentExecuted };
          if (d.ok && d.result) executedRef.current = [...executedRef.current, d.result];
          patch((st) => st.map((x) => (x.kind === 'tool_start' && x.text === d.tool && !x.done
            ? { ...x, kind: 'tool_done', ok: d.ok, done: true, detail: d.ok ? (d.summary || '') : (d.error || '被拒') }
            : x)));
        } else if (s.event === 'reply') {
          finalRef.current = (s.data as { reply: string }).reply;
        } else if (s.event === 'error') {
          finalRef.current = '（过程出错：' + (s.data as { error: string }).error + '）';
        }
      });
      setMsgs((m) => [
        ...m.map((x) => (x.id === procId ? { ...x, done: true } : x)),
        { role: 'editor', text: finalRef.current || '（完成）', executed: executedRef.current },
      ]);
      onRefresh();
    } catch (e) {
      setMsgs((m) => [...m.map((x) => (x.id === procId ? { ...x, done: true } : x)),
        { role: 'editor', text: '对话失败（后端不可达或超时）。', error: String(e) }]);
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
            {m.role === 'user' && <div className="ec-who">你</div>}
            {m.role === 'user' && <div className="ec-text">{m.text}</div>}

            {/* 过程卡：感知 / 计划 / 每个工具的执行状态 */}
            {m.role === 'process' && (
              <div className="ec-proc">
                <div className="ec-proc-title">{m.done ? '✓ 处理完成' : '⚙ 处理中…'}</div>
                {(m.stages ?? []).map((st, j) => (
                  <div className={`ec-stage ${st.kind}`} key={j}>
                    <span className="ec-stage-ico">
                      {st.kind === 'percept' ? '☰' : st.kind === 'plan' ? '✎' : st.done ? (st.ok ? '✓' : '✕') : '⏳'}
                    </span>
                    <span className="ec-stage-text">
                      {st.kind === 'percept' && st.text}
                      {st.kind === 'plan' && <>{st.text}{st.tools && st.tools.length > 0 && <em>（{(st.tools ?? []).map((t) => TOOL_LABEL[t] ?? t).join(' → ')}）</em>}</>}
                      {(st.kind === 'tool_start' || st.kind === 'tool_done') && (
                        <>{st.label ?? st.text}{st.why ? <em title={st.why}> · {st.why}</em> : null}{st.done && st.detail ? <em> · {st.detail}</em> : null}</>
                      )}
                    </span>
                  </div>
                ))}
                {!m.done && <div className="ec-stage running"><span className="ec-stage-ico">⏳</span><span className="ec-stage-text">进行中…</span></div>}
              </div>
            )}

            {m.role === 'editor' && <div className="ec-who">责编</div>}
            {m.role === 'editor' && <div className="ec-text">{m.text}</div>}
            {m.executed && m.executed.length > 0 && (
              <div className="ec-exec">
                {m.executed.map((e, j) => (
                  <div className={`ec-chip ${e.ok ? 'ok' : 'denied'}`} key={j} title={e.error || e.why || ''}>
                    <span>{e.ok ? '✓' : '✕'} {TOOL_LABEL[e.tool] ?? e.tool}</span>
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
      </div>

      <div className="ec-quick">
        {quickListOf(openAnns.length).map((q) => (
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
