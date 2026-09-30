import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import { useDialog } from '../components/common/Dialog';
import { listBooks, createBook, fetchDashboard } from '../api/novel';
import type { BookMeta, DashboardData } from '../api/novel';

const TODO_ICON: Record<string, { icon: string; char: string }> = {
  ok: { icon: 'success', char: '✓' },
  info: { icon: 'info', char: '✎' },
  warn: { icon: 'warning', char: '!' },
  danger: { icon: 'warning', char: '!' },
};

export function DashboardPage() {
  const [sp] = useSearchParams();
  const { showPrompt } = useDialog();
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [bookId, setBookId] = useState('');
  const [data, setData] = useState<DashboardData | null>(null);
  const [err, setErr] = useState('');

  // 书列表 + 初始选中：URL ?book= 优先；无效或缺失则取首本
  useEffect(() => {
    listBooks()
      .then((bs) => {
        setBooks(bs);
        const preset = sp.get('book');
        if (bs.some((b) => b.id === preset)) setBookId(preset as string);
        else if (bs.length) setBookId(bs[0].id);
      })
      .catch((e) => setErr(`书列表加载失败：${e instanceof Error ? e.message : e}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);

  useEffect(() => {
    if (!bookId) {
      setData(null);
      return;
    }
    setErr('');
    fetchDashboard(bookId)
      .then(setData)
      .catch((e) => setErr(`概览加载失败：${e instanceof Error ? e.message : e}`));
  }, [bookId]);

  const newBook = async () => {
    const title = await showPrompt('新书名', '新书');
    if (!title) return;
    try {
      const r = await createBook({ title, genre: '玄幻', status: 'planned' });
      const bs = await listBooks();
      setBooks(bs);
      setBookId(r.id);
    } catch (e) {
      setErr(`建书失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const kpi = data?.kpi;
  const wordCountFmt = (w: number) => {
    if (w <= 0) return '0 字';
    return w >= 10000 ? `${(w / 10000).toFixed(1)} 万字` : `${w} 字`;
  };
  const hasWordCount = (kpi?.word_count ?? 0) > 0;

  return (
    <div className="app-shell">
      <Sidebar active="dashboard" />
      <div className="main-col">
        <header className="top-header">
          <div className="header-left">
            <h1 className="book-title-main">{data?.book.title || '（未选书）'}</h1>
            <span className="genre-tag">{data?.book.genre || '暂无书'}</span>
          </div>
          <div className="header-search">
            <span className="search-icon">⌕</span>
            <input type="text" placeholder="跨书搜索角色、伏笔、章节…" />
          </div>
          <div className="header-right">
            <button className="btn-primary" onClick={newBook}>
              <span>＋ 新建书</span>
            </button>
            <Link to="/director">
              <button className="btn-primary">
                <span>进入导演台</span>
              </button>
            </Link>
            <span className={`health-badge ${kpi && kpi.health < 60 ? 'warn' : ''}`}>
              <span className="health-dot"></span>
              健康分 {kpi?.health ?? '-'}
            </span>
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
          {err && <span className="char-scope-msg err">{err}</span>}
        </div>

        <div className="content-area">
          <main className="main-content">
            {!data ? (
              <div className="char-empty detail">
                {err || '暂无概览数据。先去「主笔共创」规划书骨，或进「导演台」推演定稿。'}
              </div>
            ) : (
              <>
                <section className="kpi-section">
                  <div className="kpi-grid">
                    <div className="kpi-card">
                      <div className="kpi-icon success">
                        <span>✓</span>
                      </div>
                      <div className="kpi-body">
                        <div className="kpi-value">
                          {kpi?.chapters_done ?? 0} <span className="kpi-unit">/ {kpi?.chapters_total ?? 0}</span>
                        </div>
                        <div className="kpi-label">已定稿章数</div>
                      </div>
                    </div>
                    <div className="kpi-card">
                      <div className="kpi-icon info">
                        <span>✎</span>
                      </div>
                      <div className="kpi-body">
                        <div className="kpi-value">{wordCountFmt(kpi?.word_count ?? 0)}</div>
                        <div className="kpi-label">总字数{hasWordCount ? '' : '（尚无定稿）'}</div>
                      </div>
                    </div>
                    <div className="kpi-card">
                      <div className="kpi-icon warning">
                        <span>⟡</span>
                      </div>
                      <div className="kpi-body">
                        <div className="kpi-value">{kpi?.foreshadow_open ?? 0}</div>
                        <div className="kpi-label">待回收伏笔</div>
                      </div>
                    </div>
                    <div className="kpi-card health-card">
                      <div className="kpi-body">
                        <div className="kpi-value health-num">{kpi?.health ?? 0}</div>
                        <div className="kpi-label">健康分（预估）</div>
                      </div>
                      <svg className="kpi-ring" viewBox="0 0 44 44">
                        <circle className="kpi-ring-bg" cx="22" cy="22" r="18" fill="none" strokeWidth="4"></circle>
                        <circle className="kpi-ring-fill" cx="22" cy="22" r="18" fill="none" strokeWidth="4" strokeLinecap="round" strokeDasharray={`${kpi?.health ?? 0},100`} pathLength="100"></circle>
                      </svg>
                    </div>
                  </div>
                </section>

                <section className="section-block">
                  <div className="section-header">
                    <h2 className="section-title">
                      <span className="title-icon">⊙</span>章节时间线
                    </h2>
                    <Link to="/maestro" className="section-link">管理骨架 →</Link>
                  </div>
                  {data.timeline.length === 0 ? (
                    <div className="char-empty">尚无章节，先去「主笔共创」生成骨架</div>
                  ) : (
                    <div className="timeline-vertical">
                      {data.timeline.map((c) => (
                        <div className={`tl-node ${c.status}`.trim()} key={c.chapter_id}>
                          <div className="tl-dot"></div>
                          <div className="tl-card">
                            <div className="tl-chapter">第 {c.order_no} 章</div>
                            <div className="tl-title">{c.title}</div>
                            <div className="tl-desc">
                              {c.word_count > 0 ? `已定稿 ${c.word_count} 字` : (c.tone ? `基调 ${c.tone}` : '尚无场景')}
                            </div>
                            <span className={`status-badge ${c.badge}`}>{c.label}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                <section className="section-block">
                  <div className="section-header">
                    <h2 className="section-title">
                      <span className="title-icon">☑</span>近期待办
                    </h2>
                  </div>
                  {data.todos.length === 0 ? (
                    <div className="char-empty">暂无待办，一切正常</div>
                  ) : (
                    <div className="todo-list">
                      {data.todos.map((t, i) => {
                        const meta = TODO_ICON[t.severity] ?? TODO_ICON.info;
                        return (
                          <div className="todo-item" key={i}>
                            <div className={`todo-icon ${meta.icon}`}>
                              <span>{meta.char}</span>
                            </div>
                            <div className="todo-body">
                              <div className="todo-title">{t.title}</div>
                              <div className={`todo-desc ${t.severity === 'danger' || t.severity === 'warn' ? 'danger' : ''}`}>{t.desc}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>
              </>
            )}
          </main>

          <aside className="right-rail">
            <div className="rail-card">
              <div className="rail-title">
                <span>❝</span> 金句池
              </div>
              {!data || data.quotes.length === 0 ? (
                <div className="char-empty">暂无金句（定稿正文后自动抽取）</div>
              ) : (
                <div className="quote-list">
                  {data.quotes.map((q, i) => (
                    <div className="quote-item" key={i}>
                      <p className="quote-text">{q.text}</p>
                      <span className="quote-author">—— {q.author}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rail-card">
              <div className="rail-title">
                <span>✦</span> 近 4 周写作热力
              </div>
              {!data || data.heat.every((o) => o === 0) ? (
                <div className="char-empty">暂无成文记录<br />定稿新场景后按日更新</div>
              ) : (
                <>
                  <div className="heat-grid">
                    {data.heat.map((o, i) => {
                      const max = Math.max(...data.heat, 1);
                      return <div className="heat-cell" style={{ opacity: Math.max(0.08, o / max) }} key={i}></div>;
                    })}
                  </div>
                  <div className="heat-labels">
                    <span>少</span>
                    <span>多</span>
                  </div>
                </>
              )}
            </div>

            <div className="rail-card">
              <div className="rail-title">
                <span>☷</span> 快速操作
              </div>
              <div className="quick-actions">
                <Link to="/director">
                  <button className="quick-btn">
                    <span className="quick-icon">▶</span>
                    <span>继续导演</span>
                  </button>
                </Link>
                <Link to="/maestro">
                  <button className="quick-btn">
                    <span className="quick-icon">✎</span>
                    <span>新建章节</span>
                  </button>
                </Link>
                <Link to="/maestro">
                  <button className="quick-btn">
                    <span className="quick-icon">✦</span>
                    <span>主笔共创</span>
                  </button>
                </Link>
                <Link to="/characters">
                  <button className="quick-btn">
                    <span className="quick-icon">☑</span>
                    <span>检查信念</span>
                  </button>
                </Link>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}