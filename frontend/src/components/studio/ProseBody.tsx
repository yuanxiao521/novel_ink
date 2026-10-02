import { useMemo, useState } from 'react';
import type { ProseAnnotation } from '../../api/novel';

/* ==========================================================================
   ProseBody —— 正文阅读/编辑 + 段落序号（¶n）+ 选中批注

   设计依据：正文协作副驾架构 §8.1（批注=可追踪改稿任务）。
   段落序号是给 agent 的**定位锚**（"第 3 段不像他说的"），0 新增字段，按空行切段即可。
   ========================================================================== */

interface Props {
  text: string;
  annotations: ProseAnnotation[];
  editing: boolean;
  onChange: (t: string) => void;
  onAnnotate: (paraIndex: number, quote: string, note: string) => void;
  onAskEditor: (message: string) => void;
  onResolve: (id: string, status: 'handled' | 'dismissed') => void;
  onDeleteAnnotation: (id: string) => void;
}

export function splitParagraphs(text: string): string[] {
  return (text || '')
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

export function ProseBody({ text, annotations, editing, onChange, onAnnotate, onAskEditor, onResolve, onDeleteAnnotation }: Props) {
  const paras = useMemo(() => splitParagraphs(text), [text]);
  const [sel, setSel] = useState<{ paraIndex: number; quote: string } | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [note, setNote] = useState('');

  const byPara = useMemo(() => {
    const m = new Map<number, ProseAnnotation[]>();
    for (const a of annotations) {
      const k = a.para_index || 0;
      m.set(k, [...(m.get(k) ?? []), a]);
    }
    return m;
  }, [annotations]);

  const orphan = byPara.get(0) ?? [];

  const pick = (paraIndex: number) => {
    const s = window.getSelection()?.toString().trim() ?? '';
    if (!s) {
      setSel({ paraIndex, quote: '' });
      return;
    }
    setSel({ paraIndex, quote: s });
  };

  const commit = () => {
    if (!sel || !note.trim()) return;
    onAnnotate(sel.paraIndex, sel.quote, note.trim());
    setNote('');
    setDrafting(false);
    setSel(null);
    window.getSelection()?.removeAllRanges();
  };

  const annCard = (a: ProseAnnotation) => (
    <div className={`pb-ann ${a.status}`} key={a.id}>
      <div className="pb-ann-head">
        <span className="pb-ann-no">{a.para_index ? `¶${a.para_index}` : '未定位'}</span>
        <span className="pb-ann-status">{a.status === 'open' ? '待处理' : a.status === 'handled' ? '已处理' : '已撤销'}</span>
        <span className="pb-ann-spacer" />
        {a.status === 'open' ? (
          <>
            <button onClick={() => onResolve(a.id, 'handled')}>标记已处理</button>
            <button onClick={() => onResolve(a.id, 'dismissed')}>撤销</button>
          </>
        ) : (
          <button onClick={() => onDeleteAnnotation(a.id)}>删除</button>
        )}
      </div>
      {a.quote && <div className="pb-ann-quote">「{a.quote}」</div>}
      <div className="pb-ann-note">{a.note}</div>
    </div>
  );

  if (editing) {
    return (
      <div className="pb-edit">
        <textarea
          className="pb-textarea"
          value={text}
          placeholder="正文会出现在这里：写手生成 / 作者手写 / 应用润色稿……"
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }

  return (
    <div className="pb-body">
      {paras.length === 0 && (
        <div className="pb-empty">还没有正文 —— 点「✍ 写手·初稿」生成，或切到「编辑」直接写。</div>
      )}

      {paras.map((p, i) => {
        const no = i + 1;
        const anns = byPara.get(no) ?? [];
        return (
          <div className="pb-block" key={no}>
            <div className={`pb-para ${sel?.paraIndex === no ? 'active' : ''}`} onMouseUp={() => pick(no)}>
              <span className="pb-no" title={`第 ${no} 段（批注与 agent 定位用）`}>¶{no}</span>
              <span className="pb-text">{p}</span>
            </div>
            {anns.length > 0 && <div className="pb-anns">{anns.map(annCard)}</div>}
          </div>
        );
      })}

      {orphan.length > 0 && (
        <div className="pb-anns" style={{ marginTop: 12 }}>
          <div className="pb-anns-title">未定位批注（引用原文已不在正文里，需人工确认）</div>
          {orphan.map(annCard)}
        </div>
      )}

      {sel && (
        <div className="pb-bar">
          {!drafting ? (
            <>
              <span className="pb-bar-target">
                ¶{sel.paraIndex}
                {sel.quote ? ` · 「${sel.quote.slice(0, 24)}${sel.quote.length > 24 ? '…' : ''}」` : ' · 整段'}
              </span>
              <button className="ws-btn ws-btn-primary" onClick={() => setDrafting(true)}>批注</button>
              <button
                className="ws-btn"
                onClick={() => {
                  const q = sel.quote || `第 ${sel.paraIndex} 段`;
                  onAskEditor(`关于 ${q}：`);
                  setSel(null);
                }}
              >
                问责编
              </button>
              <button
                className="ws-btn"
                onClick={() => {
                  const q = sel.quote || `第 ${sel.paraIndex} 段`;
                  onAskEditor(`第 ${sel.paraIndex} 段重写一下（${q}），保留事实只改写法。`);
                  setSel(null);
                }}
              >
                让写手重写这段
              </button>
              <button className="ws-btn" onClick={() => setSel(null)}>取消</button>
            </>
          ) : (
            <>
              <input
                className="pb-bar-input"
                autoFocus
                placeholder="写下你的意见（会作为批注落库，责编能读到）"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
              />
              <button className="ws-btn ws-btn-primary" onClick={commit} disabled={!note.trim()}>保存批注</button>
              <button className="ws-btn" onClick={() => { setDrafting(false); setNote(''); }}>取消</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
