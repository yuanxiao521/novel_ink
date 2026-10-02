import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { Tabs } from '../components/common/Tabs';
import { useDialog } from '../components/common/Dialog';
import { EditorChat } from '../components/studio/EditorChat';
import { TasksPanel } from '../components/studio/TasksPanel';
import { ProseBody, splitParagraphs } from '../components/studio/ProseBody';
import { useWorkspace } from '../context/WorkspaceContext';
import {
  callAgentTool, createAnnotation, deleteAnnotation, fetchSceneDetail, listAgentTasks,
  listAgentTools, listAnnotations, listProseNotes, sceneScriptUrl,
} from '../api/novel';
import type { AgentTask, ProseAnnotation, ProseNote, SceneDetail } from '../api/novel';

/* ==========================================================================
   StudioPage —— 正文协作（P2）
   三区：左「场景信息」· 中「正文（段落序号 ¶n + 选中批注）」· 右「责编 / 质检」
   按钮与责编对话走同一条工具链（后端 ToolRegistry/ToolExecutor）：
     write 只出候选 → 采纳才改正文；destructive（保存/批注状态）先弹确认再带 confirm 重试。
   ========================================================================== */

type SideTab = 'editor' | 'quality' | 'tasks';
type AuditFilter = 'all' | 'pending' | 'approved' | 'rejected';

const KIND_LABEL: Record<string, string> = {
  writer: '✍ 写手', editor: '🩺 体检员', polisher: '🎨 润色师', verifier: '🔍 质检员',
  tool: '🔧 工具', bookkeeping: '📒 记账',
};
const STATUS_LABEL: Record<string, string> = { pending: '待审阅', approved: '已批准', rejected: '已驳回' };
const TOOL_LABEL: Record<string, string> = {
  'prose.review': '体检', 'prose.polish': '润色', 'prose.verify': '质检',
  'prose.scan_tone': 'AI 味扫描', 'prose.spot_fix': '定点修复', 'prose.quality_loop': '质量回环',
};

export function StudioPage() {
  const { sceneId: routeSceneId } = useParams<{ sceneId: string }>();
  const { sceneId: ctxSceneId } = useWorkspace();
  const sceneId = routeSceneId || ctxSceneId;
  const nav = useNavigate();
  const { showConfirm } = useDialog();

  const [scene, setScene] = useState<SceneDetail | null>(null);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState<ProseNote[]>([]);
  const [annotations, setAnnotations] = useState<ProseAnnotation[]>([]);
  const [toolCount, setToolCount] = useState(0);
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [busy, setBusy] = useState('');
  const [toast, setToast] = useState('');
  const [last, setLast] = useState<{ tool: string; data: Record<string, unknown> } | null>(null);
  // 侧栏 tab 支持深链：?tab=editor|quality|tasks（便于分享与截图核对）
  const [params, setParams] = useSearchParams();
  const tabParam = params.get('tab') as SideTab | null;
  const [sideTab, setSideTab] = useState<SideTab>(tabParam && ['editor', 'quality', 'tasks'].includes(tabParam) ? tabParam : 'editor');
  const switchTab = (k: SideTab) => { setSideTab(k); setParams({ tab: k }); };
  const [auditFilter, setAuditFilter] = useState<AuditFilter>('all');
  const [auditAll, setAuditAll] = useState(false);
  const [chatSeed, setChatSeed] = useState('');

  const flash = (t: string) => { setToast(t); window.setTimeout(() => setToast(''), 2200); };

  const load = useCallback(() => {
    if (!sceneId) return;
    void (async () => {
      try { const d = await fetchSceneDetail(sceneId); setScene(d); setText(d.final_prose ?? ''); } catch { /* 保持现状 */ }
      try { setNotes(await listProseNotes(sceneId)); } catch { /* 保持现状 */ }
      try { setAnnotations(await listAnnotations(sceneId)); } catch { setAnnotations([]); }
      try { setTasks(await listAgentTasks(sceneId)); } catch { setTasks([]); }
    })();
  }, [sceneId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { listAgentTools().then((t) => setToolCount(t.length)).catch(() => setToolCount(0)); }, []);

  /* ---------- 工具调用（按钮与对话同一条链） ---------- */
  const runTool = useCallback(
    async (tool: string, args: Record<string, unknown>, opts?: { confirm?: boolean; label?: string }): Promise<void> => {
      if (!sceneId || busy) return;
      setBusy(tool);
      try {
        const res = await callAgentTool(sceneId, tool, args, opts?.confirm ?? false);
        setLast({ tool, data: res.data ?? {} });
        const t = (res.data?.text ?? res.data?.after) as string | undefined;
        if (typeof t === 'string' && t.trim()) {
          setText(t);
          flash(`${opts?.label ?? tool}：已生成候选（未落库，点「保存」才写库）`);
        } else {
          flash(`${opts?.label ?? tool}：完成（右栏 / 审计可查）`);
        }
        load();
      } catch (e) {
        const msg = String(e instanceof Error ? e.message : e);
        if (/确认/.test(msg) && !opts?.confirm) {
          const ok = await showConfirm('确认执行', `${opts?.label ?? tool}：${msg}`, false);
          if (ok) { setBusy(''); await runTool(tool, args, { ...opts, confirm: true }); return; }
          flash('已取消');
        } else {
          flash(`失败：${msg}`);
        }
      } finally {
        setBusy('');
      }
    },
    [sceneId, busy, load, showConfirm],
  );

  /* ---------- 批注 ---------- */
  const onAnnotate = async (paraIndex: number, quote: string, note: string) => {
    if (!sceneId) return;
    try {
      await createAnnotation(sceneId, { note, para_index: paraIndex, quote });
      setAnnotations(await listAnnotations(sceneId));
      flash(`已批注 ¶${paraIndex}（责编会读到）`);
    } catch (e) { flash(`批注失败：${String(e)}`); }
  };
  const onResolve = async (id: string, status: 'handled' | 'dismissed') => {
    try {
      await callAgentTool(sceneId, 'annotation.resolve', { annotation_id: id, status }, true);
      setAnnotations(await listAnnotations(sceneId));
      flash(status === 'handled' ? '已标记处理' : '已撤销');
    } catch (e) { flash(`失败：${String(e)}`); }
  };
  const onDeleteAnnotation = async (id: string) => {
    const ok = await showConfirm('删除批注', '不可恢复。', true);
    if (!ok) return;
    try { await deleteAnnotation(id); setAnnotations(await listAnnotations(sceneId)); } catch (e) { flash(`删除失败：${String(e)}`); }
  };

  /* ---------- 派生 ---------- */
  const words = text.replace(/\s/g, '').length;
  const paras = useMemo(() => splitParagraphs(text), [text]);
  const openAnns = annotations.filter((a) => a.status === 'open');
  const cast = ((scene as unknown as { characters?: Array<{ name: string }> })?.characters ?? []).map((c) => c.name);
  const pendingCount = notes.filter((n) => n.status === 'pending').length;

  const auditRows = useMemo(() => {
    const filtered = notes.filter((n) => auditFilter === 'all' || n.status === auditFilter);
    const keep = auditAll ? filtered : filtered.filter((n, i) => i === 0 || n.status === 'pending');
    const merged: Array<{ note: ProseNote; count: number }> = [];
    for (const n of keep) {
      const prev = merged[merged.length - 1];
      if (prev && prev.note.kind === n.kind && prev.note.status === n.status && prev.note.suggestion === n.suggestion) prev.count += 1;
      else merged.push({ note: n, count: 1 });
    }
    return { rows: merged, hidden: Math.max(0, filtered.length - keep.length) };
  }, [notes, auditFilter, auditAll]);

  const resultCard = (r: { tool: string; data: Record<string, unknown> }) => {
    const d = r.data;
    const t = (d.text ?? d.after) as string | undefined;
    const report = d.report as { overall?: string; issues?: Array<{ severity: string; text: string; suggestion?: string }> } | undefined;
    const opinion = d.opinion as { risks?: string[]; foreshadow_updates?: unknown[]; belief_deltas?: unknown[] } | undefined;
    const score = d.total ?? d.score;
    return (
      <div className="s2-result">
        <div className="s2-result-head">最近一次：<b>{r.tool}</b></div>
        {typeof score === 'number' && <div className="s2-score">质量分 <b>{Math.round(score as number)}</b></div>}
        {report && (
          <>
            {report.overall && <div className="s2-result-line">{report.overall}</div>}
            {(report.issues ?? []).slice(0, 6).map((it, i) => (
              <div className="s2-issue" key={i}>
                <span className={`s2-sev ${it.severity}`}>{it.severity}</span>
                <span>{it.text}</span>
                {it.suggestion && <em>→ {it.suggestion}</em>}
              </div>
            ))}
          </>
        )}
        {opinion && (
          <>
            <div className="s2-result-line">
              伏笔推进 {(opinion.foreshadow_updates ?? []).length} · 信念变化 {(opinion.belief_deltas ?? []).length} · 风险 {(opinion.risks ?? []).length}
            </div>
            {(opinion.risks ?? []).slice(0, 4).map((x, i) => (
              <div className="s2-issue" key={i}><span className="s2-sev high">risk</span><span>{x}</span></div>
            ))}
          </>
        )}
        {typeof t === 'string' && t.trim() && (
          <div className="s2-preview">
            <div className="s2-preview-text">{t.slice(0, 160)}{t.length > 160 ? '……' : ''}</div>
            <button className="ws-btn ws-btn-primary" onClick={() => { setText(t); flash('候选已放入正文区（保存才落库）'); }}>采纳到正文</button>
          </div>
        )}
        {!report && !opinion && typeof score !== 'number' && !t && <div className="s2-result-line">（明细见下方审计记录）</div>}
      </div>
    );
  };

  const shell = (children: React.ReactNode) => (
    <div className="app-shell">
      <Sidebar active="studio" />
      <div className="main-col">
        <ContextBar step="studio" />
        {children}
      </div>
    </div>
  );

  if (!sceneId) {
    return shell(
      <div className="ws-entry" style={{ minHeight: 'auto', flex: 1 }}>
        <div className="ws-empty">
          <div className="ws-empty-title">还没有场景</div>
          <div className="ws-empty-desc">先去主笔创作加一章、加一个场景。</div>
          <button className="ws-btn ws-btn-primary" onClick={() => nav('/maestro')}>去主笔创作</button>
        </div>
      </div>,
    );
  }

  return shell(
    <div className="s2-page">
      {toast && <div className="s2-toast">{toast}</div>}

      <header className="s2-head">
        <span className="s2-title">{scene?.title ?? '正文协作'}</span>
        <span className="s2-sub">{words} 字 · {paras.length} 段 · {annotations.length} 条批注（待处理 {openAnns.length}）</span>
        <span className="s2-src">
          当前生效：<b>{notes[0] ? `${KIND_LABEL[notes[0].kind] ?? notes[0].kind} · ${STATUS_LABEL[notes[0].status] ?? notes[0].status}` : '尚无记录'}</b>
          {pendingCount > 0 && ` · 待审 ${pendingCount} 条`}
        </span>
      </header>

      <div className="s2-actions">
        <button className="ws-btn ws-btn-primary" disabled={!!busy} onClick={() => void runTool('prose.draft', {}, { label: '写手·初稿' })}>
          {busy === 'prose.draft' ? '写手中…' : '✍ 写手 · 初稿'}
        </button>
        {(['prose.review', 'prose.polish', 'prose.verify'] as const).map((tool) => (
          <button key={tool} className="ws-btn" disabled={!!busy || !text.trim()} onClick={() => void runTool(tool, { text }, { label: TOOL_LABEL[tool] })}>
            {busy === tool ? '处理中…' : TOOL_LABEL[tool]}
          </button>
        ))}
        <details className="s2-more">
          <summary>精修工具 ▾</summary>
          <div className="s2-more-body">
            {(['prose.scan_tone', 'prose.spot_fix', 'prose.quality_loop'] as const).map((tool) => (
              <button key={tool} className="ws-btn" disabled={!!busy || !text.trim()} onClick={() => void runTool(tool, { text }, { label: TOOL_LABEL[tool] })}>
                {TOOL_LABEL[tool]}
              </button>
            ))}
          </div>
        </details>
        <span className="s2-spacer" />
        <button className="ws-btn" onClick={() => setEditing((v) => !v)}>{editing ? '✓ 完成编辑' : '✎ 编辑'}</button>
        <button className="ws-btn ws-btn-primary" disabled={!!busy || !text.trim()} onClick={() => void runTool('prose.save', { text }, { label: '保存正文' })}>💾 保存</button>
        <a className="ws-btn" href={sceneScriptUrl(sceneId, true)} target="_blank" rel="noreferrer">导出剧本</a>
      </div>

      <div className="s2-body">
        <aside className="s2-info">
          <div className="s2-info-title">场景信息</div>
          <div className="s2-kv"><span>本场目标</span><b>{scene?.goal || '未定'}</b></div>
          <div className="s2-kv"><span>舞台</span><b>{scene?.stage_desc || '未布置'}</b></div>
          <div className="s2-kv"><span>上场角色</span><b>{cast.length > 0 ? cast.join('、') : '未配置'}</b></div>
          {scene?.content_desc && <div className="s2-kv"><span>内容</span><b>{scene.content_desc}</b></div>}
          <div className="s2-info-title" style={{ marginTop: 12 }}>待处理批注 · {openAnns.length}</div>
          {openAnns.length === 0 && <div className="s2-hint">选中正文里的一句 → 「批注」，责编就能读到你的意见。</div>}
          {openAnns.map((a) => (
            <div className="s2-ann-mini" key={a.id}><span className="s2-ann-no">¶{a.para_index || '?'}</span>{a.note}</div>
          ))}
        </aside>

        <section className="s2-prose">
          <ProseBody
            text={text}
            annotations={annotations}
            editing={editing}
            onChange={setText}
            onAnnotate={(i, q, n) => void onAnnotate(i, q, n)}
            onAskEditor={(m) => { switchTab('editor'); setChatSeed(m); }}
            onResolve={(id, s) => void onResolve(id, s)}
            onDeleteAnnotation={(id) => void onDeleteAnnotation(id)}
          />
        </section>

        <aside className="s2-side">
          <div className="s2-side-head">
            <Tabs
              items={[
                { key: 'editor', label: '责编' },
                { key: 'quality', label: '质检', badge: openAnns.length },
                { key: 'tasks', label: '任务', badge: tasks.filter((t) => t.status !== 'completed' && t.status !== 'failed').length },
              ]}
              value={sideTab}
              onChange={(k) => switchTab(k as SideTab)}
            />
            <span className="s2-tool-count" title="可用工具（按钮与对话共用）">{toolCount} 个工具</span>
          </div>
          {sideTab === 'tasks' ? (
            <div className="s2-quality">
              <TasksPanel sceneId={sceneId} tasks={tasks} onRefresh={load} onFlash={flash} />
            </div>
          ) : sideTab === 'editor' ? (
            <EditorChat
              sceneId={sceneId}
              text={text}
              onApplyText={(t) => { setText(t); flash('候选已放入正文区（保存才落库）'); }}
              onRefresh={load}
              seedMessage={chatSeed}
              onSeedConsumed={() => setChatSeed('')}
            />
          ) : (
            <div className="s2-quality">
              {last ? resultCard(last) : <div className="s2-hint">还没跑过质检类工具。点上面的「质检」或「质量回环」，结果会显示在这里；明细永远能在下方审计记录里查到。</div>}
            </div>
          )}
        </aside>
      </div>

      <div className="s2-audit">
        <div className="s2-audit-head">
          <span className="s2-audit-title">审计记录</span>
          <Tabs
            items={[
              { key: 'all', label: '全部' },
              { key: 'pending', label: '待审', badge: pendingCount },
              { key: 'approved', label: '已批准' },
              { key: 'rejected', label: '已驳回' },
            ]}
            value={auditFilter}
            onChange={(k) => setAuditFilter(k as AuditFilter)}
          />
          <span className="s2-spacer" />
          <span className="s2-hint">{auditAll ? `显示全部 ${notes.length} 条` : `默认只看最新 + 待审（${auditRows.hidden} 条已折叠）`}</span>
          <button className="ws-btn" onClick={() => setAuditAll((v) => !v)}>{auditAll ? '只看最新+待审' : '展开全部'}</button>
        </div>
        <div className="s2-audit-list">
          {auditRows.rows.length === 0 && <div className="s2-hint">暂无记录。</div>}
          {auditRows.rows.map(({ note: n, count }) => (
            <div className={`s2-note ${n.status}`} key={n.id}>
              <span className="s2-note-kind">{KIND_LABEL[n.kind] ?? n.kind}{count > 1 ? ` ×${count}` : ''}</span>
              <span className="s2-note-status">{STATUS_LABEL[n.status] ?? n.status}</span>
              <span className="s2-note-text">{n.suggestion}</span>
              <span className="s2-note-by">{n.created_by}</span>
              <span className="s2-note-ts">{new Date(n.ts).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
    </div>,
  );
}
