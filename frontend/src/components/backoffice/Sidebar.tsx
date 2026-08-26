import { Link } from 'react-router-dom';

const NAV_ITEMS = [
  { key: 'dashboard', to: '/dashboard', icon: '◉', text: '概览' },
  { key: 'maestro', to: '/maestro', icon: '✎', text: '主笔创作' },
  { key: 'settings', icon: '☷', text: '设定' },
  { key: 'characters', to: '/characters', icon: '☺', text: '人物' },
  { key: 'outline', icon: '☷', text: '大纲' },
  { key: 'foreshadow', icon: '⟡', text: '伏笔' },
  { key: 'chapters', icon: '☰', text: '章节' },
  { key: 'memory', icon: '▦', text: '记忆包' },
  { key: 'checkup', icon: '♥', text: '体检' },
];

export function Sidebar({ active }: { active: 'dashboard' | 'maestro' | 'characters' | 'planning' }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-seal">墨</span>
        <span className="brand-name">墨卷</span>
      </div>

      <div className="sidebar-section">
        <div className="section-label">书架</div>
        <div className="book-list">
          <a className="book-item active" href="#">
            <span className="book-init">雨</span>
            <div className="book-meta">
              <div className="book-title">雨夜书房</div>
              <div className="book-sub">悬疑 · 23 章</div>
            </div>
          </a>
        </div>
        <button className="new-book-btn">
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