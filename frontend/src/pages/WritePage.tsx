import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { useDialog } from '../components/common/Dialog';
import { useWorkspace } from '../context/WorkspaceContext';
import {
  chapterDraftStream, enqueueChapterInspect, fetchChapterProse, generateChapterSettings,
  listScenes, pollJob, saveChapterProse,
} from '../api/novel';

/* ==========================================================================
   WritePage —— 章级写作主线（v1.46 · 直写链路）

   产品方向 v2：**章是最小可写单位，场是可选的细分**。
   为什么单独一页：原来「正文协作」以场景为准入（没有场景就是死路），
   而绝大多数作者是「先写一章」。所以主线入口必须是「章」：书 → 章 → （可选）场。

   这一页只做四件事：
     1. 看见这一章要写什么（基调 / 字数目标 / 摘要 / 场清单）
     2. 一键「写这一章」——章级流式，按字数目标分段写满（不是写一段）
     3. 手动编辑
     4. 保存（落 chapters.prose_md，唯一真源）
   ========================================================================== */

interface SceneRow {
  id: string; title?: string; goal?: string; final_prose?: string;
  stage_desc?: string; content_desc?: string;
}

export function WritePage() {
  const { chapterId = '' } = useParams<{ chapterId: string }>();
  const nav = useNavigate();
  const { book, chapters } = useWorkspace();
  /** 全书章进度（v1.47 主线视图）：作者一进来就知道"写到第几章了" */
  const chapterIdx = chapters.findIndex((c) => c.id === chapterId);
  const { showConfirm } = useDialog();

  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [tone, setTone] = useState('');
  const [summary, setSummary] = useState('');
  const [wordTarget, setWordTarget] = useState(0);
  const [savedText, setSavedText] = useState('');
  const [scenes, setScenes] = useState<SceneRow[]>([]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [streamChars, setStreamChars] = useState(0);
  const [toast, setToast] = useState('');
  /** P1：设定补齐 / 章级巡检 */
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [inspectBusy, setInspectBusy] = useState(false);
  const [inspect, setInspect] = useState<Record<string, unknown> | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const flash = (t: string) => { setToast(t); window.setTimeout(() => setToast(''), 2400); };

  const load = useCallback(async () => {
    if (!chapterId) return;
    try {
      const d = await fetchChapterProse(chapterId);
      setTitle(d.title || '');
      setTone(d.tone || '');
      setSummary(d.summary || '');
      setWordTarget(d.word_target || 0);
      setText(d.prose_md || d.prose || '');
      setSavedText(d.prose_md || d.prose || '');
    } catch { /* 保持现状 */ }
    try { setScenes((await listScenes(chapterId)) as unknown as SceneRow[]); } catch { setScenes([]); }
  }, [chapterId]);

  useEffect(() => { void load(); }, [load]);

  const words = useMemo(() => text.replace(/\s/g, '').length, [text]);
  const paras = useMemo(() => text.split(/\n\s*\n/).filter((p) => p.trim()), [text]);
  const dirty = text !== savedText;
  const progress = wordTarget > 0 ? Math.min(100, Math.round((words / wordTarget) * 100)) : 0;
  /** 超目标时不能显示"100%"了事——要如实说超了多少（实测 6636/2500 显示 100% 很误导）。 */
  const overTarget = wordTarget > 0 && words > wordTarget;
  /** 场设定缺口（舞台/本场目标/内容描述任一为空）——写前该补，否则写手只能自由发挥 */
  const gapScenes = scenes.filter(
    (s) => !(s.stage_desc || '').trim() || !(s.goal || '').trim() || !(s.content_desc || '').trim(),
  ).length;

  /** 写这一章：章级流式（分段写满，段间自动承接） */
  const writeChapter = async () => {
    if (!chapterId || busy) return;
    if (dirty && !(await showConfirm('覆盖未保存的改动？', '正文区有还没保存的修改，本章重写会覆盖编辑区内容（数据库里已保存的版本不受影响）。', true))) return;
    setBusy(true); setStreamChars(0);
    let acc = '';
    try {
      await chapterDraftStream(
        chapterId,
        (delta) => { acc += delta; setText(acc); setStreamChars(acc.length); },
        (done) => { if (done) { acc = done; setText(done); } },
      );
      const n = acc.replace(/\s/g, '').length;
      if (!n) flash('写手没产出内容（模型未接入或调用失败，见右上角降级提示）');
      else flash(`已写出 ${n} 字（未落库，点「保存」才写库）`);
    } catch (e) {
      flash(`写这一章失败：${String(e instanceof Error ? e.message : e)}`);
    } finally {
      setBusy(false); setStreamChars(0);
    }
  };

  /** P1：补全本章设定（只补空字段，不覆盖手写） */
  const runGenerateSettings = async () => {
    if (!chapterId || settingsBusy) return;
    setSettingsBusy(true);
    try {
      const r = await generateChapterSettings(chapterId, false);
      if (r.error) flash(`补设定失败：${r.error}`);
      else if (r.skipped) flash(r.skipped);
      else flash(`已补齐 ${r.filled} 项设定（章摘要 ${r.chapter_summary ? '已填' : '未动'} · ${r.scenes ?? 0} 场）`);
      await load();
    } catch (e) {
      flash(`补设定失败：${String(e instanceof Error ? e.message : e)}`);
    } finally { setSettingsBusy(false); }
  };

  /** P1：章后巡检（后台队列：体检 ∥ 质检 → 评审 → 裁决 → 记账） */
  const runInspect = async () => {
    if (!chapterId || inspectBusy) return;
    setInspectBusy(true); setInspect(null);
    try {
      const { job_id } = await enqueueChapterInspect(chapterId);
      const st = await pollJob(job_id);
      if (st.status === 'failed') throw new Error(st.error || '巡检失败');
      setInspect((st.result ?? {}) as Record<string, unknown>);
      flash('章级巡检完成（右侧看裁决与明细）');
      await load();
    } catch (e) {
      flash(`巡检失败：${String(e instanceof Error ? e.message : e)}`);
    } finally { setInspectBusy(false); }
  };

  const save = async () => {
    if (!chapterId || busy) return;
    setBusy(true);
    try {
      const r = await saveChapterProse(chapterId, text);
      setSavedText(text);
      flash(r.bookkeeping === 'queued'
        ? `已保存：${r.words} 字 · 记账已排队（后台对照账本，结果进世界状态/伏笔）`
        : `已保存：${r.words} 字`);
    } catch (e) {
      flash(`保存失败：${String(e instanceof Error ? e.message : e)}`);
    } finally { setBusy(false); }
  };

  return (
    <div className="app-shell">
      <Sidebar active="studio" />
      <div className="main-col">
        <ContextBar step="write" />
        <div className="s2-page">
          {toast && <div className="s2-toast">{toast}</div>}

          <header className="s2-head">
            <span className="s2-title">{title || '未命名章'}</span>
            <span className="s2-sub">
              {words} 字
              {wordTarget > 0
                ? (overTarget ? ` / 目标 ${wordTarget} 字（已超 ${words - wordTarget} 字）` : ` / 目标 ${wordTarget} 字（${progress}%）`)
                : ''}
              {' '}· {paras.length} 段
              {dirty && <b className="s2-dirty"> · 未保存</b>}
            </span>
            <span className="s2-src">基调：<b>{tone || '未定'}</b></span>
          </header>

          <div className="s2-actions">
            <button className="ws-btn ws-btn-primary" disabled={busy} onClick={() => void writeChapter()}
                    title="按本章字数目标分段写满（不是写一段），段间自动承接">
              {busy ? (streamChars > 0 ? `写这一章… ${Math.round(streamChars)} 字` : '写这一章…（构思中）') : '✍ 写这一章'}
            </button>
            <button className="ws-btn" disabled={settingsBusy || busy} onClick={() => void runGenerateSettings()}
                    title="主笔补齐本章摘要与各场设定（舞台/本场目标/内容描述）——只补空字段，不覆盖你手写的">
              {settingsBusy ? '🧩 补设定中…' : `🧩 补全设定${gapScenes > 0 ? `（缺 ${gapScenes} 场）` : ''}`}
            </button>
            <button className="ws-btn" disabled={inspectBusy || busy || words === 0} onClick={() => void runInspect()}
                    title="体检 ∥ 质检 → 评审 → 裁决 → 自动记账（后台跑，刷新不断链）">
              {inspectBusy ? '🔍 巡检中…' : '🔍 章后巡检'}
            </button>
            <button className="ws-btn" disabled={busy} onClick={() => setEditing((v) => !v)}>
              {editing ? '✓ 完成编辑' : '✎ 编辑'}
            </button>
            <button className="ws-btn ws-btn-primary" disabled={busy || !dirty} onClick={() => void save()}
                    title={dirty ? '写入 chapters.prose_md' : '与已保存版本一致'}>
              {dirty ? '💾 保存*' : '💾 已保存'}
            </button>
            <span className="s2-spacer" />
            {busy && streamChars > 0 && <span className="s2-hint">正在写：{Math.round(streamChars)} 字…（可刷新，结果会丢，建议写完再离开）</span>}
          </div>

          <div className="s2-body">
            <aside className="s2-info">
              <div className="s2-info-title">这一章</div>
              <div className="s2-kv"><span>全书进度</span>
                <b>{chapters.length > 0
                  ? `第 ${chapterIdx >= 0 ? chapterIdx + 1 : '?'} 章 / 共 ${chapters.length} 章`
                  : '—'}</b>
              </div>
              <div className="s2-kv"><span>字数目标</span><b>{wordTarget > 0 ? `${wordTarget} 字` : '未设（默认 4000）'}</b></div>
              <div className="s2-kv"><span>基调</span><b>{tone || '未定'}</b></div>
              <div className="s2-kv"><span>要完成的事</span>
                <b className="wp-summary" title={summary || '未填'}>{summary || '未填'}</b>
              </div>
              <div className="s2-info-title" style={{ marginTop: 12 }}>场 · {scenes.length}（可选细分）</div>
              {scenes.length === 0 && <div className="s2-hint">本章没拆场也能写——章就是最小单位。需要推演/细纲时再去主笔或导演台拆场。</div>}
              {scenes.map((s) => (
                <div className="s2-ann-mini" key={s.id}>
                  <span className="s2-ann-no">{(s.final_prose || '').trim() ? '✓' : '·'}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    style={{ cursor: 'pointer' }}
                    onClick={() => nav(`/studio/${s.id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') nav(`/studio/${s.id}`); }}
                  >
                    {s.title || '（未命名场）'}
                    {!(s.final_prose || '').trim() && <em className="wp-scene-todo">未写</em>}
                  </span>
                </div>
              ))}
            </aside>

            <section className="s2-prose">
              {editing ? (
                <textarea
                  ref={taRef}
                  className="wp-editor"
                  value={text}
                  placeholder="在这里写这一章，或点上面的「✍ 写这一章」让它先出稿…"
                  onChange={(e) => setText(e.target.value)}
                />
              ) : (
                <div className="wp-prose">
                  {paras.length === 0 && (
                    <div className="ws-empty" style={{ minHeight: 'auto' }}>
                      <div className="ws-empty-title">这一章还没有正文</div>
                      <div className="ws-empty-desc">点上方「✍ 写这一章」按字数目标分段写满，或点「✎ 编辑」自己写。</div>
                      <button className="ws-btn ws-btn-primary" onClick={() => void writeChapter()}>✍ 写这一章</button>
                    </div>
                  )}
                  {paras.map((p, i) => (
                    <p className="wp-para" key={i}>
                      <span className="wp-para-no">¶{i + 1}</span>
                      {p}
                    </p>
                  ))}
                </div>
              )}
            </section>

            <aside className="s2-side">
              <div className="s2-side-head"><span className="ec-title">本章进度</span></div>
              <div className="s2-quality">
                <div className="wp-progress">
                  <div className="wp-progress-bar" style={{ width: `${progress}%` }} />
                </div>
                <div className="s2-kv"><span>已写</span><b>{words} 字</b></div>
                <div className="s2-kv"><span>目标</span><b>{wordTarget > 0 ? `${wordTarget} 字` : '未设'}{overTarget ? `（已超 ${words - wordTarget}）` : ''}</b></div>
                <div className="s2-kv"><span>段落</span><b>{paras.length}</b></div>
                <div className="s2-hint" style={{ marginTop: 12 }}>
                  写作顺序建议：先让开篇立住 → 中间推进二到三次 → 收在一个钩子上。
                  「写这一章」就是按这个节奏分段的。
                </div>
                {book?.title && <div className="s2-hint" style={{ marginTop: 8 }}>书：{book.title}</div>}

                {inspect && (
                  <div className="wp-inspect">
                    <div className="wp-inspect-head">章级巡检</div>
                    {typeof inspect.skipped === 'string' && <div className="s2-hint">{inspect.skipped}</div>}
                    {typeof inspect.verdict === 'string' && (
                      <div className="wp-inspect-verdict">{inspect.verdict}</div>
                    )}
                    {(() => {
                      const sc = (inspect.score ?? {}) as Record<string, unknown>;
                      const rv = (inspect.review ?? {}) as Record<string, unknown>;
                      const vf = (inspect.verify ?? {}) as Record<string, unknown>;
                      const total = typeof sc.total === 'number' ? sc.total : null;
                      return (
                        <>
                          {total !== null && <div className="s2-kv"><span>评审</span><b>{total} 分</b></div>}
                          <div className="s2-kv"><span>体检</span>
                            <b>{String(rv.issues ?? 0)} 条（高危 {String(rv.high ?? 0)}）· 口吻 {String(rv.voice ?? 0)}</b>
                          </div>
                          <div className="s2-kv"><span>质检</span>
                            <b>风险 {String(vf.risks ?? 0)} · 伏笔 {String(vf.foreshadow ?? 0)} · 信念 {String(vf.beliefs ?? 0)}</b>
                          </div>
                          <div className="s2-kv"><span>记账</span><b>写入 {String(inspect.bookkeeping_written ?? 0)} 条</b></div>
                          {Array.isArray(vf.top_risks) && (vf.top_risks as string[]).slice(0, 3).map((x, i) => (
                            <div className="wp-inspect-risk" key={i}>· {String(x)}</div>
                          ))}
                        </>
                      );
                    })()}
                  </div>
                )}
              </div>
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}
