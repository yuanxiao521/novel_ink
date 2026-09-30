import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import { useDialog } from '../components/common/Dialog';
import chenImg from '../assets/portrait-chenmo.png';
import liwImg from '../assets/portrait-liwen.png';
import zhouImg from '../assets/portrait-zhoushen.png';
import {
  listBooks,
  listBookCharacters,
  createBookCharacter,
  updateCharacter,
  deleteCharacter,
  listBookBeliefs,
  createBelief,
  updateBelief,
  deleteBelief,
} from '../api/novel';
import type { BeliefChannel, BeliefMeta, BookMeta, CharacterCardMeta, CharacterSpec } from '../api/novel';

type Tab = 'basic' | 'belief' | 'memory' | 'relation';

/** 已知角色 id → 立绘（演示场景 betrayal_night 的角色卡），其他角色用首字占位。 */
const CHAR_AVATARS: Record<string, string> = { chenmo: chenImg, liwen: liwImg, zhoushen: zhouImg };

/** ④层角色卡默认空壳（docs/prompt核心设定.md：留空走运行时默认模板）。 */
function emptySpec(name: string): CharacterSpec {
  return {
    id: '', // CharacterCard.id 必填（后端 model_validate_json 校验）；创建后回填真实 id
    name,
    summary: '',
    traits: [],
    voice: '',
    core_beliefs: [],
    dynamic_goals: [],
    bottom_lines: [],
    system_prompt: '',
    think_schema: '',
    decide_schema: '',
    static_world: '',
  };
}

function parseSpec(c: CharacterCardMeta): CharacterSpec {
  try {
    return { ...emptySpec(c.name), ...JSON.parse(c.spec_json || '{}') };
  } catch {
    return emptySpec(c.name);
  }
}

function lines(text: string): string[] {
  return text.split('\n').map((x) => x.trim()).filter(Boolean);
}

const MEM_GROUPS = [
  {
    label: '第 6 章 · 文件异动',
    cards: [
      { type: 'event', tag: '关键事件', title: '发现文件不在原位', desc: '书架第三层的那份手稿，位置偏移了大约两厘米。', time: 'T-06', impact: '高', impCls: 'high' },
      { type: 'dialogue', tag: '对话', title: '李文的解释', desc: '「我找东西的时候碰过一下，可能没放齐。」', time: 'T-06', impact: '中', impCls: 'mid' },
    ],
  },
  {
    label: '第 5 章 · 沉默对峙',
    cards: [
      { type: 'emotion', tag: '情感', title: '李文的眼神', desc: '李文看自己的眼神里，有恐惧，也有某种别的东西。', time: 'T-05', impact: '高', impCls: 'high' },
      { type: 'event', tag: '关键事件', title: '沉默的三分钟', desc: '两人在书房里对视了整整三分钟，谁都没先开口。', time: 'T-05', impact: '中', impCls: 'mid' },
      { type: 'observe', tag: '观察', title: '李文的手指', desc: '李文右手食指有微小的颤抖，但被她很快控制住了。', time: 'T-05', impact: '低', impCls: 'low' },
    ],
  },
  {
    label: '第 4 章 · 保险柜疑云',
    cards: [
      { type: 'event', tag: '关键事件', title: '保险柜门半开', desc: '锁孔周围有新鲜的划痕，像是用错了钥匙。', time: 'T-04', impact: '高', impCls: 'high' },
      { type: 'observe', tag: '观察', title: '周婶的表情', desc: '提到保险柜时，周婶的嘴角有极微的抽动。', time: 'T-04', impact: '中', impCls: 'mid' },
    ],
  },
];

const RELATIONS = [
  { name: '李文', img: liwImg, type: '互相试探', typeCls: 'type-strange', desc: '陈默怀疑李文知道真相，李文则害怕被看穿', pct: 60, label: '张力' },
  { name: '周婶', img: null, ph: '周', type: '主仆', typeCls: 'type-neutral', desc: '表面是管家与雇主的关系，陈默隐约觉得周婶知道更多', pct: 30, label: '张力' },
  { name: '先生（已故）', img: null, ph: '先', type: '敬仰', typeCls: 'type-respect', desc: '陈默的恩师，三年前去世，死因存疑', pct: 85, label: '羁绊' },
  { name: '老赵（警官）', img: null, ph: '警', type: '盟友', typeCls: 'type-allies', desc: '旧识，偶尔提供一些"非正式"的信息', pct: 70, label: '信任' },
];

export function CharactersPage() {
  const [tab, setTab] = useState<Tab>('basic');
  const [beliefFilter, setBeliefFilter] = useState('all');
  const [bookId, setBookId] = useState('');
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [chars, setChars] = useState<CharacterCardMeta[]>([]);
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState<CharacterSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const { showConfirm } = useDialog();
  // ---- 信念账本（v1.5 接真）----
  const [beliefs, setBeliefs] = useState<BeliefMeta[]>([]);
  const [beliefBusy, setBeliefBusy] = useState(false);
  const [editForm, setEditForm] = useState<{
    id?: string; char_id: string; channel: BeliefChannel; text: string; confidence: number;
  } | null>(null);

  const flash = (kind: 'ok' | 'err', text: string) => {
    setMsg({ kind, text });
    window.setTimeout(() => setMsg(null), 4000);
  };

  useEffect(() => {
    listBooks()
      .then((bs) => {
        setBooks(bs);
        if (bs.length) setBookId(bs[0].id);
      })
      .catch((e) => flash('err', `书列表加载失败：${e instanceof Error ? e.message : e}`));
  }, []);

  // 书 → 书级角色库（全书共享，一次编辑到处生效）
  useEffect(() => {
    if (!bookId) {
      setChars([]);
      setSelected('');
      setDraft(null);
      return;
    }
    listBookCharacters(bookId)
      .then((list) => {
        setChars(list);
        setSelected(list[0]?.id ?? '');
        setDraft(list[0] ? parseSpec(list[0]) : null);
      })
      .catch((e) => flash('err', `角色库加载失败：${e instanceof Error ? e.message : e}`));
  }, [bookId]);

  const cur = chars.find((c) => c.id === selected);

  const selectChar = (c: CharacterCardMeta) => {
    setSelected(c.id);
    setTab('basic');
    setDraft(parseSpec(c));
  };

  const patch = (p: Partial<CharacterSpec>) => {
    setDraft((d) => (d ? { ...d, ...p } : d));
  };

  // ---- 信念账本：随 书/选中角色/筛选 加载 ----
  useEffect(() => {
    if (!bookId || !selected) {
      setBeliefs([]);
      return;
    }
    listBookBeliefs(bookId, {
      char_id: selected,
      ...(beliefFilter !== 'all' ? { channel: beliefFilter } : {}),
    })
      .then(setBeliefs)
      .catch((e) => flash('err', `信念加载失败：${e instanceof Error ? e.message : e}`));
  }, [bookId, selected, beliefFilter]);

  const CHANNEL_META: Record<BeliefChannel, { cls: string; tag: string; tagCls: string }> = {
    perceived: { cls: 'witness', tag: '亲见', tagCls: 'tag-witness' },
    told: { cls: 'told', tag: '被告知', tagCls: 'tag-told' },
    inferred: { cls: 'infer', tag: '推测', tagCls: 'tag-infer' },
  };

  const reloadBeliefs = () => {
    if (!bookId || !selected) return Promise.resolve();
    return listBookBeliefs(bookId, {
      char_id: selected,
      ...(beliefFilter !== 'all' ? { channel: beliefFilter } : {}),
    }).then(setBeliefs);
  };

  const submitBelief = () => {
    if (!bookId || !editForm) return;
    const text = editForm.text.trim();
    if (!text) {
      flash('err', '信念内容不能为空');
      return;
    }
    setBeliefBusy(true);
    const body = {
      char_id: editForm.char_id,
      channel: editForm.channel,
      text,
      confidence: Math.max(0, Math.min(1, editForm.confidence)),
    };
    const p = editForm.id
      ? updateBelief(editForm.id, body)
      : createBelief(bookId, body);
    p.then(() => reloadBeliefs())
      .then(() => {
        setEditForm(null);
        flash('ok', editForm.id ? '✓ 信念已更新（手改）' : '✓ 信念已记录（手改）');
      })
      .catch((e) => flash('err', `保存失败：${e instanceof Error ? e.message : e}`))
      .finally(() => setBeliefBusy(false));
  };

  const removeBelief = async (b: BeliefMeta) => {
    const ok = await showConfirm('删除信念', `「${b.text}」将移出该角色的信念账本。`, true);
    if (!ok) return;
    deleteBelief(b.id)
      .then(() => {
        setBeliefs((ls) => ls.filter((x) => x.id !== b.id));
        flash('ok', '已删除信念');
      })
      .catch((e) => flash('err', `删除失败：${e instanceof Error ? e.message : e}`));
  };

  const save = () => {
    if (!selected || !draft) return;
    setBusy(true);
    updateCharacter(selected, { name: draft.name, spec_json: JSON.stringify({ ...draft, id: selected }) })
      .then(() => {
        setChars((cs) => cs.map((c) => (c.id === selected ? { ...c, name: draft.name, spec_json: JSON.stringify({ ...draft, id: selected }) } : c)));
        flash('ok', '✓ 角色卡已保存（落库 scenes.characters.spec_json）');
      })
      .catch((e) => flash('err', `保存失败：${e instanceof Error ? e.message : e}`))
      .finally(() => setBusy(false));
  };

  const addChar = () => {
    if (!bookId) return;
    setBusy(true);
    const s = emptySpec('新角色');
    createBookCharacter(bookId, { name: s.name, spec_json: JSON.stringify(s) })
      .then((r) => {
        const withId: CharacterSpec = { ...s, id: r.id };
        const meta: CharacterCardMeta = { id: r.id, book_id: bookId, name: withId.name, spec_json: JSON.stringify(withId) };
        setChars((cs) => [...cs, meta]);
        setSelected(meta.id);
        setDraft(withId);
        flash('ok', '✓ 已新建角色，编辑后点「保存角色卡」');
      })
      .catch((e) => flash('err', `新建失败：${e instanceof Error ? e.message : e}`))
      .finally(() => setBusy(false));
  };

  const delChar = async () => {
    if (!selected) return;
    const ok = await showConfirm('删除角色卡', '此操作不可撤销。', true);
    if (!ok) return;
    setBusy(true);
    deleteCharacter(selected)
      .then(() => {
        const left = chars.filter((c) => c.id !== selected);
        setChars(left);
        setSelected(left[0]?.id ?? '');
        setDraft(left[0] ? parseSpec(left[0]) : null);
        flash('ok', '已删除角色');
      })
      .catch((e) => flash('err', `删除失败：${e instanceof Error ? e.message : e}`))
      .finally(() => setBusy(false));
  };

  const stats = (c: CharacterCardMeta) => {
    const s = parseSpec(c);
    return `${s.dynamic_goals.length} 目标 · ${s.traits.length} 特质`;
  };

  return (
    <div className="app-shell">
      <Sidebar active="characters" />
      <div className="main-col">
        <header className="top-header">
          <div className="header-left">
            <h1 className="book-title-main">人物管理</h1>
            <span className="genre-tag">{chars.length} 位登场角色</span>
          </div>
          <div className="header-search">
            <span className="search-icon">⌕</span>
            <input type="text" placeholder="搜索角色名、信念、记忆…" />
          </div>
          <div className="header-right">
            <Link to="/director">
              <button className="btn-primary">
                <span>进入导演台</span>
              </button>
            </Link>
            <ThemeToggle />
            <div className="user-avatar">作</div>
          </div>
        </header>

        <div className="char-scope-row">
          <label className="char-scope-label">书</label>
          <select className="char-scope-select" value={bookId} onChange={(e) => setBookId(e.target.value)}>
            {books.length === 0 && <option value="">（暂无书）</option>}
            {books.map((b) => (
              <option key={b.id} value={b.id}>{b.title}</option>
            ))}
          </select>
          <span className="char-scope-label">角色库 · 全书共享</span>
          {msg && <span className={`char-scope-msg ${msg.kind}`}>{msg.text}</span>}
        </div>

        <div className="char-page-body">
          <aside className="char-list-panel">
            <div className="panel-header">
              <span className="panel-title">角色库</span>
              <button className="add-char-btn" onClick={addChar} disabled={!bookId || busy} title="在选中的书新建角色卡（全书共享）">
                <span>+</span>
              </button>
            </div>
            {chars.length === 0 ? (
              <div className="char-empty">该书暂无角色<br />点「+」新建</div>
            ) : (
              <div className="char-list">
                {chars.map((c) => {
                  const avatar = CHAR_AVATARS[c.id] || '';
                  const spec = parseSpec(c);
                  return (
                    <div
                      className={`char-list-item ${selected === c.id ? 'active' : ''}`}
                      data-char={c.id}
                      key={c.id}
                      onClick={() => selectChar(c)}
                    >
                      {avatar ? (
                        <img className="char-list-avatar" src={avatar} alt={c.name} />
                      ) : (
                        <div className="char-list-avatar placeholder">{c.name.slice(0, 1)}</div>
                      )}
                      <div className="char-list-info">
                        <div className="char-list-name">{c.name}</div>
                        <div className="char-list-role">{spec.summary || '未填人设'}</div>
                      </div>
                      <span className="char-list-count">{stats(c)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </aside>

          <main className="char-detail-panel">
            {draft && cur ? (
              <>
                <div className="char-detail-header">
                  <div className="char-portrait-large">
                    {CHAR_AVATARS[cur.id] ? (
                      <img src={CHAR_AVATARS[cur.id]} alt={`${draft.name}立绘`} />
                    ) : (
                      <div className="char-edit-placeholder-avatar">{draft.name.slice(0, 1)}</div>
                    )}
                    <span className="char-role-badge">{draft.dynamic_goals.length} 目标</span>
                  </div>
                  <div className="char-basic-info">
                    <h2 className="char-name-large">{draft.name}</h2>
                    <div className="char-edit-actions">
                      <button className="char-save-btn" onClick={save} disabled={busy}>
                        {busy ? '保存中…' : '保存角色卡'}
                      </button>
                      <button className="char-del-btn" onClick={delChar} disabled={busy}>删除</button>
                    </div>
                    <div className="char-meta-row">
                      <span className="char-meta-item">
                        <strong>身份：</strong>{draft.summary || '未填写'}
                      </span>
                    </div>
                  </div>
                  <div className="char-stats-col">
                    <div className="char-stat">
                      <div className="char-stat-num">{specCount('goal')}</div>
                      <div className="char-stat-label">动态目标</div>
                    </div>
                    <div className="char-stat">
                      <div className="char-stat-num">{specCount('trait')}</div>
                      <div className="char-stat-label">特质</div>
                    </div>
                    <div className="char-stat">
                      <div className="char-stat-num">{specCount('line')}</div>
                      <div className="char-stat-label">底线</div>
                    </div>
                  </div>
                </div>

                <div className="char-tabs">
                  {(
                    [
                      ['basic', '基本设定'],
                      ['belief', '信念账本'],
                      ['memory', '记忆卡片'],
                      ['relation', '关系网'],
                    ] as Array<[Tab, string]>
                  ).map(([k, label]) => (
                    <button className={`char-tab ${tab === k ? 'active' : ''}`} data-tab={k} key={k} onClick={() => setTab(k)}>
                      {label}
                    </button>
                  ))}
                </div>

                <div className="char-tab-content">
                  {tab === 'basic' && (
                    <div className="info-grid">
                      <div className="info-card full-width accent-primary">
                        <div className="info-card-title">角色名</div>
                        <div className="info-card-body">
                          <input className="char-edit-input" value={draft.name} onChange={(e) => patch({ name: e.target.value })} />
                        </div>
                      </div>
                      <div className="info-card full-width accent-blue">
                        <div className="info-card-title">一句话人设（summary）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={2} value={draft.summary} onChange={(e) => patch({ summary: e.target.value })} placeholder="例：沉默的观察者，真相的追寻者" />
                        </div>
                      </div>
                      <div className="info-card full-width accent-cyan">
                        <div className="info-card-title">人格特质（traits，每行一条）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={4} value={draft.traits.join('\n')} onChange={(e) => patch({ traits: lines(e.target.value) })} placeholder={'沉稳\n敏锐\n固执'} />
                        </div>
                      </div>
                      <div className="info-card full-width accent-green">
                        <div className="info-card-title">说话腔调（voice）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={3} value={draft.voice} onChange={(e) => patch({ voice: e.target.value })} placeholder="例：语速偏慢，习惯先停顿再开口" />
                        </div>
                      </div>
                      <div className="info-card full-width accent-gold">
                        <div className="info-card-title">核心信条（core_beliefs，每行一条）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={3} value={draft.core_beliefs.join('\n')} onChange={(e) => patch({ core_beliefs: lines(e.target.value) })} placeholder={'真相可以被逻辑推导\n秩序优于激情'} />
                        </div>
                      </div>
                      <div className="info-card full-width accent-red">
                        <div className="info-card-title">性格底线（bottom_lines，第1层护栏依据，每行一条）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={3} value={draft.bottom_lines.join('\n')} onChange={(e) => patch({ bottom_lines: lines(e.target.value) })} placeholder={'绝不伤害无辜\n绝不背叛信任自己的人'} />
                        </div>
                      </div>
                      <div className="info-card full-width accent-blue">
                        <div className="info-card-title">动态目标（可被导演调权，权重 0-100%）</div>
                        <div className="info-card-body">
                          {draft.dynamic_goals.length === 0 && <div className="char-goals-empty">暂无目标</div>}
                          {draft.dynamic_goals.map((g, i) => (
                            <div className="char-goal-row" key={g.id}>
                              <input className="char-goal-text" value={g.text} onChange={(e) => {
                                const goals = draft.dynamic_goals.map((x, j) => (j === i ? { ...x, text: e.target.value } : x));
                                patch({ dynamic_goals: goals });
                              }} />
                              <input className="char-goal-weight" type="number" min={0} max={1} step={0.1} value={g.weight} onChange={(e) => {
                                const goals = draft.dynamic_goals.map((x, j) => j === i ? { ...x, weight: Math.max(0, Math.min(1, Number(e.target.value) || 0)) } : x);
                                patch({ dynamic_goals: goals });
                              }} />
                              <button className="char-goal-del" onClick={() => patch({ dynamic_goals: draft.dynamic_goals.filter((_, j) => j !== i) })} title="删除该目标">✕</button>
                            </div>
                          ))}
                          <button className="char-goal-add" onClick={() => patch({ dynamic_goals: [...draft.dynamic_goals, { id: `g-${Date.now().toString(36)}`, text: '新目标', weight: 0.5 }] })}>
                            + 添加动态目标
                          </button>
                        </div>
                      </div>
                      <div className="info-card full-width accent-cyan">
                        <div className="info-card-title">④层 演绎 prompt：system_prompt（腔调/决策偏好，可含 {'{self}'} 占位）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input mono" rows={4} value={draft.system_prompt} onChange={(e) => patch({ system_prompt: e.target.value })} placeholder="留空则用内置默认模板；填写后覆盖默认参与感知→思考→决策拼装" />
                        </div>
                      </div>
                      <div className="info-card full-width accent-green">
                        <div className="info-card-title">④层 静态设定 static_world（该角色可读的静态世界/人物关系）</div>
                        <div className="info-card-body">
                          <textarea className="char-edit-input" rows={3} value={draft.static_world} onChange={(e) => patch({ static_world: e.target.value })} placeholder="例：他是恩师已故者留下的管家，知道宅子的大多数秘密" />
                        </div>
                      </div>
                    </div>
                  )}

                  {tab === 'belief' && (
                    <div className="tab-pane active" data-pane="belief">
                      <div className="belief-toolbar">
                        <div className="belief-filter">
                          {(
                            [
                              ['all', '全部'],
                              ['perceived', '亲见'],
                              ['inferred', '推测'],
                              ['told', '被告知'],
                            ] as Array<[string, string]>
                          ).map(([k, label]) => (
                            <span className={`filter-chip ${beliefFilter === k ? 'active' : ''}`} data-filter={k} key={k} onClick={() => setBeliefFilter(k)}>
                              {label}
                            </span>
                          ))}
                        </div>
                        <button
                          className="add-belief-btn"
                          onClick={() =>
                            setEditForm({ char_id: selected, channel: 'perceived', text: '', confidence: 0.6 })
                          }
                          disabled={!selected || beliefBusy}
                        >
                          <span>+</span> 新增信念
                        </button>
                      </div>

                      {editForm && (
                        <div className="belief-card">
                          <div className="belief-card-head">
                            <span className="belief-tag tag-witness">手写</span>
                            <span className="belief-source">{editForm.id ? '编辑中' : '新信念'}</span>
                          </div>
                          <div className="belief-edit-form">
                            <textarea
                              className="char-edit-input"
                              rows={2}
                              value={editForm.text}
                              onChange={(e) => setEditForm({ ...editForm, text: e.target.value })}
                              placeholder="信念内容……（例：保险柜里装着先生的研究手稿）"
                            />
                            <div className="belief-edit-row">
                              <select
                                className="char-scope-select"
                                value={editForm.channel}
                                onChange={(e) => setEditForm({ ...editForm, channel: e.target.value as BeliefChannel })}
                              >
                                <option value="perceived">亲见</option>
                                <option value="told">被告知</option>
                                <option value="inferred">推测</option>
                              </select>
                              <label className="belief-edit-conf">
                                确信度
                                <input
                                  className="char-goal-weight"
                                  type="number" min={0} max={1} step={0.1}
                                  value={editForm.confidence}
                                  onChange={(e) => setEditForm({ ...editForm, confidence: Number(e.target.value) || 0 })}
                                />
                              </label>
                            </div>
                          </div>
                          <div className="belief-card-foot">
                            <span className="belief-certainty">手改后与推演自动沉淀共用一个账本</span>
                            <div className="belief-actions">
                              <button className="belief-action" onClick={submitBelief} disabled={beliefBusy}>
                                {beliefBusy ? '保存中…' : '保存'}
                              </button>
                              <button className="belief-action" onClick={() => setEditForm(null)}>取消</button>
                            </div>
                          </div>
                        </div>
                      )}

                      <div className="belief-list">
                        {beliefs.length === 0 ? (
                          <div className="char-empty">该角色暂无信念<br />推演中会实时沉淀，也可点「新增信念」手写</div>
                        ) : (
                          beliefs.map((b) => {
                            const meta = CHANNEL_META[b.channel] ?? CHANNEL_META.perceived;
                            const editing = editForm?.id === b.id;
                            return (
                              <div className={`belief-card ${meta.cls}`} key={b.id}>
                                <div className="belief-card-head">
                                  <span className={`belief-tag ${meta.tagCls}`}>{meta.tag}</span>
                                  <span className="belief-source">
                                    {b.fact_id ? `事实 ${b.fact_id}` : '手写'} · {b.source_event_id || 'AUTHOR'}
                                    {b.edited && <em className="belief-hand"> 手改</em>}
                                  </span>
                                </div>
                                {editing ? (
                                  <div className="belief-edit-form">
                                    <textarea
                                      className="char-edit-input"
                                      rows={2}
                                      value={editForm.text}
                                      onChange={(e) => setEditForm({ ...editForm, text: e.target.value })}
                                    />
                                    <div className="belief-edit-row">
                                      <select
                                        className="char-scope-select"
                                        value={editForm.channel}
                                        onChange={(e) => setEditForm({ ...editForm, channel: e.target.value as BeliefChannel })}
                                      >
                                        <option value="perceived">亲见</option>
                                        <option value="told">被告知</option>
                                        <option value="inferred">推测</option>
                                      </select>
                                      <label className="belief-edit-conf">
                                        确信度
                                        <input
                                          className="char-goal-weight"
                                          type="number" min={0} max={1} step={0.1}
                                          value={editForm.confidence}
                                          onChange={(e) => setEditForm({ ...editForm, confidence: Number(e.target.value) || 0 })}
                                        />
                                      </label>
                                    </div>
                                  </div>
                                ) : (
                                  <div className="belief-text">{b.text}</div>
                                )}
                                <div className="belief-card-foot">
                                  <span className="belief-certainty">确信度 {Math.round(b.confidence * 100)}%</span>
                                  <div className="belief-actions">
                                    {editing ? (
                                      <>
                                        <button className="belief-action" onClick={submitBelief} disabled={beliefBusy}>
                                          {beliefBusy ? '保存中…' : '保存'}
                                        </button>
                                        <button className="belief-action" onClick={() => setEditForm(null)}>取消</button>
                                      </>
                                    ) : (
                                      <>
                                        <button
                                          className="belief-action"
                                          onClick={() => setEditForm({
                                            id: b.id, char_id: b.char_id,
                                            channel: b.channel, text: b.text, confidence: b.confidence,
                                          })}
                                        >
                                          编辑
                                        </button>
                                        <button className="belief-action danger" onClick={() => removeBelief(b)}>删除</button>
                                      </>
                                    )}
                                  </div>
                                </div>
                              </div>
                            );
                          })
                        )}
                      </div>
                    </div>
                  )}

                  {tab === 'memory' && (
                    <div className="tab-pane active" data-pane="memory">
                      <div className="memory-toolbar">
                        <div className="memory-filter">
                          <span className="filter-chip active">全部 28</span>
                          <span className="filter-chip">关键事件 8</span>
                          <span className="filter-chip">对话 12</span>
                          <span className="filter-chip">观察 5</span>
                          <span className="filter-chip">情感 3</span>
                        </div>
                        <button className="add-memory-btn">
                          <span>+</span> 新增记忆
                        </button>
                      </div>
                      <div className="memory-timeline">
                        {MEM_GROUPS.map((g, i) => (
                          <div className="mem-group" key={i}>
                            <div className="mem-group-label">{g.label}</div>
                            <div className="mem-cards-row">
                              {g.cards.map((c, j) => (
                                <div className={`mem-card type-${c.type}`} key={j}>
                                  <div className="mem-card-tag">{c.tag}</div>
                                  <div className="mem-card-title">{c.title}</div>
                                  <div className="mem-card-desc">{c.desc}</div>
                                  <div className="mem-card-foot">
                                    <span className="mem-time">{c.time}</span>
                                    <span className={`mem-impact ${c.impCls}`}>影响：{c.impact}</span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="char-subtle-bar">记忆卡片由推演事件沉淀（关键事件/对话/观察），RAG 检索二阶段接入；当前为演示样例。</div>
                    </div>
                  )}

                  {tab === 'relation' && (
                    <div className="tab-pane active" data-pane="relation">
                      <div className="relation-layout">
                        <div className="relation-list-col">
                          <div className="section-subtitle">{draft.name}的关系</div>
                          <div className="relation-list">
                            {RELATIONS.map((r, i) => (
                              <div className="relation-item" key={i}>
                                {r.img ? (
                                  <img className="relation-avatar" src={r.img} alt={r.name} />
                                ) : (
                                  <div className="relation-avatar placeholder">{r.ph}</div>
                                )}
                                <div className="relation-info">
                                  <div className="relation-name">{r.name}</div>
                                  <div className={`relation-type ${r.typeCls}`}>
                                    <span className="rel-dot"></span> {r.type}
                                  </div>
                                  <div className="relation-desc">{r.desc}</div>
                                </div>
                                <div className="relation-strength">
                                  <div className="strength-bar">
                                    <div className="strength-fill" style={{ width: `${r.pct}%` }}></div>
                                  </div>
                                  <span className="strength-label">{r.label} {r.pct}%</span>
                                </div>
                              </div>
                            ))}
                          </div>
                          <button className="add-relation-btn">
                            <span>+</span> 添加关系
                          </button>
                        </div>
                        <div className="relation-viz-col">
                          <div className="section-subtitle">关系示意</div>
                          <div className="relation-graph">
                            <div className="graph-center">
                              <div className="graph-node center">
                                <img src={chenImg} alt="陈默" />
                                <span>陈默</span>
                              </div>
                            </div>
                            <div className="graph-node top">
                              <img src={liwImg} alt="李文" />
                              <span>李文</span>
                              <span className="graph-edge edge-strange">互相试探</span>
                            </div>
                            <div className="graph-node right">
                              <div className="graph-avatar placeholder">周</div>
                              <span>周婶</span>
                              <span className="graph-edge edge-neutral">主仆</span>
                            </div>
                            <div className="graph-node bottom-left">
                              <div className="graph-avatar placeholder">先</div>
                              <span>先生</span>
                              <span className="graph-edge edge-respect">敬仰</span>
                            </div>
                            <div className="graph-node bottom-right">
                              <div className="graph-avatar placeholder">警</div>
                              <span>老赵</span>
                              <span className="graph-edge edge-allies">盟友</span>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div className="char-subtle-bar">关系网由信念账本与事件归因推导，当前为演示样例。</div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="char-empty detail">选择书以加载角色库；没有角色时点「+」新建。</div>
            )}
          </main>
        </div>
      </div>
    </div>
  );

  /** 统计草稿中的字段数（供右侧数字栏展示，避免额外状态）。 */
  function specCount(kind: 'goal' | 'trait' | 'line'): number {
    if (!draft) return 0;
    if (kind === 'goal') return draft.dynamic_goals.length;
    if (kind === 'trait') return draft.traits.length;
    return draft.bottom_lines.length;
  }
}