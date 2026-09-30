import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { listBooks, createBook } from '../../api/novel';
import { useDialog } from '../common/Dialog';
import type { BookMeta } from '../../api/novel';

const NAV_ITEMS = [
  { key: 'home', to: '/', icon: '▤', text: '书架' },
  { key: 'dashboard', to: '/dashboard', icon: '◉', text: '概览' },
  { key: 'maestro', to: '/maestro', icon: '✎', text: '主笔创作' },
  { key: 'characters', to: '/characters', icon: '☺', text: '人物' },
  // 以下为未来扩展点（死链保留，不加假功能）
  { key: 'settings', icon: '☷', text: '设定' },
  { key: 'outline', icon: '☷', text: '大纲' },
  { key: 'foreshadow', icon: '⟡', text: '伏笔' },
  { key: 'chapters', icon: '☰', text: '章节' },
  { key: 'memory', icon: '▦', text: '记忆包' },
  { key: 'checkup', icon: '♥', text: '体检' },
];

export function Sidebar({ active }: { active: 'home' | 'dashboard' | 'maestro' | 'characters' }) {
  const nav = useNavigate();
  const location = useLocation();
  const { showPrompt } = useDialog();
  const [books, setBooks] = useState<BookMeta[]>([]);

  const load = () => {
    listBooks().then(setBooks).catch(() => setBooks([]));
  };
  useEffect(() => {
    load();
  }, []);

  // 当前选中书（来自 ?book=，供书架高亮）
  const selBook = new URLSearchParams(location.search).get('book') || '';

  const newBook = async () => {
    const title = await showPrompt('新书名', '新书');
    if (!title) return;
    try {
      const r = await createBook({ title, genre: '玄幻', status: 'planned' });
      await load();
      nav(`/dashboard?book=${r.id}`);
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

      <div className="sidebar-section">
        <div className="section-label">书架</div>
        <div className="book-list">
          {books.length === 0 && (
            <div className="char-empty">暂无书<br />点「新书」创建</div>
          )}
          {books.map((b) => (
            <Link
              className={`book-item ${selBook === b.id ? 'active' : ''}`}
              to={`/dashboard?book=${b.id}`}
              key={b.id}
            >
              <span className="book-init">{b.cover_init || b.title.slice(0, 1)}</span>
              <div className="book-meta">
                <div className="book-title">{b.title}</div>
                <div className="book-sub">{b.genre} · {b.chapter_count} 章</div>
              </div>
            </Link>
          ))}
        </div>
        <button className="new-book-btn" onClick={newBook}>
          <span className="plus-icon">+</span>
          <span>新书</span>
        </button>
      </div>

      <div className="sidebar-divider"></div>

      <div className="sidebar-section nav-section">
        <div className="section-label">本书工作区</div>
        <nav className="nav-list">
          {NAV_ITEMS.map((it) =>
            it.to ? (
              <Link className={`nav-item ${active === it.key ? 'active' : ''}`} to={it.to} key={it.key}>
                <span className="nav-icon">{it.icon}</span>
                <span className="nav-text">{it.text}</span>
              </Link>
            ) : (
              <a className="nav-item" href="#" key={it.key}>
                <span className="nav-icon">{it.icon}</span>
                <span className="nav-text">{it.text}</span>
              </a>
            ),
          )}
        </nav>
      </div>

      <div className="sidebar-footer">
        <a className="nav-item" href="#">
          <span className="nav-icon">⚙</span>
          <span className="nav-text">全局设置</span>
        </a>
      </div>
    </aside>
  );
}