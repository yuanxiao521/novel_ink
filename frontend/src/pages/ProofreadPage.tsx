import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { GlobalSearch } from '../components/common/GlobalSearch';
import { exportBookUrl, fetchCollation } from '../api/novel';
import type { Collation } from '../api/novel';
import { EmptyState } from '../components/common/EmptyState';
import { useWorkspace } from '../context/WorkspaceContext';

/**
 * 逐章校对（成书收口 · C）。
 *
 * 回答作者在「要成书了」这一刻最关心的三个问题：
 *   ① 哪章还没写完？② 哪一场还没定稿？③ 现在够不够导出？
 * 数据来自 `GET /books/{id}/collation`（0-token 派生，字数与概览/导出同口径）。
 * 每场都能一键跳到正文协作去改——校对不是只读，是改稿入口。
 */
/** 章标题本身常已含「第 N 章」（主笔排的、手建的都这样）→ 别重复前缀。 */
const CHAPTER_RE = /^第\s*[0-9一二三四五六七八九十百零]+\s*章/;
function chapterLabel(ch: { order_no: number; title: string }): string {
  const title = (ch.title || '').trim() || '（无题）';
  if (CHAPTER_RE.test(title)) return title;
  return `第 ${ch.order_no || '?'} 章 ${title}`;
}

export function ProofreadPage() {
  const nav = useNavigate();
  const { bookId, tree, setChapter, setScene } = useWorkspace();
  const [data, setData] = useState<Collation | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!bookId) { setData(null); return; }
    setLoading(true);
    setErr('');
    fetchCollation(bookId)
      .then(setData)
      .catch((e) => setErr('校对数据加载失败：' + (e instanceof Error ? e.message : e)))
      .finally(() => setLoading(false));
  }, [bookId, tree]);

  const kpi = data?.kpi;
  const canExport = (kpi?.word_count ?? 0) > 0;
  const allDone = (kpi?.chapters_total ?? 0) > 0 && kpi?.chapters_done === kpi?.chapters_total;

  return (
    <div className="app-shell">
      <Sidebar active="proofread" />
      <div className="main-col">
        <header className="top-header">
          <div className="header-left">
            <h1 className="book-title-main">{data?.title || '（未选书）'}</h1>
            <span className="genre-tag">逐章校对 · 成书</span>
          </div>
          <GlobalSearch />
          <div className="header-right">
            {canExport && bookId && (
              <a className="btn-primary" href={exportBookUrl(bookId)} title="导出全书 Markdown（仅含已定稿场景）">
                <span>导出成书</span>
              </a>
            )}
            <Link className="pf-back" to={'/dashboard?book=' + bookId}>← 概览复盘</Link>
          </div>
        </header>

        <div className="content-area">
          <main className="main-content">
            {err && <div className="ws-msg err" role="alert"><span>{err}</span></div>}
            {!data && !err && (
              <EmptyState
                icon="▤"
                title={loading ? '正在汇总各章…' : '选一本书开始校对'}
                desc="这里按章列出场景、字数与定稿状态，每场都能一键跳去改写。"
              />
            )}

            {data && (
              <>
                <section className="kpi-section">
                  <div className="kpi-grid">
                    <div className="kpi-card">
                      <div className="kpi-icon success"><span>✓</span></div>
                      <div className="kpi-body">
                        <div className="kpi-value">{kpi?.chapters_done ?? 0} <span className="kpi-unit">/ {kpi?.chapters_total ?? 0}</span></div>
                        <div className="kpi-label">已定稿章数</div>
                      </div>
                    </div>
                    <div className="kpi-card">
                      <div className="kpi-icon info"><span>✎</span></div>
                      <div className="kpi-body">
                        <div className="kpi-value">{kpi?.word_count ?? 0}</div>
                        <div className="kpi-label">总字数</div>
                      </div>
                    </div>
                    <div className="kpi-card">
                      <div className="kpi-icon warning"><span>⟡</span></div>
                      <div className="kpi-body">
                        <div className="kpi-value">{kpi?.unfinished_scenes ?? 0} <span className="kpi-unit">/ {kpi?.scene_total ?? 0}</span></div>
                        <div className="kpi-label">未定稿场景</div>
                      </div>
                    </div>
                    <div className="kpi-card">
                      <div className={'kpi-icon ' + (allDone ? 'success' : 'warning')}><span>{allDone ? '★' : '…'}</span></div>
                      <div className="kpi-body">
                        <div className="kpi-value">{allDone ? '可成书' : '未完成'}</div>
                        <div className="kpi-label">成书状态</div>
                      </div>
                    </div>
                  </div>
                </section>

                {(data.chapters ?? []).map((ch) => (
                  <section className="section-block" key={ch.chapter_id}>
                    <div className="section-header">
                      <h2 className="section-title">
                        <span className="title-icon">▤</span>
                        {chapterLabel(ch)}
                      </h2>
                      <div className="pf-chapter-meta">
                        <span>{ch.word_count} 字</span>
                        <span>{ch.scene_count} 场</span>
                        {ch.word_target > 0 && <span>目标 {ch.word_target} 字</span>}
                        <span className={'status-badge ' + (ch.done ? 'done' : 'draft')}>{ch.done ? '已定稿' : '待定稿'}</span>
                      </div>
                    </div>
                    {ch.summary && <p className="pf-chapter-summary">{ch.summary}</p>}
                    {ch.scenes.length === 0 ? (
                      <EmptyState
                        compact
                        icon="▫"
                        title="这章还没有场景"
                        desc="章节要有场景才能落正文。"
                        primary={{ label: '✦ 去主笔创作加场景', onClick: () => nav('/maestro') }}
                      />
                    ) : (
                      <div className="pf-scenes">
                        {ch.scenes.map((s) => (
                          <div className="pf-scene" key={s.scene_id}>
                            <div className="pf-scene-head">
                              <span className="pf-scene-title">{s.title || '（未命名场景）'}</span>
                              <span className={'pf-scene-state ' + (s.finalized ? 'done' : 'todo')}>
                                {s.finalized ? s.word_count + ' 字 · 已定稿' : '未定稿'}
                              </span>
                              <Link
                                className="pf-go"
                                to={'/studio/' + s.scene_id}
                                onClick={() => { setChapter(ch.chapter_id); setScene(s.scene_id); }}
                              >
                                {s.finalized ? '去改 →' : '去写 →'}
                              </Link>
                            </div>
                            <p className="pf-scene-preview">{s.preview || '（还没有正文）'}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                ))}

                {(data.chapters ?? []).length === 0 && (
                  <EmptyState
                    icon="▣"
                    title="这本书还没有章节"
                    desc="先让主笔按一句话方向排一批骨架（A4 分批交付，每批复核后落库）。"
                    primary={{ label: '✦ 去主笔创作', onClick: () => nav('/maestro') }}
                  />
                )}
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
