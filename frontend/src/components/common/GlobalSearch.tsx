import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { searchEverything } from '../../api/novel';
import type { SearchHit } from '../../api/novel';

/** 命中类型 → 徽标文案（与后端 search_all 的 kind 对齐）。 */
const KIND_LABEL: Record<string, string> = {
  book: '书', chapter: '章节', scene: '场景', character: '角色', foreshadow: '伏笔',
};

/**
 * 跨书搜索（v1.34）。
 *
 * 缘起：概览复盘页头部一直挂着「跨书搜索角色、伏笔、章节…」输入框，但既没有 handler、
 * 后端也没有接口 —— 敲进去什么都没发生（用户实测反馈「按钮读不到内容」）。
 * 现在接 `GET /api/v1/search`：命中项带好后端算好的 path，点一下直接跳过去。
 */
export function GlobalSearch({ placeholder = '跨书搜索角色、伏笔、章节…' }: { placeholder?: string }) {
  const nav = useNavigate();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const boxRef = useRef<HTMLDivElement | null>(null);

  // 输入防抖 250ms；空串直接清空（不打后端）
  useEffect(() => {
    const text = q.trim();
    if (!text) { setHits([]); setErr(''); setBusy(false); return; }
    const t = setTimeout(() => {
      setBusy(true);
      searchEverything(text)
        .then((res) => { setHits(res.hits); setErr(''); setOpen(true); })
        .catch((e) => { setHits([]); setErr('搜索失败：' + (e instanceof Error ? e.message : e)); setOpen(true); })
        .finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  // 点面板外面收起
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const go = (h: SearchHit) => { setOpen(false); setQ(''); nav(h.path); };

  return (
    <div className="header-search" ref={boxRef}>
      <span className="search-icon">⌕</span>
      <input
        type="text"
        placeholder={placeholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => { if (hits.length > 0 || err) setOpen(true); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && hits[0]) go(hits[0]);
          if (e.key === 'Escape') setOpen(false);
        }}
      />
      {open && (
        <div className="search-drop">
          {err && <div className="search-empty">{err}</div>}
          {!err && busy && hits.length === 0 && <div className="search-empty">搜索中…</div>}
          {!err && !busy && hits.length === 0 && <div className="search-empty">没有匹配的章节 / 场景 / 角色 / 伏笔</div>}
          {hits.map((h) => (
            <button key={h.kind + '-' + h.id} className="search-item" onClick={() => go(h)}>
              <span className={'search-kind ' + h.kind}>{KIND_LABEL[h.kind] ?? h.kind}</span>
              <span className="search-label">{h.label}</span>
              <span className="search-sub">{h.subtitle}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
