import chenImg from '../../assets/portrait-chenmo.png';
import liwImg from '../../assets/portrait-liwen.png';

/** 读者阅读台 · 沉浸模式（静态样板，与 index.html 一致） */
export function ReaderView() {
  return (
    <div className="view reader active" id="view-reader">
      <header className="reader-topbar">
        <button className="reader-back" data-back="reader" style={{ background: 'none' }}>
          <svg className="reader-back-icon" viewBox="0 0 16 16" fill="none">
            <path d="M10 3L5 8L10 13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="reader-back-text">返回</span>
        </button>
        <span className="reader-top-title">背叛之夜</span>
        <div className="reader-top-right">
          <span className="reader-chapter">第七章</span>
          <svg className="reader-immersive-icon" viewBox="0 0 18 18" fill="none">
            <path d="M2 5V2h3M13 2h3v3M16 13v3h-3M5 16H2v-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </header>

      <div className="reader-body">
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
          <article className="reader-paper">
            <h1 className="reader-chapter-title">第七章  雨夜的试探</h1>
            <p className="reader-paragraph">窗外的雨越下越密，敲打着窗棂，像某种不安的节拍。陈默坐在书桌后，目光落在那杯早已凉透的茶上，却没有端起来的意思。</p>
            <p className="reader-paragraph">李文站在桌前，手指无意识地绞着衣角。沉默了几秒，她终于开口：</p>
            <div className="reader-dialogue">
              <img className="reader-avatar" src={liwImg} alt="李文" />
              <span className="reader-quote">「默哥，这文件……你什么时候放的？」</span>
            </div>
            <p className="reader-paragraph">陈默抬起眼，没有直接回答，只是缓缓问：</p>
            <div className="reader-dialogue">
              <img className="reader-avatar" src={chenImg} alt="陈默" />
              <span className="reader-quote">「你怎么会问这个？」</span>
            </div>
            <p className="reader-paragraph">李文的手指停住了。她望着陈默，那眼神里有试探，也有某种更深的、几乎要溢出来的东西。</p>
          </article>
          <div className="reader-footer">
            <div className="reader-nav">
              <svg viewBox="0 0 14 14" fill="none">
                <path d="M8.5 3L4.5 7L8.5 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              上一章
            </div>
            <span className="reader-progress">7 / 23</span>
            <div className="reader-nav">
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