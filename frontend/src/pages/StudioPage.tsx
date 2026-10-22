import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { Tabs } from '../components/common/Tabs';
import { useDialog } from '../components/common/Dialog';
import { CharacterVoices } from '../components/studio/CharacterVoices';
import { EditorChat } from '../components/studio/EditorChat';
import { TasksPanel } from '../components/studio/TasksPanel';
import { ProseBody, splitParagraphs } from '../components/studio/ProseBody';
import { useWorkspace } from '../context/WorkspaceContext';
import {
  callAgentTool, createAnnotation, deleteAnnotation, draftProseStream, fetchSceneDetail,
  listAgentTasks, listAgentTools, listAnnotations, listProseNotes, sceneScriptUrl,
} from '../api/novel';
import type { AgentTask, ProseAnnotation, ProseNote, SceneDetail } from '../api/novel';

/* ==========================================================================
   StudioPage —— 正文协作（P2）
   三区：左「场景信息」· 中「正文（段落序号 ¶n + 选中批注）」· 右「责编 / 质检」
   按钮与责编对话走同一条工具链（后端 ToolRegistry/ToolExecutor）：
     write 只出候选 → 采纳才改正文；destructive（保存/批注状态）先弹确认再带 confirm 重试。
   ========================================================================== */

// v1.43：「质检」tab 已取消——复杂工具的**过程与明细**都内联在「责编」流里（ToolResult 富卡）
type SideTab = 'editor' | 'tasks' | 'voices';
type AuditFilter = 'all' | 'pending' | 'approved' | 'rejected';

const KIND_LABEL: Record<string, string> = {
  writer: '✍ 写手', editor: '🩺 体检员', polisher: '🎨 润色师', verifier: '🔍 质检员',
  tool: '🔧 工具', bookkeeping: '📒 记账',
};
const STATUS_LABEL: Record<string, string> = { pending: '待审阅', approved: '已批准', rejected: '已驳回' };

/** 精修工具（v1.43）：按钮留在顶栏原位；点了自动切到「责编」tab —— 过程与明细在那个流里。 */
const REFINE_TOOLS: Array<{ tool: string; label: string; hint: string }> = [
  { tool: 'prose.review', label: '🩺 体检', hint: '问题清单 + 逐角色口吻比对' },
  { tool: 'prose.verify', label: '🔍 质检', hint: '伏笔/信念/因果/状态对照账本' },
  { tool: 'prose.polish', label: '🎨 润色', hint: '只改写法不改事实' },
  { tool: 'prose.quality_score', label: '📊 评审', hint: '五维自评 + 硬信号合分（只读）' },
  { tool: 'prose.scan_tone', label: '🧪 AI 味', hint: '0-token 规则扫描，不花模型' },
  { tool: 'prose.spot_fix', label: '🔧 定点修复', hint: '只修命中句，双闸复检' },
  { tool: 'prose.quality_loop', label: '♻ 质量回环', hint: '自评 → 按需重写 → 复检；后台跑' },
];
export function StudioPage() {
  const { sceneId: routeSceneId } = useParams<{ sceneId: string }>();
  const { sceneId: ctxSceneId, bookId } = useWorkspace();
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
  // 侧栏 tab 支持深链：?tab=editor|quality|tasks（便于分享与截图核对）
  const [params, setParams] = useSearchParams();
  const tabParam = params.get('tab') as SideTab | null;
  const [sideTab, setSideTab] = useState<SideTab>(
    tabParam && ['editor', 'tasks', 'voices'].includes(tabParam) ? tabParam : 'editor',
  );
  const switchTab = (k: SideTab) => { setSideTab(k); setParams({ tab: k }); };
  const [auditFilter, setAuditFilter] = useState<AuditFilter>('all');
  const [auditAll, setAuditAll] = useState(false);
  const [chatSeed, setChatSeed] = useState('');
  /** 顶栏按钮 → 责编流（v1.43）：按钮位置不变，过程与明细进流 */
  const [pendingTool, setPendingTool] = useState('');
  /** 责编面板正在跑什么（顶栏按钮据此显示进度并禁用） */
  const [editorBusy, setEditorBusy] = useState('');
  const anyBusy = !!busy || !!editorBusy;
  /** 审计记录可收起（v1.43）：正文区拿回高度，偏好记本地 */
  const [auditOpen, setAuditOpen] = useState(() => {
    try { return localStorage.getItem('mk.auditOpen') !== '0'; } catch { return true; }
  });
  const toggleAudit = () => setAuditOpen((v) => {
    const next = !v;
    try { localStorage.setItem('mk.auditOpen', next ? '1' : '0'); } catch { /* 忽略 */ }
    return next;
  });
  /** 点顶栏工具：先切到「责编」tab（保证看得见），再把活派给那个面板 */
  const askEditor = (tool: string) => { switchTab('editor'); setPendingTool(tool); };

  const flash = (t: string) => { setToast(t); window.setTimeout(() => setToast(''), 2200); };

  /** 编辑区有未保存改动（v1.44）：写手/采纳候选会覆盖它，先问一句 */
  const savedText = scene?.final_prose ?? '';
  const dirty = text !== savedText;
  const guardDirty = async (what: string): Promise<boolean> => {
    if (!dirty) return true;
    return showConfirm('覆盖未保存的改动？',
      `正文区有还没保存的修改，${what}会覆盖编辑区内容（数据库里已保存的版本不受影响，审计记录里也有 before 稿）。`,
      true);
  };

  /** 写手 · 初稿（v1.44 流式）：逐 token 写进中间正文区，不再黑等 */
  const runDraftStream = async () => {
    if (!sceneId || anyBusy) return;
    if (!(await guardDirty('写手初稿'))) return;
    setBusy('prose.draft');
    let acc = '';
    try {
      await draftProseStream(
        sceneId,
        (delta) => { acc += delta; setText(acc); },
        (text) => { if (text) setText(text); },
      );
      if (!acc.trim()) {
        flash('写手没产出内容（模型未接入或调用失败，见右上角降级提示 / /health.llm）');
      } else {
        flash(`写手：已流式生成 ${acc.replace(/\s/g, '').length} 字（未落库，点「保存」才写库）`);
      }
      // 只刷审计（**不能** load()：那会把 setText 拉回已落库版本，把刚流出来的稿子冲掉）
      try { setNotes(await listProseNotes(sceneId)); } catch { /* 保持现状 */ }
    } catch (e) {
      flash(`写手失败：${String(e instanceof Error ? e.message : e)}`);
    } finally {
      setBusy('');
    }
  };

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
        <span className="s2-sub">
          {words} 字 · {paras.length} 段 · {annotations.length} 条批注（待处理 {openAnns.length}）
          {dirty && <b className="s2-dirty" title="编辑区有未保存改动：写手/采纳候选会覆盖它"> · 未保存</b>}
        </span>
        {/* v1.64：场景精修 → 章级写作页（两个页面互跳，作者不用记 URL） */}
        {scene?.chapter_id && (
          <button className="ws-btn" onClick={() => nav('/write/' + scene.chapter_id + (bookId ? '?book=' + bookId : ''))}
                  title="回到章级写作页（整章正文 + 记忆/前情/稿本）">
            ✍ 写这一章
          </button>
        )}
        <span className="s2-src">
          当前生效：<b>{notes[0] ? `${KIND_LABEL[notes[0].kind] ?? notes[0].kind} · ${STATUS_LABEL[notes[0].status] ?? notes[0].status}` : '尚无记录'}</b>
          {pendingCount > 0 && ` · 待审 ${pendingCount} 条`}
        </span>
      </header>

      <div className="s2-actions">
        <button className="ws-btn ws-btn-primary" disabled={anyBusy} onClick={() => void runDraftStream()}
                title="流式生成初稿：正文区会一个字一个字长出来（未落库，点「保存」才写库）">
          {busy === 'prose.draft' ? '✍ 写手中…' : '✍ 写手 · 初稿'}
        </button>
        {/* v1.43：按钮**留在顶栏**，点了自动切到右栏「责编」——过程卡与明细富卡长在那个流里 */}
        <button
          className="ws-btn s2-meeting"
          disabled={anyBusy || !text.trim()}
          title="体检与质检并发 → 润色（有问题才做）→ 评审，后台跑完给出裁决（刷新不中断）；过程与明细在右栏「责编」里"
          onClick={() => askEditor('prose.review_meeting')}
        >
          {editorBusy === 'prose.review_meeting' ? '审稿中…' : '🗂 审稿会'}
        </button>
        <details className="s2-more">
          <summary>精修工具 ▾</summary>
          <div className="s2-more-body">
            {REFINE_TOOLS.map((t) => (
              <button
                key={t.tool}
                className="ws-btn"
                title={t.hint}
                disabled={anyBusy || !text.trim()}
                onClick={() => askEditor(t.tool)}
              >
                {editorBusy === t.tool ? `${t.label} · 跑着…` : t.label}
              </button>
            ))}
          </div>
        </details>
        <span className="s2-spacer" />
        {!text.trim() && <span className="s2-moved">正文为空：先点「✍ 写手 · 初稿」或「✎ 编辑」写一段</span>}
        <button className="ws-btn" onClick={() => setEditing((v) => !v)}>{editing ? '✓ 完成编辑' : '✎ 编辑'}</button>
        <button className="ws-btn ws-btn-primary" disabled={anyBusy || !text.trim() || !dirty}
                onClick={() => void runTool('prose.save', { text }, { label: '保存正文' })}
                title={dirty ? '把编辑区内容写入 scenes.final_prose（需确认）' : '编辑区与已落库版本一致，无需保存'}>
          {dirty ? '💾 保存*' : '💾 已保存'}
        </button>
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
                // badge 为 0 时不传（否则标题会显示「责编 0 任务 0」，很吵）
                { key: 'editor', label: '责编', ...(openAnns.length > 0 ? { badge: openAnns.length } : {}) },
                {
                  key: 'tasks', label: '任务',
                  ...(tasks.filter((t) => t.status !== 'completed' && t.status !== 'failed').length > 0
                    ? { badge: tasks.filter((t) => t.status !== 'completed' && t.status !== 'failed').length } : {}),
                },
                { key: 'voices', label: '角色' },
              ]}
              value={sideTab}
              onChange={(k) => switchTab(k as SideTab)}
            />
            <span className="s2-tool-count" title="可用工具（按钮与对话共用）">{toolCount} 个工具</span>
          </div>
          {sideTab === 'voices' ? (
            <div className="s2-quality">
              <CharacterVoices sceneId={sceneId} text={text} onFlash={flash} />
            </div>
          ) : sideTab === 'tasks' ? (
            <div className="s2-quality">
              <TasksPanel sceneId={sceneId} tasks={tasks} onRefresh={load} onFlash={flash} />
            </div>
          ) : (
            <EditorChat
              sceneId={sceneId}
              text={text}
              onApplyText={async (t) => {
                if (!(await guardDirty('采纳候选'))) return;
                setText(t);
                flash('候选已放入正文区（保存才落库）');
              }}
              onRefresh={load}
              seedMessage={chatSeed}
              onSeedConsumed={() => setChatSeed('')}
              pendingTool={pendingTool}
              onPendingConsumed={() => setPendingTool('')}
              onBusyChange={setEditorBusy}
            />
          )}
        </aside>
      </div>

      <div className={`s2-audit${auditOpen ? '' : ' collapsed'}`}>
        <div className="s2-audit-head">
          <button className="s2-audit-toggle" onClick={toggleAudit} title="收起/展开审计记录（正文区会拿回高度）">
            <span className="s2-audit-caret">{auditOpen ? '▾' : '▸'}</span>
            <span className="s2-audit-title">审计记录</span>
            <span className="s2-audit-count">{notes.length} 条</span>
          </button>
          {auditOpen && (
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
          )}
          <span className="s2-spacer" />
          {auditOpen && (
            <>
              <span className="s2-hint">{auditAll ? `显示全部 ${notes.length} 条` : `默认只看最新 + 待审（${auditRows.hidden} 条已折叠）`}</span>
              <button className="ws-btn" onClick={() => setAuditAll((v) => !v)}>{auditAll ? '只看最新+待审' : '展开全部'}</button>
            </>
          )}
          <button className="ws-btn" onClick={toggleAudit}>{auditOpen ? '收起' : '展开'}</button>
        </div>
        {auditOpen && <div className="s2-audit-list">
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
        </div>}
      </div>
    </div>,
  );
}
