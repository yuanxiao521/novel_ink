import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { EmptyState } from '../components/common/EmptyState';
import { Tabs } from '../components/common/Tabs';
import { useDialog } from '../components/common/Dialog';
import { createMemory, createWorldState, deleteMemory, deleteWorldState, fetchConstraints, fetchHealth, fetchWorldStates, listMemories, updateMemory, updateWorldState } from '../api/novel';
import type { BookMemoryMeta, ConstraintTable, MemoryTopic, WorldStateMeta } from '../api/novel';
import { useWorkspace } from '../context/WorkspaceContext';

/* ==========================================================================
   SettingsPage —— 设定（书级资产容器）
   tab：方向 / 世界观 / 世界状态 / 约束 / 记忆 / 全局
   "方向"是骨架生成（POST /books/{id}/plan）的 direction 来源，此前只存在于主笔页输入框。
   ========================================================================== */

type TabKey = 'direction' | 'worldview' | 'states' | 'constraint' | 'memory' | 'global';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'direction', label: '方向' },
  { key: 'worldview', label: '世界观' },
  { key: 'states', label: '世界状态' },
  { key: 'constraint', label: '约束' },
  { key: 'memory', label: '记忆' },
  { key: 'global', label: '全局' },
];

const MEMORY_TOPIC_LABEL: Record<string, string> = {
  direction: '方向',
  setting: '设定',
  constraint: '约束',
  history: '历史',
  preference: '偏好',
};

const MEMORY_TABS: MemoryTopic[] = ['setting', 'history', 'preference'];

function pretty(json?: string): string {
  if (!json) return '';
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}

export function SettingsPage() {
  const ws = useWorkspace();
  const nav = useNavigate();
  const { showConfirm } = useDialog();
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab') as TabKey | null;
  const tab: TabKey = raw && TABS.some((t) => t.key === raw) ? raw : 'direction';

  const [memories, setMemories] = useState<BookMemoryMeta[]>([]);
  const [draft, setDraft] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<{ status: string; degraded?: { active?: boolean; count?: number } } | null>(null);
  const [table, setTable] = useState<ConstraintTable | null>(null);
  const [worldStates, setWorldStates] = useState<WorldStateMeta[] | null>(null);
  const [wsKind, setWsKind] = useState<string>('');
  const [wsEditingId, setWsEditingId] = useState<string | null>(null);
  const [wsForm, setWsForm] = useState<{ kind: string; name: string; attr: string; value: string } | null>(null);
  const [wsBusy, setWsBusy] = useState(false);

  const load = useCallback(() => {
    if (!ws.bookId) {
      setMemories([]);
      return;
    }
    listMemories(ws.bookId)
      .then(setMemories)
      .catch(() => setMemories([]));
  }, [ws.bookId]);
  useEffect(() => {
    load();
  }, [load]);

  // B 批：书级约束表（全员读的单一来源；0-token 派生）
  useEffect(() => {
    if (!ws.bookId) { setTable(null); return; }
    fetchConstraints(ws.bookId).then(setTable).catch(() => setTable(null));
  }, [ws.bookId, ws.tree]);

  // S1 世界状态账本（质检 state_deltas 落库 + 作者编辑）
  const loadStates = useCallback(() => {
    if (!ws.bookId) { setWorldStates([]); return; }
    fetchWorldStates(ws.bookId, wsKind || undefined).then(setWorldStates).catch(() => setWorldStates(null));
  }, [ws.bookId, wsKind]);
  useEffect(() => {
    if (tab === 'states') loadStates();
  }, [tab, loadStates]);

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealth(h as { status: string; degraded?: { active?: boolean; count?: number } }))
      .catch(() => setHealth(null));
  }, []);

  const direction = useMemo(() => memories.find((m) => m.topic === 'direction') ?? null, [memories]);
  const constraints = useMemo(() => memories.filter((m) => m.topic === 'constraint'), [memories]);
  const plainMemories = useMemo(() => memories.filter((m) => MEMORY_TABS.includes(m.topic)), [memories]);

  useEffect(() => {
    setDraft(tab === 'direction' ? direction?.content ?? '' : '');
    setEditingId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, direction?.id, ws.bookId]);

  const save = async (topic: MemoryTopic, id: string | null, content: string) => {
    const text = content.trim();
    if (!text || !ws.bookId) return;
    setBusy(true);
    try {
      if (id) await updateMemory(id, { topic, content: text });
      else await createMemory(ws.bookId, { topic, content: text });
      load();
      setEditingId(null);
      setDraft('');
    } catch (e) {
      console.warn('[设定] 保存失败：', e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m: BookMemoryMeta) => {
    const ok = await showConfirm('删除', '删除后主笔将不再感知这条内容，不可恢复。', true);
    if (!ok) return;
    try {
      await deleteMemory(m.id);
      load();
    } catch (e) {
      console.warn('[设定] 删除失败：', e);
    }
  };

  // ---- S1 世界状态：作者编辑 ----
  const wsSave = async (editing: WorldStateMeta | null) => {
    if (!ws.bookId || !wsForm) return;
    const { kind, name, attr, value } = wsForm;
    if (!name.trim() || !value.trim()) return;
    setWsBusy(true);
    try {
      if (editing) {
        await updateWorldState(editing.id, { kind: editing.kind, name: name.trim(), value: value.trim(), key: editing.key, scene_no: editing.scene_no });
      } else {
        await createWorldState(ws.bookId, { kind, name: name.trim(), value: value.trim(), attr: attr.trim() || undefined });
      }
      setWsForm(null);
      setWsEditingId(null);
      loadStates();
    } catch (e) {
      console.warn('[世界状态] 保存失败：', e);
    } finally {
      setWsBusy(false);
    }
  };

  const wsRemove = async (s: WorldStateMeta) => {
    const ok = await showConfirm('删除状态', `删除「${s.name} · ${s.key}」？质检不会再往这个 key 写，但历史正文不受影响。`, true);
    if (!ok) return;
    try {
      await deleteWorldState(s.id);
      loadStates();
    } catch (e) {
      console.warn('[世界状态] 删除失败：', e);
    }
  };

  const startEditWs = (s: WorldStateMeta) => {
    setWsEditingId(s.id);
    setWsForm({ kind: s.kind, name: s.name, attr: '', value: s.value });
  };

  const startNewWs = () => {
    setWsEditingId(null);
    setWsForm({ kind: wsKind || 'character', name: '', attr: '', value: '' });
  };

  const WS_KIND_LABEL: Record<string, string> = {
    character: '角色', item: '道具', time: '时间', term: '术语', numeric: '数值',
  };

  const memoryRows = (rows: BookMemoryMeta[], topic: MemoryTopic) => (
    <>
      {rows.length === 0 && (
        <EmptyState
          compact
          icon="◌"
          title="还没有内容"
          desc="写一条，主笔每次对话与规划都会感知到。"
          primary={{ label: '＋ 添加', onClick: () => { setEditingId('new'); setDraft(''); } }}
        />
      )}
      {rows.map((m) => (
        <div className="settings-row" key={m.id}>
          {editingId === m.id ? (
            <>
              <textarea
                className="settings-textarea"
                rows={2}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <button className="ws-btn ws-btn-primary" disabled={busy} onClick={() => void save(topic, m.id, draft)}>
                保存
              </button>
              <button className="ws-btn" onClick={() => setEditingId(null)}>
                取消
              </button>
            </>
          ) : (
            <>
              <span className="settings-row-main">{m.content}</span>
              <span className="settings-row-tag">{MEMORY_TOPIC_LABEL[m.topic] ?? m.topic}</span>
              <span className="settings-row-tag">{m.source === 'author' ? '作者' : m.source === 'chief' ? '主笔' : '记账'}</span>
              <button className="ws-btn" onClick={() => { setEditingId(m.id); setDraft(m.content); }}>
                编辑
              </button>
              <button className="ws-btn" onClick={() => void remove(m)}>
                删除
              </button>
            </>
          )}
        </div>
      ))}
      {editingId === 'new' && (
        <div className="settings-row">
          <textarea className="settings-textarea" rows={2} value={draft} placeholder="写一条内容……" onChange={(e) => setDraft(e.target.value)} />
          <button className="ws-btn ws-btn-primary" disabled={busy} onClick={() => void save(topic, null, draft)}>
            添加
          </button>
          <button className="ws-btn" onClick={() => setEditingId(null)}>
            取消
          </button>
        </div>
      )}
      {editingId !== 'new' && rows.length > 0 && (
        <button className="ws-btn" onClick={() => { setEditingId('new'); setDraft(''); }}>
          ＋ 添加
        </button>
      )}
    </>
  );

  return (
    <div className="app-shell">
      <Sidebar active="settings" />
      <div className="main-col">
        <ContextBar step="settings" />
        <div className="settings-page">
          <div className="settings-head">
            <span className="settings-title">设定</span>
            <span className="settings-sub">
              {ws.book?.title ?? '未选书'}
              {ws.bookId && ` · 书级资产（世界观 / 世界状态 / 方向 / 约束 / 记忆）`}
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <Tabs items={TABS} value={tab} onChange={(k) => setParams({ tab: k })} />
            </span>
          </div>

          <div className="settings-body">
            {tab === 'direction' && (
              <div className="settings-card">
                <div className="settings-card-title">一句话方向</div>
                {direction && editingId !== direction.id ? (
                  <>
                    <div className="settings-row-main" style={{ fontSize: 13.5, lineHeight: 1.9 }}>{direction.content}</div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                      <button className="ws-btn" onClick={() => { setEditingId(direction.id); setDraft(direction.content); }}>
                        编辑
                      </button>
                      <button className="ws-btn" onClick={() => void remove(direction)}>
                        删除
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <textarea
                      className="settings-textarea"
                      rows={2}
                      value={draft}
                      placeholder="例：废材少年因血脉被夺，发誓重返宗门讨回公道"
                      onChange={(e) => setDraft(e.target.value)}
                    />
                    <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                      <button
                        className="ws-btn ws-btn-primary"
                        disabled={busy || !draft.trim()}
                        onClick={() => void save('direction', direction?.id ?? null, draft)}
                      >
                        {direction ? '保存方向' : '填写方向'}
                      </button>
                      {editingId && (
                        <button className="ws-btn" onClick={() => { setEditingId(null); setDraft(''); }}>
                          取消
                        </button>
                      )}
                    </div>
                  </>
                )}
                <div className="settings-note">
                  影响范围：骨架生成（<code>POST /books/&#123;id&#125;/plan</code> 的 direction）、写手、主笔对话。建书时也可填（可跳过）。
                </div>
              </div>
            )}

            {tab === 'worldview' && (
              <>
                <div className="settings-card" style={{ borderLeftColor: 'var(--accent-cyan)' }}>
                  <div className="settings-card-title">书级约束表 · 全员读（0-token 派生，不新建表）</div>
                  {table ? (
                    <>
                      <div className="settings-row">
                        <span className="settings-row-main">
                          方向 {table.counts.direction ?? 0} · 硬规则 {table.counts.hard_rules ?? 0} · 约束 {table.counts.constraints ?? 0} · 偏好 {table.counts.preferences ?? 0} · 设定 {table.counts.settings ?? 0}
                        </span>
                        <span className="settings-row-tag">主笔维护</span>
                        <span className="settings-row-tag">全员读</span>
                      </div>
                      <div className="settings-mono">
                        {(table.hard_rules ?? []).map((r) => `硬规则：${r.constraint}`).join('\n') || '硬规则：（无）'}
                        {'\n'}
                        {(table.constraints ?? []).map((c) => `约束：${c}`).join('\n') || '约束：（无）'}
                      </div>
                      {table.dropped.length > 0 && (
                        <div className="settings-note" style={{ color: 'var(--accent-gold)' }}>
                          <b>裁剪留痕（{table.dropped.length}）</b>：
                          {table.dropped.map((d) => `${d.src} → ${d.reason}`).join('；')}
                        </div>
                      )}
                      <div className="settings-note">
                        这份表由「方向 / 世界观 / 硬规则 / 约束 / 偏好 / 记忆」派生而来 —— 主笔与作者维护，**写手、责编、场记、角色都读同一份**（这就是"主笔掌管一切"的落地形态）。
                      </div>
                    </>
                  ) : (
                    <div className="settings-note">还没有约束（先把「方向」和世界观补上）。</div>
                  )}
                </div>
                <div className="settings-card">
                  <div className="settings-card-title">世界观前提</div>
                  {ws.tree?.worldview_json ? (
                    <div className="settings-mono">{pretty(ws.tree.worldview_json)}</div>
                  ) : (
                    <EmptyState compact icon="◍" title="还没有世界观" desc="让主笔构思骨架后会自动写入；也可以在主笔共创里直接描述。" primary={{ label: '去主笔创作', onClick: () => nav('/maestro') }} />
                  )}
                </div>
                <div className="settings-card">
                  <div className="settings-card-title">世界硬规则</div>
                  {ws.tree?.world_rules_json ? (
                    <div className="settings-mono">{pretty(ws.tree.world_rules_json)}</div>
                  ) : (
                    <EmptyState compact icon="⚖" title="还没有硬规则" desc="硬规则由 0-token 校验器强制执行（concept / constraint / keywords）。" />
                  )}
                </div>
              </>
            )}

            {tab === 'states' && (
              <div className="settings-card">
                <div className="settings-card-title">
                  世界状态账本（S1）
                  <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                    {[
                      { v: '', label: '全部' },
                      { v: 'character', label: '角色' },
                      { v: 'item', label: '道具' },
                      { v: 'time', label: '时间' },
                      { v: 'term', label: '术语' },
                      { v: 'numeric', label: '数值' },
                    ].map((k) => (
                      <button
                        key={k.v || 'all'}
                        className={`ws-btn${wsKind === k.v ? ' ws-btn-primary' : ''}`}
                        onClick={() => setWsKind(k.v)}
                      >
                        {k.label}
                      </button>
                    ))}
                    <button className="ws-btn" disabled={!ws.bookId} onClick={startNewWs}>＋ 新增</button>
                  </span>
                </div>
                {worldStates === null ? (
                  <div className="settings-row"><span className="settings-row-main">加载中…</span></div>
                ) : (
                  <>
                    {worldStates.length === 0 && !wsForm && (
                      <EmptyState
                        compact
                        icon="▤"
                        title={!ws.bookId ? '还没有选书' : wsKind ? '这个类别还没有状态记录' : '账本还是空的'}
                        desc={
                          !ws.bookId
                            ? '先在侧栏选一本书，账本按书隔离。'
                            : '质检（state_deltas）批准后会自动落库；作者也可「＋ 新增」手写设定（手改项质检不再覆盖，主笔写作以此为准）。'
                        }
                        primary={
                          !ws.bookId
                            ? { label: '去主笔创作', onClick: () => nav('/maestro') }
                            : { label: '＋ 手写一条', onClick: startNewWs }
                        }
                        secondary={{ label: '去正文协作', onClick: () => nav(ws.sceneId ? `/studio/${ws.sceneId}` : '/studio') }}
                      />
                    )}
                    {worldStates.map((s) => (
                      <div className="settings-row" key={s.id}>
                        {wsEditingId === s.id && wsForm ? (
                          <>
                            <input className="settings-input" style={{ width: 110 }} value={wsForm.name}
                              onChange={(e) => setWsForm({ ...wsForm, name: e.target.value })} placeholder="对象名" />
                            <input className="settings-input" style={{ flex: 1 }} value={wsForm.value}
                              onChange={(e) => setWsForm({ ...wsForm, value: e.target.value })} placeholder="状态值（持续态，如：元婴初期）" />
                            <button className="ws-btn ws-btn-primary" disabled={wsBusy} onClick={() => void wsSave(s)}>保存</button>
                            <button className="ws-btn" onClick={() => { setWsEditingId(null); setWsForm(null); }}>取消</button>
                          </>
                        ) : (
                          <>
                            <span className="settings-row-main">
                              <b>{s.name}</b>
                              <span className="settings-mono" style={{ marginLeft: 8 }}>{s.key}</span>
                              <span style={{ marginLeft: 8 }}>＝ {s.value}</span>
                              {s.previous_value && (
                                <span style={{ marginLeft: 8, opacity: 0.65 }}>（前值：{s.previous_value}）</span>
                              )}
                            </span>
                            <span className="settings-row-tag">{WS_KIND_LABEL[s.kind] ?? s.kind}</span>
                            {s.edited && <span className="settings-row-tag">作者手改</span>}
                            {s.scene_no > 0 && <span className="settings-row-tag">第 {s.scene_no} 场</span>}
                            <button className="ws-btn" onClick={() => startEditWs(s)}>编辑</button>
                            <button className="ws-btn" onClick={() => void wsRemove(s)}>删除</button>
                          </>
                        )}
                      </div>
                    ))}
                    {wsForm && wsEditingId === null && (
                      <div className="settings-row">
                        <select className="settings-input" style={{ width: 88 }} value={wsForm.kind}
                          onChange={(e) => setWsForm({ ...wsForm, kind: e.target.value })}>
                          {Object.entries(WS_KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </select>
                        <input className="settings-input" style={{ width: 110 }} value={wsForm.name}
                          onChange={(e) => setWsForm({ ...wsForm, name: e.target.value })} placeholder="对象名（如 林尘）" />
                        <input className="settings-input" style={{ width: 110 }} value={wsForm.attr}
                          onChange={(e) => setWsForm({ ...wsForm, attr: e.target.value })} placeholder="属性（如 境界）" />
                        <input className="settings-input" style={{ flex: 1 }} value={wsForm.value}
                          onChange={(e) => setWsForm({ ...wsForm, value: e.target.value })} placeholder="状态值（持续态，如：元婴初期）" />
                        <button className="ws-btn ws-btn-primary" disabled={wsBusy} onClick={() => void wsSave(null)}>添加</button>
                        <button className="ws-btn" onClick={() => setWsForm(null)}>取消</button>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === 'constraint' && (
              <div className="settings-card">
                <div className="settings-card-title">写作约束</div>
                {memoryRows(constraints, 'constraint')}
              </div>
            )}

            {tab === 'memory' && (
              <div className="settings-card">
                <div className="settings-card-title">记忆（设定 / 历史 / 偏好）</div>
                {memoryRows(plainMemories, 'setting')}
              </div>
            )}

            {tab === 'global' && (
              <div className="settings-card">
                <div className="settings-card-title">全局设置</div>
                <div className="settings-row">
                  <span className="settings-row-main">服务状态</span>
                  <span className="settings-row-tag">
                    {health ? `${health.status}${health.degraded?.active ? ' · 降级中' : ' · 无降级'}` : '不可用'}
                  </span>
                  {health?.degraded?.active && <span className="settings-row-tag">降级 {health.degraded.count ?? 0} 次</span>}
                </div>
                <div className="settings-row">
                  <span className="settings-row-main">主题</span>
                  <span className="settings-row-tag">纸 / 墨随右上角开关切换</span>
                </div>
                <div className="settings-row">
                  <span className="settings-row-main">素材库</span>
                  <span className="settings-row-tag">跨书全局 · RAG 落地后开放</span>
                </div>
                <div className="settings-note">
                  业务状态单一来源是 PostgreSQL；仓库内存态仅在后端不可达时兜底。Redis 当前不需要（单进程），多 worker / 长任务再引入。
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
