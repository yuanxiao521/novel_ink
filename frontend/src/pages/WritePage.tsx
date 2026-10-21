import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Sidebar } from '../components/backoffice/Sidebar';
import { ContextBar } from '../components/common/ContextBar';
import { useDialog } from '../components/common/Dialog';
import { useWorkspace } from '../context/WorkspaceContext';
import {
  chapterDraftStream, fetchChapterProse, listScenes, saveChapterProse,
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

interface SceneRow { id: string; title?: string; goal?: string; final_prose?: string }

export function WritePage() {
  const { chapterId = '' } = useParams<{ chapterId: string }>();
  const nav = useNavigate();
  const { bookId } = useWorkspace();
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

  const save = async () => {
    if (!chapterId || busy) return;
    setBusy(true);
    try {
      const r = await saveChapterProse(chapterId, text);
      setSavedText(text);
      flash(`已保存：${r.words} 字`);
    } catch (e) {
      flash(`保存失败：${String(e instanceof Error ? e.message : e)}`);
    } finally { setBusy(false); }
  };

  return (
    <div className="app-shell">
      <Sidebar active="studio" />
      <div className="main-col">
        <ContextBar step="studio" />
        <div className="s2-page">
          {toast && <div className="s2-toast">{toast}</div>}

          <header className="s2-head">
            <span className="s2-title">{title || '未命名章'}</span>
            <span className="s2-sub">
              {words} 字{wordTarget > 0 ? ` / 目标 ${wordTarget} 字（${progress}%）` : ''} · {paras.length} 段
              {dirty && <b className="s2-dirty"> · 未保存</b>}
            </span>
            <span className="s2-src">基调：<b>{tone || '未定'}</b></span>
          </header>

          <div className="s2-actions">
            <button className="ws-btn ws-btn-primary" disabled={busy} onClick={() => void writeChapter()}
                    title="按本章字数目标分段写满（不是写一段），段间自动承接">
              {busy ? (streamChars > 0 ? `写这一章… ${Math.round(streamChars)} 字` : '写这一章…（构思中）') : '✍ 写这一章'}
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
              <div className="s2-kv"><span>字数目标</span><b>{wordTarget > 0 ? `${wordTarget} 字` : '未设（默认 4000）'}</b></div>
              <div className="s2-kv"><span>基调</span><b>{tone || '未定'}</b></div>
              <div className="s2-kv"><span>要完成的事</span><b>{summary || '未填'}</b></div>
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
                <div className="s2-kv"><span>目标</span><b>{wordTarget > 0 ? `${wordTarget} 字` : '未设'}</b></div>
                <div className="s2-kv"><span>段落</span><b>{paras.length}</b></div>
                <div className="s2-hint" style={{ marginTop: 12 }}>
                  写作顺序建议：先让开篇立住 → 中间推进二到三次 → 收在一个钩子上。
                  「写这一章」就是按这个节奏分段的。
                </div>
                {bookId && <div className="s2-hint" style={{ marginTop: 8 }}>书：{bookId}</div>}
              </div>
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}
