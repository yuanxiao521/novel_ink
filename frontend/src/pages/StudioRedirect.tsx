import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchSceneDetail } from '../api/novel';
import { useWorkspace } from '../context/WorkspaceContext';

/**
 * v1.65：场级页面（/studio）并入「章级写作台」后，这里只做**重定向**，不留空路由。
 *
 * 为什么合并：同一个"写正文"被拆成两个页面，作者要记两个入口、两套工具、两份上下文；
 * 而场其实是章的细分（scenes.final_prose 只是可并入的来源快照，章正文才是唯一真源）。
 * 现在 /studio/:sceneId → /write/{章}?scene={场}，章页左栏会把该场高亮出来。
 */
export function StudioRedirect() {
  const { sceneId = '' } = useParams<{ sceneId?: string }>();
  const { sceneId: wsSceneId, chapterId } = useWorkspace();
  const nav = useNavigate();
  const [err, setErr] = useState('');

  useEffect(() => {
    const sid = sceneId || wsSceneId;
    if (!sid) {
      // 没有场景上下文：直接进当前章的写作台（章是主线单位，不依赖拆场）
      nav(chapterId ? '/write/' + chapterId : '/write', { replace: true });
      return;
    }
    let cancel = false;
    fetchSceneDetail(sid)
      .then((d) => {
        if (cancel) return;
        const cid = String((d as { chapter_id?: string }).chapter_id || '');
        nav(cid ? '/write/' + cid + '?scene=' + sid : '/write', { replace: true });
      })
      .catch((e) => {
        if (!cancel) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => { cancel = true; };
  }, [sceneId, wsSceneId, chapterId, nav]);

  return (
    <div className="app-shell">
      <div className="main-col">
        <div className="s2-page">
          <div className="ws-empty" style={{ flex: 1 }}>
            <div className="ws-empty-title">{err ? '场景读取失败' : '正在打开章级写作台…'}</div>
            <div className="ws-empty-desc">
              {err
                ? err
                : '场级页面已并入「章级写作台」：整章正文、责编、精修工具、前情与记忆都在那一页；场只是可并入的细分段。'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
