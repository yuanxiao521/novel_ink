import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ThemeToggle } from '../components/backoffice/ThemeToggle';
import chenImg from '../assets/portrait-chenmo.png';
import liwImg from '../assets/portrait-liwen.png';
import zhouImg from '../assets/portrait-zhoushen.png';

type Tab = 'basic' | 'belief' | 'memory' | 'relation';

const CHAR_LIST = [
  { id: 'chenmo', name: '陈默', role: '主角 · 大学讲师', meta: '12 信念 · 28 记忆', img: chenImg, badge: '主角' },
  { id: 'liwen', name: '李文', role: '配角 · 研究员', meta: '9 信念 · 21 记忆', img: liwImg },
  { id: 'zhoushen', name: '周婶', role: '配角 · 管家', meta: '6 信念 · 14 记忆', img: zhouImg },
];

const BELIEFS = [
  { cls: 'witness', tag: '亲见', tagCls: 'tag-witness', src: '第 4 章 · 书房', text: '保险柜的门半开着，锁孔有新鲜划痕', pct: 95 },
  { cls: 'infer', tag: '推测', tagCls: 'tag-infer', src: '第 5 章 · 对峙后', text: '李文知道保险柜里装的是什么', pct: 70 },
  { cls: 'infer', tag: '推测', tagCls: 'tag-infer', src: '第 3 章 · 送茶', text: '周婶在这个宅子里的时间比她自己说的要长', pct: 55 },
  { cls: 'told', tag: '被告知', tagCls: 'tag-told', src: '第 2 章 · 李文口述', text: '保险柜里是已故先生的研究手稿', pct: 40 },
  { cls: 'witness', tag: '亲见', tagCls: 'tag-witness', src: '第 1 章 · 雨夜归家', text: '书房的门虚掩着，但出门时明明锁好了', pct: 100 },
  { cls: 'doubt', tag: '存疑', tagCls: 'tag-doubt', src: '第 6 章 · 异常', text: '书架第三层的书被动过——但不确定是谁、什么时候', pct: 30 },
];

const MEM_GROUPS = [
  {
    label: '第 6 章 · 文件异动',
    cards: [
      { type: 'event', tag: '关键事件', title: '发现文件不在原位', desc: '书架第三层的那份手稿，位置偏移了大约两厘米。', time: 'T-06', impact: '高', impCls: 'high' },
      { type: 'dialogue', tag: '对话', title: '李文的解释', desc: '「我找东西的时候碰过一下，可能没放齐。」', time: 'T-06', impact: '中', impCls: 'mid' },
    ],
  },
  {
    label: '第 5 章 · 沉默对峙',
    cards: [
      { type: 'emotion', tag: '情感', title: '李文的眼神', desc: '李文看自己的眼神里，有恐惧，也有某种别的东西。', time: 'T-05', impact: '高', impCls: 'high' },
      { type: 'event', tag: '关键事件', title: '沉默的三分钟', desc: '两人在书房里对视了整整三分钟，谁都没先开口。', time: 'T-05', impact: '中', impCls: 'mid' },
      { type: 'observe', tag: '观察', title: '李文的手指', desc: '李文右手食指有微小的颤抖，但被她很快控制住了。', time: 'T-05', impact: '低', impCls: 'low' },
    ],
  },
  {
    label: '第 4 章 · 保险柜疑云',
    cards: [
      { type: 'event', tag: '关键事件', title: '保险柜门半开', desc: '锁孔周围有新鲜的划痕，像是用错了钥匙。', time: 'T-04', impact: '高', impCls: 'high' },
      { type: 'observe', tag: '观察', title: '周婶的表情', desc: '提到保险柜时，周婶的嘴角有极微的抽动。', time: 'T-04', impact: '中', impCls: 'mid' },
    ],
  },
];

const RELATIONS = [
  { name: '李文', img: liwImg, type: '互相试探', typeCls: 'type-strange', desc: '陈默怀疑李文知道真相，李文则害怕被看穿', pct: 60, label: '张力' },
  { name: '周婶', img: zhouImg, type: '主仆', typeCls: 'type-neutral', desc: '表面是管家与雇主的关系，陈默隐约觉得周婶知道更多', pct: 30, label: '张力' },
  { name: '先生（已故）', img: null, ph: '先', type: '敬仰', typeCls: 'type-respect', desc: '陈默的恩师，三年前去世，死因存疑', pct: 85, label: '羁绊' },
  { name: '老赵（警官）', img: null, ph: '警', type: '盟友', typeCls: 'type-allies', desc: '旧识，偶尔提供一些"非正式"的信息', pct: 70, label: '信任' },
];

export function CharactersPage() {
  const [tab, setTab] = useState<Tab>('basic');
  const [selected, setSelected] = useState('chenmo');
  const [beliefFilter, setBeliefFilter] = useState('all');
  const cur = CHAR_LIST.find((c) => c.id === selected) || CHAR_LIST[0];

  return (
    <div className="app-shell">
      <Sidebar active="characters" />
      <div className="main-col">
        <header className="top-header">
          <div className="header-left">
            <h1 className="book-title-main">人物管理</h1>
            <span className="genre-tag">3 位登场角色</span>
          </div>
          <div className="header-search">
            <span className="search-icon">⌕</span>
            <input type="text" placeholder="搜索角色名、信念、记忆…" />
          </div>
          <div className="header-right">
            <Link to="/director">
              <button className="btn-primary">
                <span>进入导演台</span>
              </button>
            </Link>
            <ThemeToggle />
            <div className="user-avatar">作</div>
          </div>
        </header>

        <div className="char-page-body">
          <aside className="char-list-panel">
            <div className="panel-header">
              <span className="panel-title">角色列表</span>
              <button className="add-char-btn">
                <span>+</span>
              </button>
            </div>
            <div className="char-list">
              {CHAR_LIST.map((c) => (
                <div
                  className={`char-list-item ${selected === c.id ? 'active' : ''}`}
                  data-char={c.id}
                  key={c.id}
                  onClick={() => setSelected(c.id)}
                >
                  <img className="char-list-avatar" src={c.img} alt={c.name} />
                  <div className="char-list-info">
                    <div className="char-list-name">{c.name}</div>
                    <div className="char-list-role">{c.role}</div>
                  </div>
                  <span className="char-list-count">{c.meta}</span>
                </div>
              ))}
            </div>
            <div className="char-list-footer">
              <button className="relation-overview-btn">
                <span>🕸</span> 关系总览
              </button>
            </div>
          </aside>

          <main className="char-detail-panel">
            <div className="char-detail-header">
              <div className="char-portrait-large">
                <img src={cur.img} alt={`${cur.name}立绘`} />
                <span className="char-role-badge">{cur.badge || '配角'}</span>
              </div>
              <div className="char-basic-info">
                <h2 className="char-name-large">{cur.name}</h2>
                <p className="char-tagline">「沉默的观察者，真相的追寻者。」</p>
                <div className="char-meta-row">
                  <span className="char-meta-item">
                    <strong>身份：</strong>大学讲师 / 业余侦探
                  </span>
                  <span className="char-meta-item">
                    <strong>年龄：</strong>32 岁
                  </span>
                  <span className="char-meta-item">
                    <strong>性格：</strong>沉稳、敏锐、固执
                  </span>
                </div>
                <div className="char-meta-row">
                  <span className="char-meta-item">
                    <strong>核心目标：</strong>查明保险柜背后的真相
                  </span>
                  <span className="char-meta-item">
                    <strong>人物弧光：</strong>从旁观者到入局者
                  </span>
                </div>
              </div>
              <div className="char-stats-col">
                <div className="char-stat">
                  <div className="char-stat-num">12</div>
                  <div className="char-stat-label">信念</div>
                </div>
                <div className="char-stat">
                  <div className="char-stat-num">28</div>
                  <div className="char-stat-label">记忆</div>
                </div>
                <div className="char-stat">
                  <div className="char-stat-num">5</div>
                  <div className="char-stat-label">关系</div>
                </div>
              </div>
            </div>

            <div className="char-tabs">
              {(
                [
                  ['basic', '基本设定'],
                  ['belief', '信念账本'],
                  ['memory', '记忆卡片'],
                  ['relation', '关系网'],
                ] as Array<[Tab, string]>
              ).map(([k, label]) => (
                <button className={`char-tab ${tab === k ? 'active' : ''}`} data-tab={k} key={k} onClick={() => setTab(k)}>
                  {label}
                </button>
              ))}
            </div>

            <div className="char-tab-content">
              {tab === 'basic' && (
                <div className="tab-pane active" data-pane="basic">
                  <div className="info-grid">
                    <div className="info-card">
                      <div className="info-card-title">外貌描写</div>
                      <div className="info-card-body">
                        <p>身形清瘦，常穿深色衬衫，戴一副细框眼镜。眼神沉静，看人时总像是在测量什么。左手食指有一道旧伤疤，从不解释来源。</p>
                      </div>
                    </div>
                    <div className="info-card">
                      <div className="info-card-title">背景故事</div>
                      <div className="info-card-body">
                        <p>曾是大学里最年轻的逻辑学讲师，三年前因故离开学术界。现在靠翻译侦探小说维生，偶尔帮朋友处理一些"逻辑上的小问题"。</p>
                      </div>
                    </div>
                    <div className="info-card">
                      <div className="info-card-title">行为习惯</div>
                      <div className="info-card-body">
                        <ul className="info-list">
                          <li>思考时会无意识地转动左手食指上的戒指</li>
                          <li>说话语速偏慢，习惯先停顿再开口</li>
                          <li>每天固定下午三点喝一杯黑咖啡</li>
                          <li>对秩序感有近乎偏执的要求</li>
                        </ul>
                      </div>
                    </div>
                    <div className="info-card">
                      <div className="info-card-title">人物弧光</div>
                      <div className="info-card-body">
                        <div className="arc-stage">
                          <div className="arc-label">开端</div>
                          <div className="arc-desc">冷静的旁观者，认为真相是可以被逻辑推导的</div>
                        </div>
                        <div className="arc-arrow">↓</div>
                        <div className="arc-stage">
                          <div className="arc-label">转折</div>
                          <div className="arc-desc">发现自己也在局中，逻辑开始失效</div>
                        </div>
                        <div className="arc-arrow">↓</div>
                        <div className="arc-stage">
                          <div className="arc-label">终局</div>
                          <div className="arc-desc">接受真相的模糊性，选择相信而非证明</div>
                        </div>
                      </div>
                    </div>
                    <div className="info-card full-width">
                      <div className="info-card-title">口头禅 / 标志性台词</div>
                      <div className="info-card-body">
                        <div className="quote-row">
                          <span className="quote-mark">「</span>
                          <span className="quote-content">雨下得越久，藏在雨里的东西就越清楚。</span>
                          <span className="quote-mark">」</span>
                        </div>
                        <div className="quote-row">
                          <span className="quote-mark">「</span>
                          <span className="quote-content">逻辑我不懂，但人类的考核标准多变。</span>
                          <span className="quote-mark">」</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {tab === 'belief' && (
                <div className="tab-pane active" data-pane="belief">
                  <div className="belief-toolbar">
                    <div className="belief-filter">
                      {(
                        [
                          ['all', '全部 12'],
                          ['witness', '亲见 4'],
                          ['infer', '推测 5'],
                          ['told', '被告知 2'],
                          ['doubt', '存疑 1'],
                        ] as Array<[string, string]>
                      ).map(([k, label]) => (
                        <span className={`filter-chip ${beliefFilter === k ? 'active' : ''}`} data-filter={k} key={k} onClick={() => setBeliefFilter(k)}>
                          {label}
                        </span>
                      ))}
                    </div>
                    <button className="add-belief-btn">
                      <span>+</span> 新增信念
                    </button>
                  </div>
                  <div className="belief-list">
                    {BELIEFS.map((b, i) => (
                      <div className={`belief-card ${b.cls}`} key={i}>
                        <div className="belief-card-head">
                          <span className={`belief-tag ${b.tagCls}`}>{b.tag}</span>
                          <span className="belief-source">{b.src}</span>
                        </div>
                        <div className="belief-text">{b.text}</div>
                        <div className="belief-card-foot">
                          <span className="belief-certainty">确信度 {b.pct}%</span>
                          <div className="belief-actions">
                            <button className="belief-action">编辑</button>
                            <button className="belief-action danger">删除</button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {tab === 'memory' && (
                <div className="tab-pane active" data-pane="memory">
                  <div className="memory-toolbar">
                    <div className="memory-filter">
                      <span className="filter-chip active">全部 28</span>
                      <span className="filter-chip">关键事件 8</span>
                      <span className="filter-chip">对话 12</span>
                      <span className="filter-chip">观察 5</span>
                      <span className="filter-chip">情感 3</span>
                    </div>
                    <button className="add-memory-btn">
                      <span>+</span> 新增记忆
                    </button>
                  </div>
                  <div className="memory-timeline">
                    {MEM_GROUPS.map((g, i) => (
                      <div className="mem-group" key={i}>
                        <div className="mem-group-label">{g.label}</div>
                        <div className="mem-cards-row">
                          {g.cards.map((c, j) => (
                            <div className={`mem-card type-${c.type}`} key={j}>
                              <div className="mem-card-tag">{c.tag}</div>
                              <div className="mem-card-title">{c.title}</div>
                              <div className="mem-card-desc">{c.desc}</div>
                              <div className="mem-card-foot">
                                <span className="mem-time">{c.time}</span>
                                <span className={`mem-impact ${c.impCls}`}>影响：{c.impact}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {tab === 'relation' && (
                <div className="tab-pane active" data-pane="relation">
                  <div className="relation-layout">
                    <div className="relation-list-col">
                      <div className="section-subtitle">陈默的关系</div>
                      <div className="relation-list">
                        {RELATIONS.map((r, i) => (
                          <div className="relation-item" key={i}>
                            {r.img ? (
                              <img className="relation-avatar" src={r.img} alt={r.name} />
                            ) : (
                              <div className="relation-avatar placeholder">{r.ph}</div>
                            )}
                            <div className="relation-info">
                              <div className="relation-name">{r.name}</div>
                              <div className={`relation-type ${r.typeCls}`}>
                                <span className="rel-dot"></span> {r.type}
                              </div>
                              <div className="relation-desc">{r.desc}</div>
                            </div>
                            <div className="relation-strength">
                              <div className="strength-bar">
                                <div className="strength-fill" style={{ width: `${r.pct}%` }}></div>
                              </div>
                              <span className="strength-label">{r.label} {r.pct}%</span>
                            </div>
                          </div>
                        ))}
                      </div>
                      <button className="add-relation-btn">
                        <span>+</span> 添加关系
                      </button>
                    </div>
                    <div className="relation-viz-col">
                      <div className="section-subtitle">关系示意</div>
                      <div className="relation-graph">
                        <div className="graph-center">
                          <div className="graph-node center">
                            <img src={chenImg} alt="陈默" />
                            <span>陈默</span>
                          </div>
                        </div>
                        <div className="graph-node top">
                          <img src={liwImg} alt="李文" />
                          <span>李文</span>
                          <span className="graph-edge edge-strange">互相试探</span>
                        </div>
                        <div className="graph-node right">
                          <img src={zhouImg} alt="周婶" />
                          <span>周婶</span>
                          <span className="graph-edge edge-neutral">主仆</span>
                        </div>
                        <div className="graph-node bottom-left">
                          <div className="graph-avatar placeholder">先</div>
                          <span>先生</span>
                          <span className="graph-edge edge-respect">敬仰</span>
                        </div>
                        <div className="graph-node bottom-right">
                          <div className="graph-avatar placeholder">警</div>
                          <span>老赵</span>
                          <span className="graph-edge edge-allies">盟友</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}