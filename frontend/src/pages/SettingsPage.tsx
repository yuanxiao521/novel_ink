import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { EmptyState } from '../components/common/EmptyState';
import { Tabs } from '../components/common/Tabs';
import { useDialog } from '../components/common/Dialog';
import { createMemory, deleteMemory, fetchHealth, listMemories, updateMemory } from '../api/novel';
import type { BookMemoryMeta, MemoryTopic } from '../api/novel';
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
                <div className="settings-card-title">世界状态账本（S1）</div>
                <EmptyState
                  icon="▤"
                  title="账本已落库，浏览视图待补"
                  desc="S1 的写入（质检 state_deltas）与感知（写手/主笔注入）已交付并落库 world_states；本页只差一个只读列表接口，列为 P1 补项。"
                  primary={{ label: '看全局诊断', onClick: () => nav(ws.bookId ? `/dashboard?book=${ws.bookId}` : '/dashboard') }}
                  secondary={{ label: '去正文协作', onClick: () => nav(ws.sceneId ? `/studio/${ws.sceneId}` : '/studio') }}
                />
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
