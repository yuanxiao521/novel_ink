import { useNavigate } from 'react-router-dom';
import { useWorkspace, type StepKey } from '../../context/WorkspaceContext';
import { ThemeToggle } from '../backoffice/ThemeToggle';

/* ==========================================================================
   ContextBar —— 全局上下文条（书 / 章 / 场景 + 前置条件 + 上一步 / 下一步）
   取代各页自建的"书切换"，让所有工作站点共享同一落点。
   ========================================================================== */

export function ContextBar({ step }: { step: StepKey }) {
  const ws = useWorkspace();
  const nav = useNavigate();

  const prev = ws.stepLink(step, 'prev');
  const next = ws.stepLink(step, 'next');
  const blocker = ws.blocker(step);

  return (
    <div className="ws-ctxbar">
      <div className="ws-crumbs">
        <button className="ws-crumb-link" onClick={() => nav('/')} title="回到书架">
          书架
        </button>
        <span className="ws-sep">/</span>
        <select className="ws-sel" value={ws.bookId} onChange={(e) => ws.setBook(e.target.value)} title="切换书">
          {ws.books.length === 0 && <option value="">（暂无书）</option>}
          {ws.books.map((b) => (
            <option key={b.id} value={b.id}>
              {b.title}
            </option>
          ))}
        </select>

        {ws.chapters.length > 0 && (
          <>
            <span className="ws-sep">/</span>
            <select className="ws-sel" value={ws.chapterId} onChange={(e) => ws.setChapter(e.target.value)} title="切换章">
              {ws.chapters.map((c, i) => (
                <option key={c.id} value={c.id}>
                  {/^\s*第\s*[0-9一二三四五六七八九十]+\s*[章回]/.test(c.title) ? c.title : `第 ${i + 1} 章 ${c.title}`}
                </option>
              ))}
            </select>
          </>
        )}

        {ws.scenes.length > 0 && (
          <>
            <span className="ws-sep">/</span>
            <select className="ws-sel" value={ws.sceneId} onChange={(e) => ws.setScene(e.target.value)} title="切换场景">
              {ws.scenes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </>
        )}
      </div>

      {blocker ? (
        <button className="ws-blocker" onClick={() => nav(blocker.to)} title="点击去修复">
          ○ {blocker.text}
        </button>
      ) : (
        <span className="ws-spacer" />
      )}

      <div className="ws-right">
        <ThemeToggle />
        {prev && (
          <button className="ws-step" onClick={() => nav(prev.to)}>
            ◀ {prev.label}
          </button>
        )}
        {next && (
          <button className="ws-step ws-step-next" onClick={() => nav(next.to)}>
            {next.label} ▶
          </button>
        )}
      </div>
    </div>
  );
}
