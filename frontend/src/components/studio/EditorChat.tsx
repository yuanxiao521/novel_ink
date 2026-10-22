import { useEffect, useRef, useState } from 'react';
import { chapterEditorChatStream, chapterToolRunStream, editorChatStream, enqueueBookJob,
  listAnnotations, pollJob, toolRunStream } from '../../api/novel';
import type { AgentExecuted, ProseAnnotation, ToolRunStage } from '../../api/novel';
import { useDialog } from '../common/Dialog';
import { useWorkspace } from '../../context/WorkspaceContext';
import { ToolResult } from './ToolResult';

/* ==========================================================================
   EditorChat —— 责编（场景级副驾）对话面板

   与按钮走**同一条工具链**（后端 ToolExecutor）。
   v1.39：走 SSE 流式——感知/计划/逐工具执行/完成，每个阶段渲染成过程卡。
   v1.43：**按钮位置不动，过程与明细进责编流**。作者反馈"点了体检/质检没有输出，也不知道
          怎么体检的"——根因是按钮走同步 POST、结果只落右侧「质检」tab，作者停在「责编」时
          什么都看不到。现在：① 顶栏按钮照旧（审稿会 / 精修工具▾），点了**自动切到责编 tab**；
          ② 按钮走 /agent/run/stream，与对话共用同一张过程卡（含耗时心跳）；
          ③ 结果用 ToolResult 富卡摊开明细（问题清单/账本对照/五维分/裁决）。
          注意：**不是**把按钮搬进右栏——右栏只负责"看得见"。
   ========================================================================== */

interface Msg {
  role: 'user' | 'editor' | 'process';
  id?: string;
  text?: string;
  stages?: Stage[];
  done?: boolean;
  executed?: AgentExecuted[];
  error?: string;
  /** 过程卡产出的富结果（按钮路径与对话路径都会填） */
  result?: { tool: string; data: Record<string, unknown> };
  /** 过程卡标题（按钮路径 = 工具中文名） */
  title?: string;
}

/** 过程卡的阶段（v1.39；v1.43 增 stage/tick） */
interface Stage {
  kind: 'percept' | 'plan' | 'tool_start' | 'tool_done' | 'stage' | 'tick';
  text: string;
  label?: string;      // 工具的中文显示名
  why?: string;
  ok?: boolean;
  done?: boolean;
  detail?: string;
  tools?: string[];
}

interface Props {
  /** 场级（拆了场的精修路径）——与 chapterId 二选一 */
  sceneId?: string;
  /**
   * 章级（v1.65 · 两个页面合并后主线）：传它就按整章工作，不传才是场级。
   * 责编同一套对话/过程卡，只是作用域不同（后端感知包与工具都按章装配）。
   */
  chapterId?: string;
  text: string;
  onApplyText: (t: string) => void;
  onRefresh: () => void;
  seedMessage?: string;
  onSeedConsumed?: () => void;
  /** 顶栏按钮派活（v1.43）：按钮在顶栏，过程与结果长在这个流里。 */
  pendingTool?: string;
  onPendingConsumed?: () => void;
  /** 把"正在跑哪个工具"报给顶栏，让顶栏按钮也能显示进度/禁用（v1.43）。 */
  onBusyChange?: (v: string) => void;
}

/** 工具名 → 中文显示名（与前端按钮文案对齐，未知工具原样显示）。 */
const TOOL_LABEL: Record<string, string> = {
  'prose.draft': '写手 · 初稿', 'prose.review': '体检员', 'prose.polish': '润色师',
  'prose.verify': '质检员', 'prose.scan_tone': 'AI 味扫描', 'prose.spot_fix': '定点修复',
  'prose.quality_loop': '质量回环', 'prose.save': '保存正文', 'prose.quality_score': '质量评分',
  'prose.review_meeting': '审稿会', 'character.voices': '角色在场感',
  'annotation.list': '读批注', 'annotation.resolve': '处理批注',
  'task.rehearsal': '请求彩排', 'task.list': '看任务', 'task.transition': '流转任务',
  // 章级（v1.65）
  'chapter.review': '体检整章', 'chapter.verify': '质检整章', 'chapter.polish': '润色整章',
  'chapter.scan_tone': 'AI 味扫描（整章）', 'chapter.quality_score': '评审整章',
  'chapter.inspect': '章后巡检', 'chapter.context': '看本章上下文', 'chapter.merge_scenes': '并入本章',
};

/** 后台队列类工具（长任务，刷新不断链）：走 /jobs 而不是 SSE。 */
const QUEUED: Record<string, 'review_meeting' | 'quality_loop'> = {
  'prose.review_meeting': 'review_meeting',
  'prose.quality_loop': 'quality_loop',
};

const QUICK = ['看看这段有没有 AI 味', '口吻像不像上场角色', '有没有偏离本场目标', '帮我处理待办批注'];

/** 有待处理批注时，把"处理批注"置顶并带上具体条数（副驾的待办驱动）。 */
function quickListOf(openCount: number): string[] {
  if (openCount <= 0) return QUICK;
  return [`帮我处理 ${openCount} 条待办批注（处理完标 handled）`, ...QUICK.slice(0, 3)];
}

const numOf = (v: unknown): number | null => (typeof v === 'number' ? v : null);

/** 队列类结果的一句话摘要（明细由 ToolResult 摊开）。 */
function queuedSummary(tool: string, d: Record<string, unknown>): string {
  if (tool === 'prose.review_meeting') return String(d.verdict ?? '完成');
  const before = numOf((d.before as Record<string, unknown> | undefined)?.total);
  const after = numOf((d.after_score as Record<string, unknown> | undefined)?.total);
  if (d.accepted) return `质量回环：${before ?? '—'} → ${after ?? '—'} 分`;
  return '质量回环：未采纳';
}

export function EditorChat({ sceneId = '', chapterId = '', text, onApplyText, onRefresh, seedMessage,
                             onSeedConsumed, pendingTool, onPendingConsumed, onBusyChange }: Props) {
  const { bookId } = useWorkspace();
  const { showConfirm } = useDialog();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState('');

  /** busy 同时报给顶栏：按钮能显示"体检中…"并禁用（v1.43）。 */
  const markBusy = (v: string) => { setBusy(v); onBusyChange?.(v); };
  const [openAnns, setOpenAnns] = useState<ProseAnnotation[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const executedRef = useRef<AgentExecuted[]>([]);
  const finalRef = useRef('');

  // 本场待处理批注：副驾的"待办"——有批注时快捷指令一键直达
  useEffect(() => {
    if (!sceneId || chapterId) { setOpenAnns([]); return; }   // 章级不聚合场批注（左栏场列表里有条数）
    listAnnotations(sceneId, 'open')
      .then(setOpenAnns)
      .catch(() => setOpenAnns([]));
  }, [sceneId, msgs.length]);

  // 过程卡边跑边长：始终把最新一行滚进视野
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  // 从正文区"问责编 / 重写这段"带过来的预填消息
  if (seedMessage && input === '' && !busy) {
    setInput(seedMessage);
    onSeedConsumed?.();
  }

  const patchStages = (procId: string, fn: (st: Stage[]) => Stage[]) =>
    setMsgs((m) => m.map((x) => (x.id === procId ? { ...x, stages: fn(x.stages ?? []) } : x)));

  const pushResult = (procId: string, tool: string, data: Record<string, unknown>) =>
    setMsgs((m) => m.map((x) => (x.id === procId ? { ...x, result: { tool, data } } : x)));

  const finishProc = (procId: string) =>
    setMsgs((m) => m.map((x) => (x.id === procId ? { ...x, done: true } : x)));

  /** 耗时心跳：只保留最后一行，不刷屏。 */
  const upsertTick = (procId: string, elapsed: number) =>
    patchStages(procId, (st) => {
      const rest = st.filter((x) => x.kind !== 'tick');
      return [...rest, { kind: 'tick', text: `已跑 ${elapsed.toFixed(1)}s…` }];
    });

  const send = async (raw?: string) => {
    const message = (raw ?? input).trim();
    if (!message || busy) return;
    setMsgs((m) => [...m, { role: 'user', text: message }]);
    setInput('');
    markBusy('chat');
    executedRef.current = [];
    finalRef.current = '';

    // 过程卡：责编的每个阶段都可见（v1.39）——感知/计划/逐工具/完成
    const procId = 'proc-' + Date.now();
    setMsgs((m) => [...m, { role: 'process', id: procId, title: '责编处理中', stages: [] }]);
    const patch = (fn: (st: Stage[]) => Stage[]) => patchStages(procId, fn);

    try {
      const runChat = chapterId
      ? (m: string, tx: string, cb: Parameters<typeof editorChatStream>[3]) =>
          chapterEditorChatStream(chapterId, m, tx, cb)
      : (m: string, tx: string, cb: Parameters<typeof editorChatStream>[3]) =>
          editorChatStream(sceneId, m, tx, cb);
    await runChat(message, text, (s) => {
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
      markBusy('');
      inputRef.current?.focus();
    }
  };

  /* ---------- 按钮直调工具（v1.43）：过程与明细都进责编流 ---------- */

  /** 同步工具：走 /agent/run/stream，逐阶段 + 耗时心跳。 */
  const runStreamTool = async (procId: string, tool: string, confirm = false): Promise<string> => {
    let denied = '';
    let streamErr = '';
    const runTool = chapterId
      ? (tl: string, args: Record<string, unknown>, cb: (s: ToolRunStage) => void, cf = false) =>
          chapterToolRunStream(chapterId, tl, args, cb, cf)
      : (tl: string, args: Record<string, unknown>, cb: (s: ToolRunStage) => void, cf = false) =>
          toolRunStream(sceneId, tl, args, cb, cf);
    await runTool(tool, { text }, (s: ToolRunStage) => {
      if (s.event === 'tool_start') {
        patchStages(procId, (st) => [...st, { kind: 'tool_start', text: s.data.tool, label: TOOL_LABEL[s.data.tool] ?? s.data.tool, why: s.data.why }]);
      } else if (s.event === 'tick') {
        upsertTick(procId, s.data.elapsed);
      } else if (s.event === 'tool_done') {
        const d = s.data;
        if (!d.ok && d.denied) denied = d.error ?? '需要确认';
        patchStages(procId, (st) => st.map((x) => (x.kind === 'tool_start' && x.text === d.tool && !x.done
          ? { ...x, kind: 'tool_done', ok: d.ok, done: true,
              detail: d.ok ? `${d.summary ?? ''}${d.elapsed != null ? `（${d.elapsed}s）` : ''}` : (d.error || '被拒') }
          : x)));
        if (d.ok && d.result?.data) pushResult(procId, d.tool, d.result.data);
      } else if (s.event === 'error') {
        streamErr = s.data.error;
      }
    }, confirm);
    if (streamErr) throw new Error(streamErr);
    return denied;
  };

  /** 队列工具：入队 → 轮询阶段写进过程卡（刷新/断网不断链）。 */
  const runQueuedTool = async (procId: string, tool: string) => {
    if (!bookId) throw new Error('缺少 book_id（请从工作台进入）');
    const kind = QUEUED[tool];
    const { job_id } = await enqueueBookJob(bookId, kind, { text, scene_id: sceneId });
    let seen = 0;
    const st = await pollJob(job_id, (s) => {
      const stages = s.stages ?? [];
      if (stages.length > seen) {
        const fresh = stages.slice(seen);
        seen = stages.length;
        patchStages(procId, (list) => [...list, ...fresh.map((x) => ({ kind: 'stage' as const, text: x.text + (x.detail ? ` · ${x.detail}` : '') }))]);
      }
      const started = s.created_at ?? Date.now();
      if (s.status === 'running') upsertTick(procId, (Date.now() - started) / 1000);
    });
    if (st.status === 'failed') throw new Error(st.error || '任务失败');
    const result = (st.result ?? {}) as Record<string, unknown>;
    pushResult(procId, tool, result);
    patchStages(procId, (list) => [...list.filter((x) => x.kind !== 'tick'),
      { kind: 'tool_done', text: tool, label: TOOL_LABEL[tool] ?? tool, ok: true, done: true,
        detail: queuedSummary(tool, result) }]);
  };

  const runTool = async (tool: string) => {
    if ((!sceneId && !chapterId) || busy) return;
    const procId = 'proc-' + Date.now();
    markBusy(tool);
    setMsgs((m) => [...m, { role: 'process', id: procId, title: TOOL_LABEL[tool] ?? tool, stages: [] }]);
    try {
      if (QUEUED[tool]) {
        await runQueuedTool(procId, tool);
      } else {
        const denied = await runStreamTool(procId, tool);
        if (denied) {
          // 破坏性/需确认：不静默，弹确认后带 confirm 重试（与顶栏按钮同一套语义）
          const ok = await showConfirm('确认执行', `${TOOL_LABEL[tool] ?? tool}：${denied}`, false);
          if (ok) await runStreamTool(procId, tool, true);
          else patchStages(procId, (st) => [...st, { kind: 'tool_done', text: tool, ok: false, done: true, detail: '已取消' }]);
        }
      }
      onRefresh();
    } catch (e) {
      patchStages(procId, (st) => [...st.filter((x) => x.kind !== 'tick'),
        { kind: 'tool_done', text: tool, ok: false, done: true, detail: String(e instanceof Error ? e.message : e) }]);
    } finally {
      finishProc(procId);
      markBusy('');
    }
  };

  // 顶栏按钮派活（v1.43）：切到责编 tab 后由这里接住并跑起来（tab 切换会让本组件重新挂载）
  useEffect(() => {
    if (!pendingTool || busy) return;
    onPendingConsumed?.();
    void runTool(pendingTool);
    // runTool 每次渲染都是新函数，故意不放进依赖：只在 pendingTool 变化时触发一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTool, busy]);

  return (
    <div className="ec-wrap">
      <div className="ec-head">
        <span className="ec-title">{chapterId ? '责编 · 本章' : '责编 · 本场'}</span>
        <span className="ec-sub">{chapterId ? '读：整章正文 · 场清单 · 前情 · 记忆 · 角色档位' : '读：本场正文 · 角色卡 · 批注 · 审计'}</span>
      </div>
      {!text.trim() && (
        <div className="ec-empty-hint">
          正文为空：先点「✍ 写这一章」让它出稿，或切「✎ 编辑」手写一段 —— 体检/质检/审稿会都要读正文。
        </div>
      )}

      <div className="ec-msgs" ref={scrollRef}>
        {msgs.length === 0 && (
          <div className="ec-hint">
            我是这一场的责编。你可以直接说"哪里不对"，也可以用下面的工具条点「体检 / 质检 / 审稿会」——
            两条路走**同一套工具**，过程和明细都会显示在这里，并落审计。
          </div>
        )}
        {msgs.map((m, i) => (
          <div className={`ec-msg ${m.role}`} key={i}>
            {m.role === 'user' && <div className="ec-who">你</div>}
            {m.role === 'user' && <div className="ec-text">{m.text}</div>}

            {/* 过程卡：感知 / 计划 / 每个工具的执行状态 */}
            {m.role === 'process' && (
              <div className="ec-proc">
                <div className="ec-proc-title">
                  {m.done ? '✓ ' : '⚙ '}{m.title ?? '处理中'}{m.done ? ' 完成' : '…'}
                </div>
                {(m.stages ?? []).map((st, j) => (
                  <div className={`ec-stage ${st.kind}`} key={j}>
                    <span className="ec-stage-ico">
                      {st.kind === 'percept' ? '☰' : st.kind === 'plan' ? '✎'
                        : st.kind === 'tick' ? '⏳' : st.kind === 'stage' ? '•'
                        : st.done ? (st.ok ? '✓' : '✕') : '⏳'}
                    </span>
                    <span className="ec-stage-text">
                      {st.kind === 'percept' && st.text}
                      {st.kind === 'plan' && <>{st.text}{st.tools && st.tools.length > 0 && <em>（{(st.tools ?? []).map((t) => TOOL_LABEL[t] ?? t).join(' → ')}）</em>}</>}
                      {(st.kind === 'tool_start' || st.kind === 'tool_done') && (
                        <>{st.label ?? st.text}{st.why ? <em title={st.why}> · {st.why}</em> : null}{st.done && st.detail ? <em> · {st.detail}</em> : null}</>
                      )}
                      {(st.kind === 'stage' || st.kind === 'tick') && <em>{st.text}</em>}
                    </span>
                  </div>
                ))}
                {!m.done && (m.stages ?? []).every((s) => s.kind !== 'tick') && (
                  <div className="ec-stage running"><span className="ec-stage-ico">⏳</span><span className="ec-stage-text">进行中…</span></div>
                )}
                {m.result && (
                  <div className="ec-result">
                    <ToolResult tool={m.result.tool} data={m.result.data} onApplyText={onApplyText} />
                  </div>
                )}
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
                  </div>
                ))}
              </div>
            )}
            {m.executed && m.executed.filter((e) => e.ok && e.data).map((e, j) => (
              <div className="ec-result" key={`r${j}`}>
                <ToolResult tool={e.tool} data={(e.data ?? {}) as Record<string, unknown>} onApplyText={onApplyText} />
              </div>
            ))}
            {m.error && <div className="ec-err">{m.error}</div>}
          </div>
        ))}
      </div>

      <div className="ec-quick">
        {quickListOf(openAnns.length).map((q) => (
          <button key={q} className="ec-quick-btn" disabled={!!busy} onClick={() => void send(q)}>{q}</button>
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
        <button className="ws-btn ws-btn-primary" disabled={!!busy || !input.trim()} onClick={() => void send()}>发送</button>
      </div>
    </div>
  );
}
