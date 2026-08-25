import { Link } from 'react-router-dom';

const BOOKS = [
  {
    id: 'rain',
    title: '雨夜书房',
    genre: '悬疑',
    chapters: 23,
    status: '写作中',
    cover: '雨',
    progress: 65,
    lastUpdate: '2 小时前',
  },
  {
    id: 'star',
    title: '星辰邮局',
    genre: '温情',
    chapters: 8,
    status: '写作中',
    cover: '星',
    progress: 40,
    lastUpdate: '昨天',
  },
  {
    id: 'sword',
    title: '剑雪归藏',
    genre: '武侠',
    chapters: 0,
    status: '草稿',
    cover: '剑',
    progress: 0,
    lastUpdate: '3 天前',
  },
];

export function HomePage() {
  return (
    <div className="app-shell home-shell">
      <div className="home-container">
        {/* 顶部品牌栏 */}
        <header className="home-header">
          <div className="home-brand">
            <span className="brand-seal-lg">墨</span>
            <div className="brand-text">
              <h1 className="brand-name-lg">墨卷</h1>
              <span className="brand-slogan">涌现式小说 Agent</span>
            </div>
          </div>
          <div className="home-header-right">
            <div className="user-avatar">作</div>
          </div>
        </header>

        {/* 书架区 */}
        <main className="home-main">
          <div className="home-section-header">
            <h2 className="home-section-title">我的书架</h2>
            <button className="new-book-btn home-new-btn">
              <span className="plus-icon">+</span>
              <span>新书</span>
            </button>
          </div>

          <div className="book-grid">
            {BOOKS.map((book) => (
              <Link to="/dashboard" className="book-card" key={book.id}>
                <div className="book-cover">
                  <span className="book-cover-init">{book.cover}</span>
                </div>
                <div className="book-card-body">
                  <div className="book-card-title">{book.title}</div>
                  <div className="book-card-meta">
                    <span className="book-card-genre">{book.genre}</span>
                    <span className="book-card-chapters">{book.chapters} 章</span>
                  </div>
                  {book.progress > 0 && (
                    <div className="book-progress">
                      <div className="book-progress-bar">
                        <div className="book-progress-fill" style={{ width: `${book.progress}%` }}></div>
                      </div>
                      <span className="book-progress-text">{book.progress}%</span>
                    </div>
                  )}
                  <div className="book-card-status">
                    <span className={`status-dot ${book.status === '写作中' ? 'active' : ''}`}></span>
                    {book.status} · {book.lastUpdate}
                  </div>
                </div>
              </Link>
            ))}
          </div>

          {/* 快速入口 */}
          <div className="home-section-header" style={{ marginTop: 32 }}>
            <h2 className="home-section-title">快速入口</h2>
          </div>
          <div className="quick-entry-grid">
            <Link to="/dashboard" className="quick-entry-card">
              <span className="quick-entry-icon">◉</span>
              <span className="quick-entry-label">概览</span>
            </Link>
            <Link to="/director" className="quick-entry-card">
              <span className="quick-entry-icon">▶</span>
              <span className="quick-entry-label">导演台</span>
            </Link>
            <Link to="/characters" className="quick-entry-card">
              <span className="quick-entry-icon">☺</span>
              <span className="quick-entry-label">人物</span>
            </Link>
            <div className="quick-entry-card">
              <span className="quick-entry-icon">☰</span>
              <span className="quick-entry-label">章节</span>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
