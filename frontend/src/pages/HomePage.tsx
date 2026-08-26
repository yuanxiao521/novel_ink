import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listBooks, fetchBookTree, type BookMeta, type BookTree } from '../api/novel';

export function HomePage() {
  const navigate = useNavigate();
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [tree, setTree] = useState<BookTree | null>(null); // 选中的书籍树（章→场景）
  const [selectedChapter, setSelectedChapter] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const bs = await listBooks();
        if (!cancelled) setBooks(bs);
      } catch (e) {
        console.warn('[书架] 拉取书籍失败：', e);
        setBooks([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openBook = async (bookId: string) => {
    try {
      const t = await fetchBookTree(bookId);
      setTree(t);
      setSelectedChapter(t.chapters[0]?.id ?? null);
    } catch (e) {
      console.warn('[书架] 拉取书籍树失败：', e);
    }
  };

  const enterScene = (bookId: string, chapterId: string, sceneId: string) => {
    navigate(`/director/${sceneId}`, { state: { book_id: bookId, chapter_id: chapterId, scene_id: sceneId } });
  };

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

          {loading ? (
            <div className="home-hint">加载书架中…</div>
          ) : books.length === 0 ? (
            <div className="home-hint">书架为空（后端未 seed？运行 uv run python -m app.db.seed）</div>
          ) : (
            <div className="book-grid">
              {books.map((book) => (
                <div className="book-card" key={book.id} role="button" tabIndex={0} onClick={() => openBook(book.id)}>
                  <div className="book-cover">
                    <span className="book-cover-init">{book.cover_init || '墨'}</span>
                  </div>
                  <div className="book-card-body">
                    <div className="book-card-title">{book.title}</div>
                    <div className="book-card-meta">
                      <span className="book-card-genre">{book.genre}</span>
                      <span className="book-card-chapters">{book.chapter_count} 章</span>
                    </div>
                    <div className="book-card-status">
                      <span className={`status-dot ${book.status === 'writing' ? 'active' : ''}`}></span>
                      {book.status === 'writing' ? '写作中' : book.status}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {tree && (
            <div className="book-tree-panel">
              <div className="book-tree-header">
                <span className="book-tree-title">{tree.title} · 场景选择</span>
                <button className="book-tree-close" onClick={() => setTree(null)}>
                  ✕
                </button>
              </div>
              <div className="chapter-tabs">
                {tree.chapters.map((ch) => (
                  <button
                    key={ch.id}
                    className={`chapter-tab ${selectedChapter === ch.id ? 'active' : ''}`}
                    onClick={() => setSelectedChapter(ch.id)}
                  >
                    {ch.title || `第 ${ch.order_no} 章`}
                  </button>
                ))}
              </div>
              <div className="scene-list">
                {tree.chapters
                  .find((ch) => ch.id === selectedChapter)
                  ?.scenes.map((sc) => (
                    <div className="scene-card" key={sc.id}>
                      <div className="scene-card-title">{sc.title}</div>
                      <div className="scene-card-meta">
                        <span>角色 {sc.characters?.length ?? 0} 位</span>
                        <span className="scene-card-scenario">{sc.scenario_def}</span>
                      </div>
                      <button
                        className="scene-enter-btn"
                        onClick={() => enterScene(tree.id, sc.chapter_id, sc.id)}
                      >
                        进入导演台 ▶
                      </button>
                    </div>
                  ))}
              </div>
            </div>
          )}

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