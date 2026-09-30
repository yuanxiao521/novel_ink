import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import { useDialog } from '../components/common/Dialog';
import { API_BASE } from '../types/types';
import {
  listBooks,
  fetchBookTree,
  listInspirations,
  createBook,
  createInspiration,
  generateInspirations,
  createChapter,
  updateChapter,
  deleteChapter,
  createScene,
  updateScene,
  deleteScene,
  replaceChapterScenes,
  setInspirationAdopted,
  updateInspiration,
  chiefChat,
  listChatHistory,
  listMemories,
  createMemory,
  updateMemory,
  deleteMemory,
} from '../api/novel';
import type { BookMeta, BookTree, BookMemoryMeta, InspirationCard, MemoryTopic, SceneMeta, ScenePatchItem } from '../api/novel';

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

/* 书级记忆 topic → 中文标签（主笔 · 记忆域） */
const MEMORY_TOPIC_LABEL: Record<string, string> = {
  direction: '方向',
  setting: '设定',
  constraint: '约束',
  history: '历史',
  preference: '偏好',
};

const MEMORY_TOPIC_OPTIONS: Array<[string, string]> = [
  ['direction', '方向'],
  ['setting', '设定'],
  ['constraint', '约束'],
  ['history', '历史'],
  ['preference', '偏好'],
];

/* ---------- 常量 ---------- */

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
  const { showPrompt, showConfirm } = useDialog();
  const nav = useNavigate();
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState('');
  const [tree, setTree] = useState<BookTree | null>(null);

  // —— 结构编辑（原规划页功能并入：选节点 → 编辑/增删）——
  const [selKind, setSelKind] = useState<'book' | 'chapter' | 'scene' | null>(null);
  const [selId, setSelId] = useState('');
  // 编辑草稿（选中章/场景的字段）：编辑完成后点「保存」落库
  const [draft, setDraft] = useState<Record<string, string | number> | null>(null);
  const [flash, setFlash] = useState('');

  const [direction, setDirection] = useState('废材少年因血脉被夺，发誓重返宗门讨回公道');
  const [directionOpen, setDirectionOpen] = useState(false);
  const [inspirations, setInspirations] = useState<InspirationCard[]>([]);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
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

  // 灵感卡编辑（内联表单）
  const [cardEditing, setCardEditing] = useState<string | null>(null);
  const [cardDraft, setCardDraft] = useState<{ title: string; desc: string; type: 'plot' | 'character' | 'world' }>({ title: '', desc: '', type: 'plot' });

  // 书级记忆（主笔 · 记忆域）：列表 + 添加/编辑表单
  const [memories, setMemories] = useState<BookMemoryMeta[]>([]);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memoryFormOpen, setMemoryFormOpen] = useState(false);
  const [memoryEditingId, setMemoryEditingId] = useState<string | null>(null);
  const [memoryDraftTopic, setMemoryDraftTopic] = useState<MemoryTopic>('constraint');
  const [memoryDraftContent, setMemoryDraftContent] = useState('');

  // 章节场景编辑器（主笔升级 · 场景级部分修改）：整章场景列表内联编辑 → 批量保存
  const [sceneEditorOpen, setSceneEditorOpen] = useState(false);
  const [sceneEdits, setSceneEdits] = useState<ScenePatchItem[]>([]);
  const [sceneSaving, setSceneSaving] = useState(false);

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
      setMemories([]);
      setMessages([]);
      return;
    }
    // 切换书时清空对话，加载该书历史
    setMessages([]);
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
      try {
        setMemories(await listMemories(bookId));
      } catch {
        /* API 失败保留现有数据（降级） */
      }
      // 加载该书对话历史
      try {
        const history = await listChatHistory(bookId);
        if (history.length > 0) {
          setMessages(history.map((h) => ({
            sender: h.role === 'user' ? 'user' as const : 'agent' as const,
            text: h.content,
          })));
        }
      } catch {
        /* 无历史或 API 失败，保持空白 */
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

  const startEditCard = (card: InspirationCard) => {
    setCardEditing(card.id);
    setCardDraft({ title: card.title, desc: card.desc, type: card.type });
  };

  const saveCardEdit = async (card: InspirationCard) => {
    if (!cardDraft.title.trim()) return;
    try {
      await updateInspiration(card.id, cardDraft);
      setInspirations((prev) => prev.map((c) => (c.id === card.id ? { ...c, ...cardDraft } : c)));
      setCardEditing(null);
    } catch (e) {
      console.warn('[灵感] 编辑失败：', e);
    }
  };

  const addCard = async () => {
    if (!bookId) return;
    const title = await showPrompt('输入灵感标题');
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

  /* ---------- 书级记忆（主笔 · 记忆域） ---------- */
  const loadMemories = async () => {
    if (!bookId) return;
    try { setMemories(await listMemories(bookId)); } catch { /* 降级保留 */ }
  };
  const startAddMemory = () => {
    setMemoryEditingId(null);
    setMemoryDraftTopic('constraint');
    setMemoryDraftContent('');
    setMemoryFormOpen(true);
  };
  const startEditMemory = (m: BookMemoryMeta) => {
    setMemoryEditingId(m.id);
    setMemoryDraftTopic(m.topic);
    setMemoryDraftContent(m.content);
    setMemoryFormOpen(true);
  };
  const saveMemory = async () => {
    const content = memoryDraftContent.trim();
    if (!content) return;
    try {
      if (memoryEditingId) {
        await updateMemory(memoryEditingId, { topic: memoryDraftTopic, content });
      } else {
        await createMemory(bookId, { topic: memoryDraftTopic, content });
      }
      setMemoryFormOpen(false);
      await loadMemories();
      briefFlash('记忆已保存');
    } catch (e) {
      briefFlash(`记忆保存失败：${String(e)}`);
    }
  };
  const removeMemory = async (id: string) => {
    const ok = await showConfirm('删除记忆', '删除后主笔将不再感知到这条设定，不可恢复。', true);
    if (!ok) return;
    try {
      await deleteMemory(id);
      await loadMemories();
      briefFlash('已删除');
    } catch (e) {
      briefFlash(`删除失败：${String(e)}`);
    }
  };

  /* ---------- 结构编辑（原规划页功能并入） ---------- */
  const briefFlash = (t: string) => { setFlash(t); setTimeout(() => setFlash(''), 1500); };
  const loadBooks = async () => {
    try { const bs = await listBooks(); setBooks(bs); } catch { /* ignore */ }
  };
  const reload = async () => {
    try { const t = await fetchBookTree(bookId); setTree(t); } catch { /* ignore */ }
  };

  /* ---------- 章节场景编辑器（场景级部分修改 · 整体保存） ---------- */
  const openSceneEditor = () => {
    const ch = tree?.chapters.find((c) => c.id === selId);
    if (!ch) return;
    setSceneEdits((ch.scenes ?? []).map((s) => ({
      id: s.id,
      title: s.title,
      stage_desc: s.stage_desc ?? '',
      goal: s.goal ?? '',
      content_desc: s.content_desc ?? '',
    })));
    setSceneEditorOpen(true);
  };
  const setSceneEditField = (i: number, field: keyof ScenePatchItem, v: string) => {
    setSceneEdits((prev) => prev.map((r, idx) => (idx === i ? { ...r, [field]: v } : r)));
  };
  const addSceneRow = () => setSceneEdits((prev) => [...prev, { title: '', stage_desc: '', goal: '', content_desc: '' }]);
  const removeSceneRow = (i: number) => setSceneEdits((prev) => prev.filter((_, idx) => idx !== i));
  const saveSceneEdits = async () => {
    if (sceneSaving) return;
    const cleaned = sceneEdits.filter((s) => (s.title ?? '').trim() || s.id);
    if (cleaned.length === 0) { briefFlash('场景列表为空'); return; }
    setSceneSaving(true);
    try {
      await replaceChapterScenes(selId, cleaned);
      setSceneEditorOpen(false);
      await reload();
      briefFlash('场景已保存');
    } catch (e) {
      briefFlash(`保存失败：${String(e)}`);
    } finally {
      setSceneSaving(false);
    }
  };

  const letDraft = (kind: 'chapter' | 'scene', id: string) => {
    if (kind === 'chapter') {
      const c = tree?.chapters.find((x) => x.id === id);
      if (!c) return;
      const d = { title: c.title, tone: c.tone ?? 'action', word_target: c.word_target ?? 0, summary: c.summary ?? '' };
      setDraft(JSON.parse(JSON.stringify(d)));
      setSelId(id); setSelKind('chapter');
    } else {
      const s = tree?.chapters.flatMap((x) => x.scenes).find((x) => x.id === id);
      if (!s) return;
      const d = {
        title: s.title,
        scenario_def: s.scenario_def ?? 'betrayal_night',
        stage_desc: s.stage_desc ?? '',
        goal: s.goal ?? '',
        content_desc: s.content_desc ?? '',
      };
      setDraft(JSON.parse(JSON.stringify(d)));
      setSelId(id); setSelKind('scene');
    }
  };

  const saveDraft = async () => {
    if (!draft) return;
    try {
      if (selKind === 'chapter') {
        await updateChapter(selId, { title: draft.title as string, tone: draft.tone as string, word_target: Number(draft.word_target) || 0, summary: draft.summary as string });
      } else if (selKind === 'scene') {
        await updateScene(selId, {
          title: draft.title as string,
          scenario_def: draft.scenario_def as string,
          stage_desc: draft.stage_desc as string,
          goal: (draft.goal as string) ?? '',
          content_desc: (draft.content_desc as string) ?? '',
        });
      }
      await reload();
      briefFlash('已保存');
    } catch (e) {
      briefFlash(`保存失败：${String(e)}`);
    }
  };
  const setDraftField = (k: string, v: string) => setDraft((prev) => (prev ? { ...prev, [k]: v } : prev));

  const onAddBook = async () => {
    const title = await showPrompt('新书名', '新书');
    if (!title) return;
    try {
      const b = await createBook({ title, genre: '玄幻', status: 'planned' });
      await loadBooks();
      setBookId(b.id);
      briefFlash('已建书');
    } catch (e) { briefFlash(`建书失败：${String(e)}`); }
  };
  const onAddChapter = async () => {
    if (!tree) return;
    const title = await showPrompt('章节名', `第 ${(tree.chapters.length || 0) + 1} 章`);
    if (!title) return;
    try {
      await createChapter(tree.id, { title, order_no: tree.chapters.length || 0, tone: 'action' });
      await reload(); briefFlash('已建章');
    } catch (e) { briefFlash(`建章失败：${String(e)}`); }
  };
  const onAddScene = async () => {
    const ch = tree?.chapters.find((c) => c.id === selId);
    if (!ch) return;
    const title = await showPrompt('场景名', `场景 ${(ch.scenes.length || 0) + 1}`);
    if (!title) return;
    try {
      await createScene(selId, { title, scenario_def: 'betrayal_night', cursor_pos: 0, stage_desc: '' });
      setSelKind(null); setSelId('');
      await reload(); briefFlash('已建场景');
    } catch (e) { briefFlash(`建场景失败：${String(e)}`); }
  };
  const onDeleteSel = async () => {
    if (!selKind || !selId) return;
    const ok = await showConfirm('删除节点', '将删除选中节点及其子级，不可恢复。', true);
    if (!ok) return;
    try {
      if (selKind === 'chapter') await deleteChapter(selId);
      else if (selKind === 'scene') await deleteScene(selId);
      setSelKind(null); setSelId(''); setDraft(null);
      await reload(); briefFlash('已删除');
    } catch (e) { briefFlash(`删除失败：${String(e)}`); }
  };
  const enterDirector = (s: SceneMeta) => {
    nav(`/director/${s.id}`, { state: { book_id: tree?.id, chapter_id: s.chapter_id } });
  };

  const adoptable = inspirations.filter((c) => !c.adopted);
  const adopted = inspirations.filter((c) => c.adopted);

  return (
    <div className="app-shell">
      <Sidebar active="maestro" />
      <div className="main-col">
        <div className="maestro-workbench">
          {flash && <div className="maestro-feedback">{flash}</div>}
          {/* 顶栏 */}
          <header className="maestro-topbar">
            <div className="topbar-left">
              <span className="brand-seal">墨</span>
              <div className="book-meta">
                <div className="book-title">
                  {tree ? tree.title : books.find((b) => b.id === bookId)?.title ?? '本书骨架'}
                </div>
                <div className="book-switch">
                  <select
                    className="char-scope-select"
                    value={bookId}
                    onChange={(e) => setBookId(e.target.value)}
                    title="切换书"
                    style={{ maxWidth: 220 }}
                  >
                    {books.length === 0 && <option value="">（暂无书）</option>}
                    {books.map((b) => (
                      <option key={b.id} value={b.id}>{b.title}</option>
                    ))}
                  </select>
                  <button className="btn-icon" title="新建书" onClick={() => void onAddBook()}>＋</button>
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
                    {cardEditing === card.id ? (
                      <div className="card-edit-form">
                        <input
                          type="text"
                          className="char-edit-input"
                          value={cardDraft.title}
                          onChange={(e) => setCardDraft({ ...cardDraft, title: e.target.value })}
                        />
                        <textarea
                          className="char-edit-input"
                          rows={2}
                          value={cardDraft.desc}
                          onChange={(e) => setCardDraft({ ...cardDraft, desc: e.target.value })}
                        />
                        <select
                          className="char-scope-select"
                          value={cardDraft.type}
                          onChange={(e) => setCardDraft({ ...cardDraft, type: e.target.value as 'plot' | 'character' | 'world' })}
                        >
                          <option value="plot">剧情</option>
                          <option value="character">人物</option>
                          <option value="world">世界观</option>
                        </select>
                        <div className="card-footer">
                          <button className="btn-adopt" onClick={() => void saveCardEdit(card)}>保存</button>
                          <button className="btn-adopt" onClick={() => setCardEditing(null)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <>
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
                          <button className="btn-adopt" onClick={() => startEditCard(card)}>编辑</button>
                        </div>
                      </>
                    )}
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
                  <button className="btn-text" onClick={() => void onAddChapter()} disabled={!tree} title={tree ? '新增章节' : '先选一本书'}>＋ 加章</button>
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

              {/* 结构编辑：选中章/场景的上下文操作条 + 内联编辑器 */}
              {selKind && !draft && (
                <div className="inspiration-card" style={{ margin: '12px 16px 0' }}>
                  <div className="card-header">
                    <div className="card-title-wrap">
                      <h4 className="card-title">
                        {selKind === 'chapter' || selKind === 'scene'
                          ? (selKind === 'chapter'
                              ? tree?.chapters.find((c) => c.id === selId)?.title
                              : tree?.chapters.flatMap((c) => c.scenes).find((s) => s.id === selId)?.title)
                          : tree?.title}
                      </h4>
                    </div>
                  </div>
                  <div className="card-footer">
                    <span className="type-tag">{selKind === 'chapter' ? '章节' : selKind === 'scene' ? '场景' : '书'}</span>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {selKind === 'chapter' && (
                        <>
                          <button className="btn-adopt" onClick={() => void onAddScene()}>＋ 加场景</button>
                          <button className="btn-adopt" onClick={openSceneEditor}>✎ 编辑场景列表</button>
                        </>
                      )}
                      {selKind !== 'book' && (
                        <>
                          <button className="btn-adopt" onClick={() => letDraft(selKind as 'chapter' | 'scene', selId)}>✎ 编辑</button>
                          <button className="btn-adopt" onClick={() => void onDeleteSel()}>删除</button>
                        </>
                      )}
                      {selKind === 'scene' && (
                        <>
                          <button
                            className="btn-adopt"
                            onClick={() => {
                              const s = tree?.chapters.flatMap((c) => c.scenes).find((x) => x.id === selId);
                              if (s) enterDirector(s);
                            }}
                          >
                            ▶ 进入导演台
                          </button>
                          <button className="btn-adopt" onClick={() => nav(`/studio/${selId}`)}>✎ 正文协作</button>
                        </>
                      )}
                      <button className="btn-adopt" onClick={() => { setSelKind(null); setSelId(''); }}>取消</button>
                    </div>
                  </div>
                </div>
              )}

              {/* 章节场景编辑器（场景级部分修改 · 整体保存） */}
              {sceneEditorOpen && (
                <div className="card-edit-form scene-editor" style={{ margin: '12px 16px 0', padding: 12 }}>
                  <div className="scene-editor-title">
                    章节场景列表 —— 编辑后整体保存（缺省的列表项将被删除）
                  </div>
                  {sceneEdits.map((s, i) => (
                    <div className="scene-edit-row" key={i}>
                      <div className="scene-edit-fields">
                        <input
                          className="char-edit-input"
                          placeholder="场景标题"
                          value={s.title}
                          onChange={(e) => setSceneEditField(i, 'title', e.target.value)}
                        />
                        <input
                          className="char-edit-input"
                          placeholder="舞台布置 stage_desc"
                          value={s.stage_desc ?? ''}
                          onChange={(e) => setSceneEditField(i, 'stage_desc', e.target.value)}
                        />
                        <input
                          className="char-edit-input"
                          placeholder="本场目标 goal"
                          value={s.goal ?? ''}
                          onChange={(e) => setSceneEditField(i, 'goal', e.target.value)}
                        />
                        <textarea
                          className="char-edit-input"
                          rows={2}
                          placeholder="场景内容描述（事件梗概/冲突点/环境细节）"
                          value={s.content_desc ?? ''}
                          onChange={(e) => setSceneEditField(i, 'content_desc', e.target.value)}
                        />
                      </div>
                      <button className="btn-adopt" title="删除该场景" onClick={() => removeSceneRow(i)}>✕</button>
                    </div>
                  ))}
                  <div className="card-footer">
                    <button className="btn-adopt" onClick={addSceneRow}>＋ 添加场景</button>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn-adopt" onClick={() => setSceneEditorOpen(false)}>取消</button>
                      <button className="btn-adopt" onClick={() => void saveSceneEdits()} disabled={sceneSaving}>
                        {sceneSaving ? '保存中…' : '✓ 保存全部场景'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* 章/场景编辑草稿表单 */}
              {draft && (
                <div className="card-edit-form" style={{ margin: '12px 16px 0', padding: 12 }}>
                  <input
                    className="char-edit-input"
                    placeholder="标题"
                    value={String(draft.title ?? '')}
                    onChange={(e) => setDraftField('title', e.target.value)}
                  />
                  {selKind === 'chapter' ? (
                    <>
                      <select
                        className="char-scope-select"
                        value={String(draft.tone ?? 'action')}
                        onChange={(e) => setDraftField('tone', e.target.value)}
                      >
                        <option value="action">动作</option>
                        <option value="tension">张力</option>
                        <option value="reveal">揭秘</option>
                        <option value="setup">铺垫</option>
                      </select>
                      <input
                        className="char-edit-input"
                        type="number"
                        placeholder="目标字数"
                        value={String(draft.word_target ?? 0)}
                        onChange={(e) => setDraftField('word_target', e.target.value)}
                      />
                      <textarea
                        className="char-edit-input"
                        rows={2}
                        placeholder="章末小结"
                        value={String(draft.summary ?? '')}
                        onChange={(e) => setDraftField('summary', e.target.value)}
                      />
                    </>
                  ) : (
                    <>
                      <input
                        className="char-edit-input"
                        placeholder="场景方案（betrayal_night 等）"
                        value={String(draft.scenario_def ?? '')}
                        onChange={(e) => setDraftField('scenario_def', e.target.value)}
                      />
                      <textarea
                        className="char-edit-input"
                        rows={2}
                        placeholder="舞台布置"
                        value={String(draft.stage_desc ?? '')}
                        onChange={(e) => setDraftField('stage_desc', e.target.value)}
                      />
                      <input
                        className="char-edit-input"
                        placeholder="本场目标 goal"
                        value={String(draft.goal ?? '')}
                        onChange={(e) => setDraftField('goal', e.target.value)}
                      />
                      <textarea
                        className="char-edit-input"
                        rows={2}
                        placeholder="场景内容描述（事件梗概/冲突点/环境细节）"
                        value={String(draft.content_desc ?? '')}
                        onChange={(e) => setDraftField('content_desc', e.target.value)}
                      />
                    </>
                  )}
                  <div className="card-footer">
                    <button className="btn-adopt" onClick={() => void saveDraft()}>保存</button>
                    <button className="btn-adopt" onClick={() => setDraft(null)}>取消</button>
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
                        className={`outline-row chapter ${activeChapter === ch.id ? 'active' : ''} ${selKind === 'chapter' && selId === ch.id ? 'selected' : ''}`}
                        onClick={() => { setActiveChapter(ch.id); setSelKind('chapter'); setSelId(ch.id); setDraft(null); }}
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
                          <div
                            className={`outline-row scene ${selKind === 'scene' && selId === s.id ? 'selected' : ''}`}
                            key={s.id}
                            onClick={() => { setSelKind('scene'); setSelId(s.id); setDraft(null); }}
                          >
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

              {/* 书级记忆（主笔 · 记忆域） */}
              <div className="memory-section">
                <div className="memory-header" onClick={() => setMemoryOpen(!memoryOpen)}>
                  <span className="title-ico">◈</span>
                  <span>书级记忆</span>
                  <span className="panel-count">{memories.length}</span>
                  <span className="memory-toggle">{memoryOpen ? '▾' : '▴'}</span>
                </div>
                {memoryOpen && (
                  <div className="memory-body">
                    {memories.length === 0 && (
                      <div className="memory-empty">
                        暂无记忆。写下方向/设定/约束，主笔每次对话与规划都会感知到。
                      </div>
                    )}
                    <div className="memory-list">
                      {memories.map((m) => (
                        <div className="inspiration-card memory-card" key={m.id}>
                          <div className="card-header">
                            <div className="card-title-wrap">
                              <p className="memory-content">{m.content}</p>
                            </div>
                          </div>
                          <div className="card-footer">
                            <span className="type-tag">{MEMORY_TOPIC_LABEL[m.topic] ?? m.topic}</span>
                            <span className="memory-source">
                              {m.source === 'author' ? '作者' : m.source === 'chief' ? '主笔' : '记账'}
                            </span>
                            <button className="btn-adopt" onClick={() => startEditMemory(m)}>编辑</button>
                            <button className="btn-adopt" onClick={() => void removeMemory(m.id)}>删除</button>
                          </div>
                        </div>
                      ))}
                    </div>
                    {memoryFormOpen ? (
                      <div className="card-edit-form memory-form">
                        <select
                          className="char-scope-select"
                          value={memoryDraftTopic}
                          onChange={(e) => setMemoryDraftTopic(e.target.value as MemoryTopic)}
                        >
                          {MEMORY_TOPIC_OPTIONS.map(([v, l]) => (
                            <option key={v} value={v}>{l}</option>
                          ))}
                        </select>
                        <textarea
                          className="char-edit-input"
                          rows={2}
                          placeholder="写一条记忆：方向 / 设定 / 写作约束……"
                          value={memoryDraftContent}
                          onChange={(e) => setMemoryDraftContent(e.target.value)}
                        />
                        <div className="card-footer">
                          <button className="btn-adopt" onClick={() => void saveMemory()}>
                            {memoryEditingId ? '保存修改' : '添加记忆'}
                          </button>
                          <button className="btn-adopt" onClick={() => setMemoryFormOpen(false)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <div className="memory-add">
                        <button className="btn-adopt" onClick={startAddMemory}>＋ 写一条记忆</button>
                      </div>
                    )}
                  </div>
                )}
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