import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { createBook, createMemory, listBooks } from '../../api/novel';
import type { BookMeta, MemoryTopic } from '../../api/novel';
import { useDialog } from '../common/Dialog';
import { useWorkspace, type StepKey } from '../../context/WorkspaceContext';

/* ==========================================================================
   Sidebar —— 三段式（书架 / 本书工作区 / 全局）
   规则：导航项要么能到，要么显式灰态；不再保留任何 href="#" 死链。
   ========================================================================== */

type SidebarKey = StepKey | 'home' | 'characters';

const STEPS: Array<{ key: StepKey; no: string; text: string }> = [
  { key: 'maestro', no: '1', text: '主笔创作' },
  { key: 'director', no: '2', text: '导演台' },
  { key: 'studio', no: '3', text: '正文协作' },
  { key: 'dashboard', no: '4', text: '概览复盘' },
];

export function Sidebar({ active }: { active: SidebarKey }) {
  const nav = useNavigate();
  const ws = useWorkspace();
  const { showPrompt } = useDialog();
  const [fallbackBooks, setFallbackBooks] = useState<BookMeta[]>([]);

  // 兜底：context 尚未就绪时也能显示书列表
  useEffect(() => {
    if (ws.books.length > 0) return;
    listBooks().then(setFallbackBooks).catch(() => setFallbackBooks([]));
  }, [ws.books.length]);
  const books = ws.books.length > 0 ? ws.books : fallbackBooks;

  const stepTo = (key: StepKey): string => {
    if (key === 'director') return ws.sceneId ? `/director/${ws.sceneId}` : '/director';
    if (key === 'studio') return ws.sceneId ? `/studio/${ws.sceneId}` : '/studio';
    if (key === 'dashboard') return ws.bookId ? `/dashboard?book=${ws.bookId}` : '/dashboard';
    return '/maestro';
  };

  const stepDot = (key: StepKey): string => {
    const { outline, turns, prose } = ws.status;
    if (key === 'maestro') return outline ? 'done' : 'idle';
    if (key === 'director') return turns ? 'done' : outline ? 'run' : 'idle';
    if (key === 'studio') return prose ? 'done' : turns ? 'run' : 'idle';
    return (ws.globalView?.diagnostics?.length ?? 0) > 0 ? 'run' : 'idle';
  };

  // 建书：书名 + 一句话方向（可跳过）→ 方向落成 book_memories(topic=direction)
  const newBook = async () => {
    const title = await showPrompt('新书名', '新书');
    if (!title) return;
    try {
      const b = await createBook({ title, genre: '玄幻', status: 'planned' });
      const direction = await showPrompt('一句话方向（可留空跳过）', '');
      if (direction && direction.trim()) {
        try {
          await createMemory(b.id, { topic: 'direction' as MemoryTopic, content: direction.trim() });
        } catch {
          /* 方向写失败不阻塞建书 */
        }
      }
      ws.refreshBooks();
      ws.setBook(b.id);
      nav(`/maestro?book=${b.id}`);
    } catch (e) {
      console.warn('[书架] 建书失败：', e);
    }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-seal">墨</span>
        <span className="brand-name">墨卷</span>
      </div>

      {/* ---------- 书架 ---------- */}
      <div className="sidebar-group">
        <div className="sidebar-group-label">书架</div>
        <div className="book-list">
          {books.length === 0 && (
            <div className="char-empty">
              暂无书
              <br />
              点「新书」创建
            </div>
          )}
          {books.map((b) => {
            const cur = b.id === ws.bookId;
            const count = cur && ws.tree ? ws.tree.chapters.length : b.chapter_count;
            return (
              <Link
                className={`book-item ${cur ? 'active' : ''}`}
                to={`/dashboard?book=${b.id}`}
                key={b.id}
                onClick={() => ws.setBook(b.id)}
              >
                <span className="book-init">{b.cover_init || b.title.slice(0, 1)}</span>
                <div className="book-meta">
                  <div className="book-title">{b.title}</div>
                  <div className="book-sub">
                    {b.genre} · {count} 章
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
        <button className="new-book-btn" onClick={() => void newBook()}>
          <span className="plus-icon">+</span>
          <span>新书</span>
        </button>
      </div>

      {/* ---------- 本书工作区 ---------- */}
      <div className="sidebar-group">
        <div className="sidebar-group-label">本书工作区</div>
        <nav className="nav-list">
          {STEPS.map((s) => (
            <Link className={`nav-item ${active === s.key ? 'active' : ''}`} to={stepTo(s.key)} key={s.key}>
              <span className="nav-step-no">{s.no}</span>
              <span className="nav-text">{s.text}</span>
              <span className={`nav-dot ${stepDot(s.key)}`} title="进度状态" />
            </Link>
          ))}
          <Link className={`nav-item ${active === 'characters' ? 'active' : ''}`} to={ws.bookId ? `/characters?book=${ws.bookId}` : '/characters'}>
            <span className="nav-ico">☺</span>
            <span className="nav-text">人物</span>
            {ws.characterCount > 0 && <span className="nav-badge">{ws.characterCount}</span>}
          </Link>
          <Link className={`nav-item ${active === 'settings' ? 'active' : ''}`} to="/settings">
            <span className="nav-ico">☷</span>
            <span className="nav-text">设定</span>
          </Link>
        </nav>
      </div>

      {/* ---------- 全局 ---------- */}
      <div className="sidebar-group sidebar-group-footer">
        <div className="sidebar-group-label">全局</div>
        <nav className="nav-list">
          <div className="nav-item nav-soon" title="跨书素材库：RAG 落地后开放">
            <span className="nav-ico">▦</span>
            <span className="nav-text">素材库</span>
            <span className="nav-chip">RAG 后</span>
          </div>
          <Link className="nav-item" to="/settings?tab=global">
            <span className="nav-ico">⚙</span>
            <span className="nav-text">全局设置</span>
          </Link>
        </nav>
      </div>
    </aside>
  );
}
