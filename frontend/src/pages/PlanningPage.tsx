import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import {
  listBooks,
  fetchBookTree,
  createBook,
  updateBook,
  deleteBook,
  createChapter,
  updateChapter,
  deleteChapter,
  createScene,
  updateScene,
  deleteScene,
} from '../api/novel';
import type { BookMeta, BookTree, ChapterMeta, SceneMeta } from '../api/novel';
import { API_BASE } from '../types/types';

type SelNode =
  | { kind: 'book'; id: string }
  | { kind: 'chapter'; id: string }
  | { kind: 'scene'; id: string };

interface PlanPreview {
  worldview?: { premise?: string; rules_text?: string; background?: string };
  chapters?: Array<{ title?: string; tone?: string; scenes?: unknown[] }>;
  foreshadow_plan?: unknown[];
  ending_options?: string[];
}

export function PlanningPage() {
  const nav = useNavigate();
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState<string>('');
  const [tree, setTree] = useState<BookTree | null>(null);
  const [sel, setSel] = useState<SelNode | null>(null);
  const [msg, setMsg] = useState('');
  const [planning, setPlanning] = useState(false);      // 主笔规划中
  const [planPreview, setPlanPreview] = useState<PlanPreview | null>(null);

  // 保存成功过的刷新提示
  const flash = (t: string) => {
    setMsg(t);
    setTimeout(() => setMsg(''), 1500);
  };

  const loadBooks = async () => {
    const bs = await listBooks();
    setBooks(bs);
    if (bs.length > 0 && !bs.some((b) => b.id === bookId)) setBookId(bs[0].id);
  };

  const loadTree = async (bid: string) => {
    if (!bid) {
      setTree(null);
      return;
    }
    try {
      const t = await fetchBookTree(bid);
      setTree(t);
      const firstChapter = t.chapters[0];
      const firstScene = firstChapter?.scenes[0];
      setSel(firstScene ? { kind: 'scene', id: firstScene.id } : firstChapter ? { kind: 'chapter', id: firstChapter.id } : { kind: 'book', id: bid });
    } catch (e) {
      console.warn('[规划页] 加载书籍树失败：', e);
      setTree(null);
    }
  };

  useEffect(() => {
    void loadBooks();
  }, []);

  useEffect(() => {
    void loadTree(bookId);
  }, [bookId]);

  // ---- 主笔规划（S2）----
  const onChiefPlan = async () => {
    if (!tree) return;
    setPlanning(true);
    setPlanPreview(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/books/${tree.id}/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction: tree.synopsis ?? '' }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => null);
        flash(`主笔规划失败：${d?.detail ?? res.status}`);
        return;
      }
      const data = (await res.json()) as { plan: PlanPreview };
      setPlanPreview(data.plan);
    } catch (e) {
      flash(`主笔规划失败：${String(e)}`);
    } finally {
      setPlanning(false);
    }
  };
  const onChiefCommit = async () => {
    if (!tree || !planPreview) return;
    await fetch(`${API_BASE}/api/v1/books/${tree.id}/plan/commit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: planPreview }),
    });
    setPlanPreview(null);
    await loadTree(tree.id);
    await loadBooks();
    flash('骨架已落库');
  };

  // ---- 新建 ----
  const onNewBook = async () => {
    const title = window.prompt('新书名？', '新书');
    if (!title) return;
    await createBook({ title, genre: '玄幻', status: 'planned' });
    await loadBooks();
    flash('已建书');
  };
  const onNewChapter = async () => {
    if (sel?.kind !== 'book') return;
    const title = window.prompt('章节名？', `第 ${(tree?.chapters.length ?? 0) + 1} 章`);
    if (!title) return;
    await createChapter(sel.id, { title, order_no: tree?.chapters.length ?? 0, tone: 'action' });
    await loadTree(bookId);
    flash('已建章');
  };
  const onNewScene = async () => {
    if (sel?.kind !== 'chapter') return;
    const title = window.prompt('场景名？', `场景 ${((tree?.chapters.find((c) => c.id === sel.id)?.scenes.length) ?? 0) + 1}`);
    if (!title) return;
    await createScene(sel.id, { title, scenario_def: 'betrayal_night', cursor_pos: 0, stage_desc: '' });
    await loadTree(bookId);
    flash('已建场景');
  };
  const onDeleteSel = async () => {
    if (!sel) return;
    if (!window.confirm('删除选中的节点及其子级？')) return;
    if (sel.kind === 'book') await deleteBook(sel.id);
    if (sel.kind === 'chapter') await deleteChapter(sel.id);
    if (sel.kind === 'scene') await deleteScene(sel.id);
    setSel(null);
    await loadBooks();
    await loadTree(bookId);
    flash('已删除');
  };

  // ---- 保存 ----
  const onSaveBook = async () => {
    if (!tree || sel?.kind !== 'book') return;
    await updateBook(sel.id, {
      title: tree.title, genre: tree.genre, synopsis: tree.synopsis ?? '',
    });
    flash('书已保存');
  };
  const saveChapter = async (c: ChapterMeta) => {
    await updateChapter(c.id, c);
    flash('章已保存');
  };
  const saveScene = async (s: SceneMeta) => {
    await updateScene(s.id, s);
    flash('场景已保存');
  };

  const curChapter = sel?.kind === 'chapter' ? tree?.chapters.find((c) => c.id === sel.id) : undefined;
  const curScene = sel?.kind === 'scene'
    ? tree?.chapters.flatMap((c) => c.scenes).find((s) => s.id === sel.id)
    : undefined;

  return (
    <div className="app-shell">
      <Sidebar active="planning" />
      <div className="main-col">
        <header className="top-header planning-header">
          <div className="header-left">
            <h1 className="book-title-main">结构规划</h1>
            <span className="genre-tag">书 → 章 → 场景</span>
          </div>
          <div className="header-right">
            <span className="planning-msg">{msg}</span>
            {sel?.kind === 'scene' && (
              <button className="btn-primary" onClick={() => nav(`/director/${curScene?.id}`, { state: { book_id: tree?.id, chapter_id: curScene?.chapter_id } })}>
                进入导演台
              </button>
            )}
            <ThemeToggle />
          </div>
        </header>

        <div className="content-area planning-body">
          {/* 左侧：书 + 树 */}
          <aside className="planning-tree">
            <div className="planning-tree-head">
              <span className="section-title">书架</span>
              <button className="btn-mini" onClick={onNewBook}>+ 新书</button>
            </div>
            <div className="book-switch">
              {books.map((b) => (
                <button
                  key={b.id}
                  className={`book-pill ${b.id === bookId ? 'active' : ''}`}
                  onClick={() => setBookId(b.id)}
                >
                  {b.cover_init} {b.title}
                </button>
              ))}
            </div>

            {tree && (
              <div className="tree-list">
                <div className={`tree-row ${sel?.kind === 'book' ? 'selected' : ''}`} onClick={() => setSel({ kind: 'book', id: tree.id })}>
                  <span className="tree-caret">▾</span>📖 {tree.title}
                </div>
                {tree.chapters.map((c) => (
                  <div key={c.id}>
                    <div className={`tree-row tree-chapter ${sel?.kind === 'chapter' && sel.id === c.id ? 'selected' : ''}`} onClick={() => setSel({ kind: 'chapter', id: c.id })}>
                      <span className="tree-caret">▾</span>☰ {c.title}
                    </div>
                    {c.scenes.map((s) => (
                      <div key={s.id} className={`tree-row tree-scene ${sel?.kind === 'scene' && sel.id === s.id ? 'selected' : ''}`} onClick={() => setSel({ kind: 'scene', id: s.id })}>
                        ▸ {s.title}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}

            <div className="planning-actions">
              {sel?.kind === 'book' && <button className="btn-mini" onClick={onNewChapter}>+ 章</button>}
              {sel?.kind === 'chapter' && <button className="btn-mini" onClick={onNewScene}>+ 场景</button>}
              {sel && <button className="btn-mini danger" onClick={onDeleteSel}>删除</button>}
            </div>
          </aside>

          {/* 右侧：编辑 */}
          <main className="planning-editor">
            {!tree && <div className="planning-hint">还没有书，点左上「+ 新书」开始，或先进导演台跑现有书。</div>}
            {tree && sel?.kind === 'book' && (
              <div className="editor-form">
                <h3>书籍 · {tree.title}</h3>
                <label>书名<input value={tree.title} onChange={(e) => setTree({ ...tree, title: e.target.value })} /></label>
                <label>题材<input value={tree.genre} onChange={(e) => setTree({ ...tree, genre: e.target.value })} /></label>
                <label>一句话总纲 / 主笔方向
                  <textarea value={tree.synopsis ?? ''} rows={3} onChange={(e) => setTree({ ...tree, synopsis: e.target.value })} />
                  <div className="hint-text">填好方向后，点「让主笔规划」生成章节骨架，预览确认后落库。</div>
                </label>
                <div className="editor-actions">
                  <button className="btn-primary" onClick={onSaveBook}>保存书</button>
                  <button className="btn-primary chief" onClick={onChiefPlan} disabled={planning}>
                    {planning ? '主笔构思中…' : '让主笔规划'}
                  </button>
                </div>
                {planPreview && (
                  <div className="plan-preview">
                    <h4>主笔骨架预览</h4>
                    {planPreview.worldview?.premise && <p className="premise">{planPreview.worldview.premise}</p>}
                    {(planPreview.chapters ?? []).map((c, i) => (
                      <div className="preview-chapter" key={i}>
                        <div className="preview-ch-title">{i + 1}. {c.title}{c.tone ? ` · ${c.tone}` : ''}</div>
                        <div className="preview-scenes">{c.scenes?.length ?? 0} 个场景</div>
                      </div>
                    ))}
                    <div className="editor-actions">
                      <button className="btn-danger" onClick={() => setPlanPreview(null)}>✗ 重排</button>
                      <button className="btn-primary" onClick={onChiefCommit}>✓ 确认落库</button>
                    </div>
                  </div>
                )}
              </div>
            )}
            {sel?.kind === 'chapter' && curChapter && (
              <div className="editor-form">
                <h3>章节 · {curChapter.title}</h3>
                <label>章名<input value={curChapter.title} onChange={(e) => saveChapter({ ...curChapter, title: e.target.value })} /></label>
                <label>基调
                  <select value={curChapter.tone} onChange={(e) => saveChapter({ ...curChapter, tone: e.target.value })}>
                    <option value="action">action 动作</option>
                    <option value="suspense">suspense 悬念</option>
                    <option value="warmth">warmth 温情</option>
                    <option value="serene">serene 宁静</option>
                  </select>
                </label>
                <label>字数目标<input type="number" value={curChapter.word_target || 0} onChange={(e) => saveChapter({ ...curChapter, word_target: Number(e.target.value) })} /></label>
                <label>摘要<textarea value={curChapter.summary} rows={3} onChange={(e) => saveChapter({ ...curChapter, summary: e.target.value })} /></label>
              </div>
            )}
            {sel?.kind === 'scene' && curScene && (
              <div className="editor-form">
                <h3>场景 · {curScene.title}</h3>
                <label>场景名<input value={curScene.title} onChange={(e) => saveScene({ ...curScene, title: e.target.value })} /></label>
                <label>舞台布置<textarea value={curScene.stage_desc ?? ''} rows={3} onChange={(e) => saveScene({ ...curScene, stage_desc: e.target.value })} /></label>
                <label>模板<select value={curScene.scenario_def} onChange={(e) => saveScene({ ...curScene, scenario_def: e.target.value })}>
                  <option value="betrayal_night">betrayal_night 雨夜书房</option>
                  <option value="xuanhuan">xuanhuan 玄幻（预留）</option>
                </select></label>
                <button className="btn-primary" onClick={() => nav(`/director/${curScene.id}`, { state: { book_id: tree?.id, chapter_id: curScene.chapter_id } })}>进入导演台推演</button>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}