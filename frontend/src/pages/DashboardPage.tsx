import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import { useDialog } from '../components/common/Dialog';
import { createBook, exportBookUrl, fetchDashboard, fetchGlobalView } from '../api/novel';
import type { DashboardData, GlobalView } from '../api/novel';
import { useWorkspace } from '../context/WorkspaceContext';
import { TensionPanel } from '../components/dashboard/TensionPanel';
import { GlobalSearch } from '../components/common/GlobalSearch';
import { EmptyState } from '../components/common/EmptyState';

const TODO_ICON: Record<string, { icon: string; char: string }> = {
  ok: { icon: 'success', char: '✓' },
  info: { icon: 'info', char: '✎' },
  warn: { icon: 'warning', char: '!' },
  danger: { icon: 'warning', char: '!' },
};

export function DashboardPage() {
  const nav = useNavigate();
  const { showPrompt } = useDialog();
  // 书 = 全局上下文（URL ?book= 由上下文统一解析；本页不再自持一份）
  const { books, bookId, setBook, refreshBooks } = useWorkspace();
  const [data, setData] = useState<DashboardData | null>(null);
  const [gview, setGview] = useState<GlobalView | null>(null);
  const [err, setErr] = useState('');
  /** 金句池默认只展示 1 条（3 条会把右栏撑满，把热力图挤到下面） */
  const [quotesOpen, setQuotesOpen] = useState(false);

  useEffect(() => {
    if (!bookId) {
      setData(null);
      return;
    }
    setErr('');
    fetchDashboard(bookId)
      .then(setData)
      .catch((e) => setErr(`概览加载失败：${e instanceof Error ? e.message : e}`));
    // S3 全局张力视图（失败不影响概览主体，仅该区块显示加载态）
    fetchGlobalView(bookId)
      .then(setGview)
      .catch(() => setGview(null));
  }, [bookId]);

  const newBook = async () => {
    const title = await showPrompt('新书名', '新书');
    if (!title) return;
    try {
      const r = await createBook({ title, genre: '玄幻', status: 'planned' });
      refreshBooks();
      setBook(r.id);
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
          {/* 跨书搜索（B38 后续）：接了 GET /api/v1/search，命中项直接跳到对应页 */}
          <GlobalSearch />
          <div className="header-right">
            {/* 判据 = 有任意定稿正文（与后端 export 的 `final_prose` 真值判断一致）；
                不用 chapters_done——它要求整章全定稿，会让"有正文但未全定稿"的书看不到入口 */}
            {(kpi?.word_count ?? 0) > 0 && bookId && (
              <a className="btn-primary" href={exportBookUrl(bookId)} title="导出全书 markdown（仅含已定稿场景）">
                <span>导出成书</span>
              </a>
            )}
            <button className="btn-primary" onClick={newBook}>
              <span>＋ 新建书</span>
            </button>
            <Link to="/director">
              <button className="btn-primary">
                <span>进入导演台</span>
              </button>
            </Link>
            {/* v1.46：健康分已在下方 KPI 卡展示（带环形进度），顶栏不再重复 */}
            <ThemeToggle />
            <div className="user-avatar">作</div>
          </div>
        </header>

        <div className="char-scope-row">
          <label className="char-scope-label">书</label>
          <select className="char-scope-select" value={bookId} onChange={(e) => setBook(e.target.value)}>
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
              <EmptyState
                icon="◔"
                title="还没有可复盘的内容"
                desc={err || '先去「主笔共创」按方向排一版骨架，或在「导演台」推演定稿——张力曲线、伏笔网络与体检都在这里汇总。'}
                primary={{ label: '✦ 去主笔共创', onClick: () => nav('/maestro') }}
              />
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
                    <div className="section-links">
                      <Link to="/proofread" className="section-link">逐章校对 →</Link>
                      <Link to="/maestro" className="section-link">管理骨架 →</Link>
                    </div>
                  </div>
                  {data.timeline.length === 0 ? (
                    <EmptyState
                      compact
                      icon="▣"
                      title="尚无章节"
                      desc="去「主笔共创」按一句话方向排一版骨架，时间线会按章显示定稿进度。"
                      primary={{ label: '✦ 让主笔排骨架', onClick: () => nav('/maestro') }}
                    />
                  ) : (
                    <div className="timeline-vertical">
                      {data.timeline.map((c) => (
                        <div className={`tl-node ${c.status}`.trim()} key={c.chapter_id}>
                          <div className="tl-dot"></div>
                          <div className="tl-card">
                            <div className="tl-chapter">第 {c.order_no} 章</div>
                            <div className="tl-title">{c.title}</div>
                            <div className="tl-desc">
                              {c.word_count > 0 ? `${c.word_count} 字` : (c.tone ? `基调 ${c.tone}` : '尚无场景')}
                            </div>
                            <span className={`status-badge ${c.badge}`}>{c.label}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                <TensionPanel view={gview} />

                <section className="section-block">
                  <div className="section-header">
                    <h2 className="section-title">
                      <span className="title-icon">☑</span>近期待办
                    </h2>
                  </div>
                  {data.todos.length === 0 ? (
                    <EmptyState compact icon="✓" title="暂无待办" desc="伏笔、信念与结构都没发现要处理的事。" />
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
                <EmptyState compact icon="❝" title="暂无金句" desc="写完章/场正文后自动抽取（支持「」与“”对白）。" />
              ) : (
                <>
                  <div className="quote-list">
                    {(quotesOpen ? data.quotes : data.quotes.slice(0, 1)).map((q, i) => (
                      <div className="quote-item" key={i}>
                        <p className="quote-text">{q.text}</p>
                        <span className="quote-author">—— {q.author}</span>
                      </div>
                    ))}
                  </div>
                  {data.quotes.length > 1 && (
                    <button className="rail-more" onClick={() => setQuotesOpen((v) => !v)}>
                      {quotesOpen ? '收起 ▴' : `展开全部 ${data.quotes.length} 条 ▾`}
                    </button>
                  )}
                </>
              )}
            </div>

            <div className="rail-card">
              <div className="rail-title">
                <span>✦</span> 近 4 周写作热力
              </div>
              {!data || data.heat.every((o) => o === 0) ? (
                <EmptyState compact icon="▦" title="暂无成文记录" desc="写完章/场正文后按日更新（章正文与场景正文都计入）。" />
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
                <Link to="/proofread">
                  <button className="quick-btn">
                    <span className="quick-icon">▤</span>
                    <span>逐章校对</span>
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