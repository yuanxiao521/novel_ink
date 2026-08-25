import { Link } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';

const CHAPTERS = [
  { n: 1, title: '雨夜归家', desc: '陈默回到老宅，发现书房的门虚掩着。', status: 'done', label: '已定稿' },
  { n: 2, title: '书房初遇', desc: '陈默与李文在书房碰面，气氛微妙。', status: 'done', label: '已定稿' },
  { n: 3, title: '周婶送茶', desc: '周婶端茶上来，撞见两人对峙。', status: 'done', label: '已定稿' },
  { n: 4, title: '保险柜疑云', desc: '陈默发现保险柜被动过，疑心渐起。', status: 'done', label: '已定稿' },
  { n: 5, title: '沉默对峙', desc: '陈默与李文在沉默中互相试探。', status: 'current', label: '导演中', card: 'current-card', badge: 'draft' },
  { n: 6, title: '文件异动', desc: '一份神秘文件从书架上消失。', status: 'planned', label: '规划中', badge: 'planned' },
  { n: 7, title: '雨夜的试探', desc: '下一章大纲目标：李文主动出击试探陈默底线。', status: 'planned next', label: '下章目标', badge: 'next-badge' },
];

const TODOS = [
  { icon: 'primary', char: '◎', title: '下一章大纲目标', desc: '第 7 章 · 雨夜的试探：李文决定主动出击，试探陈默到底知道多少', danger: false },
  { icon: 'warning', char: '!', title: '逾期伏笔提醒', desc: '「保险柜里的信」计划第五章回收，当前已进入第七章', danger: true, cls: 'warn' },
  { icon: 'info', char: '✎', title: '人物信念待确认', desc: '陈默的"李文是无辜的"这一条信念与证据冲突，建议导演介入', danger: false },
  { icon: 'success', char: '✓', title: '体检报告', desc: '目前没有严重一致性问题，2 条轻微警告已列出', danger: false },
];

const QUOTES = [
  { text: '"逻辑我不懂，但人类的考核标准多变。"', author: '—— 李文' },
  { text: '"雨下得越久，藏在雨里的东西就越清楚。"', author: '—— 陈默独白' },
  { text: '"有些门虚掩着，不是为了让人进去，是为了让人起疑。"', author: '—— 旁白' },
];

const HEAT = [0.15, 0.4, 0.75, 0.3, 0.85, 0.55, 0.2, 0.5, 0.25, 0.65, 0.4, 0.8, 0.35, 0.6, 0.7, 0.2, 0.45, 0.9, 0.55, 0.3, 0.65, 0.15, 0.5, 0.4, 0.75, 0.35, 0.6, 0.25];

export function DashboardPage() {
  return (
    <div className="app-shell">
      <Sidebar active="dashboard" />
      <div className="main-col">
        <header className="top-header">
          <div className="header-left">
            <h1 className="book-title-main">雨夜书房</h1>
            <span className="genre-tag">悬疑 · 中篇</span>
          </div>
          <div className="header-search">
            <span className="search-icon">⌕</span>
            <input type="text" placeholder="跨书搜索角色、伏笔、章节…" />
          </div>
          <div className="header-right">
            <Link to="/director">
              <button className="btn-primary">
                <span>进入导演台</span>
              </button>
            </Link>
            <span className="health-badge warn">
              <span className="health-dot"></span>
              健康分 82
            </span>
            <ThemeToggle />
            <div className="user-avatar">作</div>
          </div>
        </header>

        <div className="content-area">
          <main className="main-content">
            <section className="kpi-section">
              <div className="kpi-grid">
                <div className="kpi-card">
                  <div className="kpi-icon success">
                    <span>✓</span>
                  </div>
                  <div className="kpi-body">
                    <div className="kpi-value">
                      7 <span className="kpi-unit">/ 23</span>
                    </div>
                    <div className="kpi-label">已定稿章数</div>
                  </div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-icon info">
                    <span>✎</span>
                  </div>
                  <div className="kpi-body">
                    <div className="kpi-value">
                      3.2<span className="kpi-unit-small"> 万字</span>
                    </div>
                    <div className="kpi-label">总字数</div>
                  </div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-icon warning">
                    <span>⟡</span>
                  </div>
                  <div className="kpi-body">
                    <div className="kpi-value">5</div>
                    <div className="kpi-label">待回收伏笔</div>
                  </div>
                </div>
                <div className="kpi-card health-card">
                  <div className="kpi-body">
                    <div className="kpi-value health-num">82</div>
                    <div className="kpi-label">健康分</div>
                  </div>
                  <svg className="kpi-ring" viewBox="0 0 44 44">
                    <circle className="kpi-ring-bg" cx="22" cy="22" r="18" fill="none" strokeWidth="4"></circle>
                    <circle className="kpi-ring-fill" cx="22" cy="22" r="18" fill="none" strokeWidth="4" strokeLinecap="round" strokeDasharray="82,100" pathLength="100"></circle>
                  </svg>
                </div>
              </div>
            </section>

            <section className="section-block">
              <div className="section-header">
                <h2 className="section-title">
                  <span className="title-icon">⊙</span>章节时间线
                </h2>
                <a className="section-link" href="#">
                  查看全部 →
                </a>
              </div>
              <div className="timeline-vertical">
                {CHAPTERS.map((c) => (
                  <div className={`tl-node ${c.status}`.trim()} key={c.n}>
                    <div className="tl-dot"></div>
                    <div className={`tl-card ${c.card || ''}`.trim()}>
                      <div className="tl-chapter">第 {c.n} 章</div>
                      <div className="tl-title">{c.title}</div>
                      <div className="tl-desc">{c.desc}</div>
                      <span className={`status-badge ${c.badge || c.status}`}>{c.label}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="section-block">
              <div className="section-header">
                <h2 className="section-title">
                  <span className="title-icon">☑</span>近期待办
                </h2>
              </div>
              <div className="todo-list">
                {TODOS.map((t, i) => (
                  <div className="todo-item" key={i}>
                    <div className={`todo-icon ${t.icon}`}>
                      <span>{t.char}</span>
                    </div>
                    <div className="todo-body">
                      <div className="todo-title">{t.title}</div>
                      <div className={`todo-desc ${t.danger ? 'danger' : ''}`}>{t.desc}</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </main>

          <aside className="right-rail">
            <div className="rail-card">
              <div className="rail-title">
                <span>❝</span> 金句池
              </div>
              <div className="quote-list">
                {QUOTES.map((q, i) => (
                  <div className="quote-item" key={i}>
                    <p className="quote-text">{q.text}</p>
                    <span className="quote-author">{q.author}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rail-card">
              <div className="rail-title">
                <span>✦</span> 近 7 日写作热力
              </div>
              <div className="heat-grid">
                {HEAT.map((o, i) => (
                  <div className="heat-cell" style={{ opacity: o }} key={i}></div>
                ))}
              </div>
              <div className="heat-labels">
                <span>少</span>
                <span>多</span>
              </div>
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
                <button className="quick-btn">
                  <span className="quick-icon">✎</span>
                  <span>新建章节</span>
                </button>
                <button className="quick-btn">
                  <span className="quick-icon">✦</span>
                  <span>生成记忆包</span>
                </button>
                <button className="quick-btn">
                  <span className="quick-icon">☑</span>
                  <span>跑一次体检</span>
                </button>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}