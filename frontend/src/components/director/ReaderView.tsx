import { useEffect, useMemo, useState } from 'react';
import chenImg from '../../assets/portrait-chenmo.png';
import liwImg from '../../assets/portrait-liwen.png';
import { exportBookUrl, fetchChapterProse } from '../../api/novel';
import type { ChapterProse } from '../../api/novel';

export interface ReaderViewProps {
  /** 书名（顶栏标题） */
  bookTitle: string;
  /** 书 id（存在时显示「导出全书」） */
  bookId?: string;
  /** 当前章 id（缺省时回退静态样板） */
  chapterId?: string;
  /** 章目录（翻章导航），[{id, title, order_no}] */
  chapters: Array<{ id: string; title: string; order_no: number }>;
}

/** 读者阅读台 · 沉浸模式：按章渲染已定稿正文（无书上下文时回退静态样板） */
export function ReaderView({ bookTitle, bookId, chapterId, chapters }: ReaderViewProps) {
  const [prose, setProse] = useState<ChapterProse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 阅读台内部翻章状态（初始 = 进入时的当前章）
  const [readChapterId, setReadChapterId] = useState<string | undefined>(chapterId);

  useEffect(() => {
    setReadChapterId(chapterId);
  }, [chapterId]);

  useEffect(() => {
    if (!readChapterId) return;
    let cancel = false;
    setLoading(true);
    setErr(null);
    fetchChapterProse(readChapterId)
      .then((p) => {
        if (!cancel) setProse(p);
      })
      .catch((e) => {
        console.warn('[阅读台] 章正文加载失败：', e);
        if (!cancel) setErr('本章正文加载失败');
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [readChapterId]);

  // 翻章：按 order_no 找相邻章
  const sorted = useMemo(
    () => [...chapters].sort((a, b) => (a.order_no || 0) - (b.order_no || 0)),
    [chapters],
  );
  const idx = sorted.findIndex((c) => c.id === readChapterId);
  const prev = idx > 0 ? sorted[idx - 1] : undefined;
  const next = idx >= 0 && idx < sorted.length - 1 ? sorted[idx + 1] : undefined;

  // 无章上下文（默认演示场景）→ 静态样板兜底
  if (!readChapterId) {
    return (
      <div className="view reader active" id="view-reader">
        <header className="reader-topbar">
          <span className="reader-top-title">{bookTitle}</span>
          <div className="reader-top-right">
            <span className="reader-chapter">演示场景 · 未挂书稿</span>
          </div>
        </header>
        <div className="reader-body">
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
            <article className="reader-paper">
              <h1 className="reader-chapter-title">书房夜谈 · 雨</h1>
              <p className="reader-paragraph">
                窗外的雨越下越密，敲打着窗棂，像某种不安的节拍。陈默坐在书桌后，目光落在那杯早已凉透的茶上，却没有端起来的意思。
              </p>
              <p className="reader-paragraph">李文站在桌前，手指无意识地绞着衣角。沉默了几秒，她终于开口：</p>
              <div className="reader-dialogue">
                <img className="reader-avatar" src={liwImg} alt="李文" />
                <span className="reader-quote">「默哥，这文件……你什么时候放的？」</span>
              </div>
              <p className="reader-paragraph">陈默抬起眼，没有直接回答，只是缓缓问——</p>
              <div className="reader-dialogue">
                <img className="reader-avatar" src={chenImg} alt="陈默" />
                <span className="reader-quote">「你怎么会问这个？」</span>
              </div>
              <p className="reader-paragraph">
                李文的手指停住了。她望着陈默，那眼神里有试探，也有某种更深的、几乎要溢出来的东西。雨声忽然变得很响，像要把什么东西盖住。
              </p>
            </article>
          </div>
        </div>
      </div>
    );
  }

  // 正文段落：聚合 prose 按 \n\n 切段
  const paragraphs = (prose?.prose ?? '').split('\n\n').filter((p) => p.trim());

  return (
    <div className="view reader active" id="view-reader">
      <header className="reader-topbar">
        <span className="reader-top-title">{bookTitle}</span>
        <div className="reader-top-right">
          <span className="reader-chapter">{prose?.title ?? (loading ? '加载中…' : '')}</span>
          {bookId && (
            <a
              className="reader-export"
              href={exportBookUrl(bookId)}
              title="导出全书 markdown（仅含已定稿场景）"
            >
              导出全书
            </a>
          )}
        </div>
      </header>

      <div className="reader-body">
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
          <article className="reader-paper">
            <h1 className="reader-chapter-title">{prose?.title ?? '…'}</h1>
            {loading && <p className="reader-paragraph">正在取书稿…</p>}
            {err && <p className="reader-paragraph">{err}</p>}
            {!loading && !err && paragraphs.length === 0 && (
              <p className="reader-paragraph">本章尚未定稿——回到工作台，收束场景后点「定稿入库」。</p>
            )}
            {paragraphs.map((p, i) => (
              <p className="reader-paragraph" key={`rp-${i}`}>
                {p}
              </p>
            ))}
          </article>
          <div className="reader-footer">
            <div
              className={`reader-nav${prev ? '' : ' disabled'}`}
              onClick={() => prev && setReadChapterId(prev.id)}
            >
              <svg viewBox="0 0 14 14" fill="none">
                <path d="M8.5 3L4.5 7L8.5 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              上一章
            </div>
            <span className="reader-progress">
              {idx + 1} / {sorted.length}
              {prose && prose.word_count > 0 ? ` · ${prose.word_count} 字` : ''}
            </span>
            <div
              className={`reader-nav${next ? '' : ' disabled'}`}
              onClick={() => next && setReadChapterId(next.id)}
            >
              下一章
              <svg viewBox="0 0 14 14" fill="none">
                <path d="M5.5 3L9.5 7L5.5 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>
        </div>
      </div>

      <div className="reader-progress-bar">
        <div className="reader-progress-fill"></div>
      </div>
    </div>
  );
}
