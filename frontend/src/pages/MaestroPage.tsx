import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import { API_BASE } from '../types/types';
import {
  listBooks,
  fetchBookTree,
  listInspirations,
  createInspiration,
  generateInspirations,
  setInspirationAdopted,
  chiefChat,
} from '../api/novel';
import type { BookMeta, BookTree, InspirationCard } from '../api/novel';

/* ---------- 类型 ---------- */

interface MiniChapter {
  num: string;
  title: string;
}

interface ChatMsg {
  sender: 'user' | 'agent';
  text: string;
  toolCall?: string | null;
  miniOutline?: MiniChapter[] | null;
}

interface PlanPreview {
  worldview?: { premise?: string; rules_text?: string; background?: string };
  chapters?: Array<{ title?: string; tone?: string; scenes?: unknown[] }>;
  foreshadow_plan?: unknown[];
  ending_options?: string[];
}

/* 后端灵感卡 type → 前端 css class 映射（对齐 maestro.css .card-icon.xxx） */
const typeClassOf = (t: string): 'plot' | 'character' | 'world' =>
  t === 'character' || t === 'world' ? t : 'plot';

const typeLabel = (t: string): string =>
  t === 'character' ? '人物' : t === 'world' ? '世界观' : '剧情';

/* ---------- 初始数据（mock 回退：API 不可用时降级，不白屏） ---------- */

const INIT_MESSAGES: ChatMsg[] = [
  { sender: 'user', text: '我想写一个废材复仇的玄幻小说，主角被宗门背叛，最后发现妹妹是神族钥匙。' },
  {
    sender: 'agent',
    text: '已理解你的方向。我建议把主线拆成「玄脉被夺→古玉残魂→重返宗门→妹妹的踪迹」四个章节，先建立复仇动机，再逐步揭开世界观。',
    toolCall: null,
    miniOutline: [
      { num: '第一章', title: '玄脉被夺' },
      { num: '第二章', title: '古玉残魂' },
      { num: '第三章', title: '重返宗门' },
      { num: '第四章', title: '妹妹的踪迹' },
    ],
  },
  { sender: 'agent', text: '我刚用工具生成了 5 张灵感卡，其中「双生妹妹」和「血脉真相」可以作为贯穿全书的伏笔，你觉得如何？', toolCall: '🔧 已生成 5 张灵感卡' },
];

const TENSION = { chapters: ['开篇', '上升', '转折', '高潮', '回落', '结局'], tension: [12, 28, 55, 92, 48, 20] };

const zoneCls = (badge?: string) => {
  if (!badge) return 'action';
  const b = badge.toLowerCase();
  if (b.includes('悬念') || b.includes('悬')) return 'tension';
  if (b.includes('揭秘')) return 'reveal';
  return 'action';
};

/* ---------- 页面 ---------- */

export function MaestroPage() {
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState('');
  const [tree, setTree] = useState<BookTree | null>(null);

  const [direction, setDirection] = useState('废材少年因血脉被夺，发誓重返宗门讨回公道');
  const [directionOpen, setDirectionOpen] = useState(false);
  const [inspirations, setInspirations] = useState<InspirationCard[]>([]);
  const [messages, setMessages] = useState<ChatMsg[]>(INIT_MESSAGES);
  const [chatInput, setChatInput] = useState('');
  const [showSkeleton, setShowSkeleton] = useState(true);
  const [agentState, setAgentState] = useState<'idle' | 'thinking' | 'tool'>('idle');
  const [ideating, setIdeating] = useState(false);

  const [outlineExpanded, setOutlineExpanded] = useState<Record<string, boolean>>({});
  const [activeChapter, setActiveChapter] = useState<string | null>(null);

  // 主笔骨架（来自 plan API 的预览）
  const [planPreview, setPlanPreview] = useState<PlanPreview | null>(null);
  const [committing, setCommitting] = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const statusText = agentState === 'idle' ? '主笔空闲中' : agentState === 'thinking' ? '主笔思考中' : '调用工具中';

  /* ---------- 数据加载 ---------- */
  useEffect(() => {
    void (async () => {
      try {
        const bs = await listBooks();
        setBooks(bs);
        if (bs.length > 0) setBookId(bs[0].id);
      } catch {
        /* 后端不可用时保持演示数据 */
      }
    })();
  }, []);

  useEffect(() => {
    if (!bookId) {
      setTree(null);
      setInspirations([]);
      return;
    }
    void (async () => {
      try {
        const t = await fetchBookTree(bookId);
        setTree(t);
      } catch {
        setTree(null);
      }
      try {
        const cards = await listInspirations(bookId);
        if (cards.length > 0) setInspirations(cards);
      } catch {
        /* API 失败保留现有数据（降级） */
      }
    })();
  }, [bookId]);

  /* ---------- 张力曲线 ---------- */
  // 从 CSS 变量读主题色，保证纸/墨模式下曲线都协调
  const cssVar = useCallback((name: string, fallback: string) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }, []);

  const drawTension = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const pad = { top: 18, bottom: 28, left: 24, right: 24 };
    const { chapters, tension } = TENSION;

    const cGrid = cssVar('--maestro-canvas-grid', 'rgba(212, 168, 83, 0.08)');
    const cRed = cssVar('--maestro-red', '#c96b6b');
    const cGold = cssVar('--maestro-gold', '#d4a853');
    const cText2 = cssVar('--maestro-text-2', '#b8b2a6');
    const cDotStroke = cssVar('--maestro-canvas-dot-stroke', 'rgba(13, 17, 23, 0.9)');

    ctx.clearRect(0, 0, w, h);

    // 网格
    ctx.strokeStyle = cGrid;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + ((h - pad.top - pad.bottom) * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(w - pad.right, y);
      ctx.stroke();
    }

    const points = chapters.map((_, i) => ({
      x: pad.left + ((w - pad.left - pad.right) * i) / (chapters.length - 1),
      y: pad.top + (h - pad.top - pad.bottom) * (1 - tension[i] / 100),
    }));

    // 曲线
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 0; i < points.length - 1; i++) {
      const cp1 = { x: (points[i].x + points[i + 1].x) / 2, y: points[i].y };
      const cp2 = { x: (points[i].x + points[i + 1].x) / 2, y: points[i + 1].y };
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, points[i + 1].x, points[i + 1].y);
    }
    ctx.strokeStyle = cRed;
    ctx.lineWidth = 2.5;
    ctx.stroke();

    points.forEach((p, i) => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = i === 3 ? cGold : cRed;
      ctx.fill();
      ctx.strokeStyle = cDotStroke;
      ctx.lineWidth = 2;
      ctx.stroke();

      ctx.fillStyle = cText2;
      ctx.font = '11px "Noto Sans SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(chapters[i], p.x, h - 10);
    });
  }, [cssVar]);

  useEffect(() => {
    drawTension();
  }, [drawTension, drawerOpen]);

  // 主题切换（data-theme 变化）时重绘张力曲线
  useEffect(() => {
    const observer = new MutationObserver(() => drawTension());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, [drawTension]);

  useEffect(() => {
    window.addEventListener('resize', drawTension);
    return () => window.removeEventListener('resize', drawTension);
  }, [drawTension]);

  /* ---------- 交互 ---------- */
  const toggleAdopt = async (card: InspirationCard) => {
    const next = !card.adopted;
    // 乐观更新，失败回滚
    setInspirations((prev) => prev.map((c) => (c.id === card.id ? { ...c, adopted: next } : c)));
    try {
      await setInspirationAdopted(card.id, next);
    } catch {
      setInspirations((prev) => prev.map((c) => (c.id === card.id ? { ...c, adopted: !next } : c)));
    }
  };

  const addCard = async () => {
    if (!bookId) return;
    const title = window.prompt('输入灵感标题');
    if (!title) return;
    try {
      const card = await createInspiration(bookId, {
        title,
        desc: '作者自建灵感卡，可后续补充细节或让主笔扩展。',
        icon: '✏️',
        type: 'plot',
      });
      setInspirations((prev) => [card, ...prev]);
    } catch {
      /* 后端不可用：降级本地 */
      setInspirations((prev) => [
        { id: `insp-local-${Date.now()}`, book_id: bookId, icon: '✏️', title, desc: '作者自建灵感卡（本地降级，未落库）。', type: 'plot', source: 'author', adopted: false, sort_order: 0 },
        ...prev,
      ]);
    }
  };

  const sendMessage = async () => {
    const text = chatInput.trim();
    if (!text) return;
    setMessages((prev) => [...prev, { sender: 'user', text }]);
    setChatInput('');
    // 预占一个 agent 气泡用于流式渲染
    setMessages((prev) => [...prev, { sender: 'agent', text: '' }]);
    setAgentState('thinking');
    const history = [...messages.filter((m) => m.text), { sender: 'user' as const, text }];
    try {
      let acc = '';
      await chiefChat(
        bookId,
        history.map((m) => ({ role: m.sender === 'user' ? 'user' as const : 'assistant' as const, content: m.text })),
        (delta) => {
          acc += delta;
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { sender: 'agent', text: acc };
            return next;
          });
        },
      );
    } catch {
      // 后端不可用：降级本地回复
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { sender: 'agent', text: '收到，我会根据这个方向调整骨架。如果你希望我把某个灵感卡直接落地成章节场景，点击卡片上的「采纳」即可。' };
        return next;
      });
    } finally {
      setAgentState('idle');
    }
  };

  // 让主笔构思：生成灵感卡 + 骨架预览（真实 API，失败降级）
  const runIdeation = async () => {
    if (ideating) return;
    setIdeating(true);
    setAgentState('thinking');
    setPlanPreview(null);

    // 1) 生成灵感卡（后端 generate 接口，LLM/模板均可）
    if (bookId) {
      try {
        const { cards } = await generateInspirations(bookId, direction);
        if (cards.length > 0) setInspirations((prev) => [...cards, ...prev]);
      } catch {
        /* 忽略：继续骨架 */
      }
    }

    // 2) 骨架预览（真实 plan API，不可用时降级）
    const useAPI = !!(tree && bookId);
    if (useAPI) {
      try {
        const adoptedIds = inspirations.filter((c) => c.adopted).map((c) => c.id);
        const res = await fetch(`${API_BASE}/api/v1/books/${bookId}/plan`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ direction, inspiration_ids: adoptedIds }),
        });
        if (res.ok) {
          const data = (await res.json()) as { plan: PlanPreview };
          setPlanPreview(data.plan);
          setAgentState('tool');
          setMessages((prev) => [
            ...prev,
            {
              sender: 'agent',
              text: '我用工具产出了新的骨架预览：世界观、章节与伏笔计划已经就绪，确认后即可落库。',
              toolCall: '🔧 已生成书籍骨架',
              miniOutline: (data.plan.chapters ?? []).map((c, i) => ({ num: `第${i + 1}章`, title: c.title ?? '' })),
            },
          ]);
          setTimeout(() => {
            setAgentState('idle');
            setIdeating(false);
          }, 600);
          return;
        }
      } catch {
        /* fall through 到模拟 */
      }
    }

    // 演示回退：新增灵感卡 + 对话
    setTimeout(() => {
      setAgentState('tool');
      setMessages((prev) => [
        ...prev,
        { sender: 'agent', text: '基于你的方向我已经开始构思，请稍后查看灵感池与骨架预览。', toolCall: '🔧 已更新灵感池' },
      ]);
      setTimeout(() => {
        setAgentState('idle');
        setIdeating(false);
      }, 400);
    }, 1200);
  };

  const commitPlan = async () => {
    if (!tree || !planPreview || committing) return;
    setCommitting(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/books/${tree.id}/plan/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: planPreview }),
      });
      if (res.ok) {
        setPlanPreview(null);
        const t = await fetchBookTree(tree.id);
        setTree(t);
      }
    } catch {
      /* ignore */
    } finally {
      setCommitting(false);
    }
  };

  const toggleChapter = (id: string) => {
    setOutlineExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const adoptable = inspirations.filter((c) => !c.adopted);
  const adopted = inspirations.filter((c) => c.adopted);

  return (
    <div className="app-shell">
      <Sidebar active="maestro" />
      <div className="main-col">
        <div className="maestro-workbench">
          {/* 顶栏 */}
          <header className="maestro-topbar">
            <div className="topbar-left">
              <span className="brand-seal">墨</span>
              <div className="book-meta">
                <div className="book-title">
                  {tree ? tree.title : books.find((b) => b.id === bookId)?.title ?? '本书骨架'}
                </div>
                <div className="genre-tags">
                  {(books.length > 1 ? books : []).slice(0, 3).map((b) => (
                    <span className="genre-tag" key={b.id}>{b.genre || '玄幻'}</span>
                  ))}
                </div>
              </div>
            </div>

            <div className="topbar-center">
              <div className="direction-input-group">
                <input
                  type="text"
                  className="direction-input"
                  value={direction}
                  placeholder="一句话方向：废材少年因血脉被夺，发誓重返宗门讨回公道……"
                  onChange={(e) => setDirection(e.target.value)}
                />
                <button className="direction-expand" title="展开完整方向" onClick={() => setDirectionOpen(!directionOpen)}>
                  {directionOpen ? '▴' : '▾'}
                </button>
              </div>
              <button className="btn-ideate" onClick={() => void runIdeation()} disabled={ideating}>
                <span className="btn-ico">✦</span>
                <span>{ideating ? '主笔构思中…' : '让主笔构思'}</span>
              </button>
            </div>

            <div className="topbar-right">
              <div className="agent-status" data-state={agentState}>
                <span className="status-dot"></span>
                <span className="status-text">{statusText}</span>
              </div>
              <ThemeToggle />
            </div>
          </header>

          {/* 方向展开面板 */}
          <div className={`direction-panel ${directionOpen ? 'open' : ''}`}>
            <div className="direction-fields">
              <label className="field-row">
                <span className="field-label">世界观基调</span>
                <input type="text" value="东方玄幻，强者为尊，血脉决定修行上限" readOnly />
              </label>
              <label className="field-row">
                <span className="field-label">核心冲突</span>
                <input type="text" value="主角被至亲背叛夺走血脉，必须在三年内夺回复仇" readOnly />
              </label>
              <label className="field-row">
                <span className="field-label">主角目标</span>
                <input type="text" value="重回宗门，击败圣子，找回失踪的妹妹" readOnly />
              </label>
            </div>
          </div>

          {/* 三栏主体 */}
          <main className="maestro-body">
            {/* 左栏：灵感池 */}
            <aside className="panel inspiration-panel">
              <div className="panel-header">
                <h2 className="panel-title">
                  <span className="title-ico">◈</span>
                  灵感池
                  <span className="panel-count">{adoptable.length}</span>
                </h2>
                <button className="btn-icon" title="自建灵感卡" onClick={addCard}>+</button>
              </div>
              <div className="inspiration-list">
                {adoptable.length === 0 && (
                  <div style={{ color: 'var(--maestro-text-3)', fontSize: 12, padding: '8px 0' }}>
                    暂无灵感卡。点「+」自建，或在上方输入方向后点「让主笔构思」。
                  </div>
                )}
                {adoptable.map((card) => (
                  <div className="inspiration-card" key={card.id}>
                    <div className="card-header">
                      <div className={`card-icon ${typeClassOf(card.type)}`}>{card.icon}</div>
                      <div className="card-title-wrap">
                        <h4 className="card-title">{card.title}</h4>
                        <p className="card-desc">{card.desc}</p>
                      </div>
                    </div>
                    <div className="card-footer">
                      <span className="type-tag">{typeLabel(card.type)}</span>
                      <button className="btn-adopt" onClick={() => void toggleAdopt(card)}>采纳</button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="adopted-header">已采纳</div>
              <div className="adopted-list">
                {adopted.length === 0 ? (
                  <div style={{ color: 'var(--maestro-text-3)', fontSize: 12, padding: '8px 0' }}>暂无已采纳灵感</div>
                ) : (
                  adopted.map((card) => (
                    <div className="inspiration-card adopted" key={card.id}>
                      <div className="card-header">
                        <div className={`card-icon ${typeClassOf(card.type)}`}>{card.icon}</div>
                        <div className="card-title-wrap">
                          <h4 className="card-title">{card.title}</h4>
                          <p className="card-desc">{card.desc}</p>
                        </div>
                      </div>
                      <div className="card-footer">
                        <span className="type-tag">{typeLabel(card.type)}</span>
                        <button className="btn-adopt" onClick={() => void toggleAdopt(card)}>已采纳</button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </aside>

            {/* 中栏：书籍骨架树 */}
            <section className="panel outline-panel">
              <div className="panel-header">
                <h2 className="panel-title">
                  <span className="title-ico">▣</span>
                  书籍骨架
                </h2>
                <div className="outline-actions">
                  <button
                    className="btn-text"
                    onClick={() =>
                      setOutlineExpanded(Object.fromEntries((tree?.chapters ?? []).map((c) => [c.id, true])))
                    }
                  >
                    全部展开
                  </button>
                  <button className="btn-text" onClick={() => setOutlineExpanded({})}>全部折叠</button>
                  <Link to="/planning" className="btn-text">完整编辑</Link>
                </div>
              </div>

              {/* 主笔骨架预览（plan API 结果） */}
              {planPreview && (
                <div className="inspiration-card" style={{ margin: '12px 16px 0' }}>
                  <div className="card-header">
                    <div className="card-icon world">📋</div>
                    <div className="card-title-wrap">
                      <h4 className="card-title">主笔骨架预览</h4>
                      <p className="card-desc">
                        {planPreview.worldview?.premise || '世界观待确认'} · {planPreview.chapters?.length ?? 0} 章 · {planPreview.foreshadow_plan?.length ?? 0} 伏笔
                      </p>
                    </div>
                  </div>
                  <div className="card-footer">
                    <span className="type-tag">待落库</span>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn-adopt" onClick={() => setPlanPreview(null)}>✗ 重排</button>
                      <button className="btn-adopt" onClick={() => void commitPlan()} disabled={committing}>
                        {committing ? '落库中…' : '✓ 确认落库'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <div className="outline-tree">
                {!tree && (
                  <div style={{ color: 'var(--maestro-text-3)', fontSize: 13, padding: '16px 4px' }}>
                    暂无书籍骨架。先选一本书，或在上方输入方向后点「让主笔构思」。
                  </div>
                )}
                {(tree?.chapters ?? []).map((ch) => {
                  const expanded = outlineExpanded[ch.id] ?? true;
                  return (
                    <div className="outline-chapter" key={ch.id}>
                      <div
                        className={`outline-row chapter ${activeChapter === ch.id ? 'active' : ''}`}
                        onClick={() => setActiveChapter(ch.id)}
                      >
                        <span className="row-toggle" onClick={(e) => { e.stopPropagation(); toggleChapter(ch.id); }}>
                          {expanded ? '▾' : '▸'}
                        </span>
                        <span className="row-number">{ch.order_no}</span>
                        <span className="row-title">{ch.title}</span>
                        <span className={`row-badge ${zoneCls(ch.tone)}`}>{ch.tone || '动作'}</span>
                        <span className="row-wordcount">{ch.word_target || 0}字</span>
                      </div>
                      <div className={`outline-children ${expanded ? '' : 'collapsed'}`}>
                        {(ch.scenes ?? []).map((s) => (
                          <div className="outline-row scene" key={s.id}>
                            <span className="row-title">{s.title}</span>
                            <div className="scene-meta">
                              <span className="scene-stage">舞台 {s.stage_desc || '未布置'}</span>
                              <div className="scene-avatars">
                                {(s.characters ?? []).slice(0, 3).map((c, i) => (
                                  <span className="avatar-dot" key={i}>{c.name?.[0] ?? '?'}</span>
                                ))}
                              </div>
                              <span className="scene-goal">{s.scene_summary || s.title}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* 右栏：主笔共创对话 */}
            <aside className="panel chat-panel">
              <div className="panel-header">
                <h2 className="panel-title">
                  <span className="title-ico">✦</span>
                  主笔共创
                </h2>
                <label className="toggle-skeleton">
                  <input type="checkbox" checked={showSkeleton} onChange={(e) => setShowSkeleton(e.target.checked)} />
                  <span>查看骨架预览</span>
                </label>
              </div>
              <div className="chat-messages">
                {messages.map((msg, i) => {
                  const isLast = i === messages.length - 1;
                  return (
                    <div className={`chat-bubble ${msg.sender}`} key={i}>
                      <div className="bubble-sender">{msg.sender === 'user' ? '你' : '主笔 Agent'}</div>
                      <p className="bubble-text">
                        {msg.text}
                        {isLast && msg.sender === 'agent' && <span className="cursor"></span>}
                      </p>
                      {msg.toolCall && <div className="tool-call">{msg.toolCall}</div>}
                      {showSkeleton && msg.miniOutline && (
                        <div className="mini-outline">
                          <div className="mini-outline-title">骨架预览</div>
                          {msg.miniOutline.map((c, j) => (
                            <div className="mini-chapter" key={j}>
                              <span className="num">{c.num}</span>
                              <span>{c.title}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="chat-input-area">
                <div className="chat-input-wrap">
                  <input
                    type="text"
                    className="chat-input"
                    placeholder="补充方向或让主笔调整……"
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') sendMessage(); }}
                  />
                  <button className="btn-send" onClick={sendMessage}>发送</button>
                </div>
              </div>
            </aside>
          </main>

          {/* 底部张力曲线 */}
          <div className={`tension-drawer ${drawerOpen ? '' : 'collapsed'}`}>
            <div className="drawer-handle" onClick={() => setDrawerOpen(!drawerOpen)}>
              <span className="handle-title">
                <span className="title-ico">〰</span>
                全书张力曲线
              </span>
              <button className="btn-icon-sm" title="折叠/展开">{drawerOpen ? '▾' : '▴'}</button>
            </div>
            <div className="drawer-body">
              <div className="tension-canvas-wrap">
                <canvas ref={canvasRef} width="1200" height="90"></canvas>
              </div>
              <div className="tension-legend">
                <span className="legend-item"><i className="dot planted"></i> 伏笔埋设</span>
                <span className="legend-item"><i className="dot resolved"></i> 伏笔回收</span>
                <span className="legend-item"><i className="line"></i> 情绪张力</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}