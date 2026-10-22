import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { ThemeProvider } from './theme/ThemeContext';
import { DialogProvider } from './components/common/Dialog';
import { HomePage } from './pages/HomePage';
import { DirectorPage } from './pages/DirectorPage';
import { DashboardPage } from './pages/DashboardPage';
import { CharactersPage } from './pages/CharactersPage';
import { MaestroPage } from './pages/MaestroPage';
import { StudioPage } from './pages/StudioPage';
import { StudioRedirect } from './pages/StudioRedirect';
import { WritePage } from './pages/WritePage';
import { SettingsPage } from './pages/SettingsPage';
import { ProofreadPage } from './pages/ProofreadPage';
import { DegradedBanner } from './components/common/DegradedBanner';
import { EmptyState } from './components/common/EmptyState';
import { ContextBar } from './components/common/ContextBar';
import { WorkspaceProvider, useWorkspace } from './context/WorkspaceContext';

/* ==========================================================================
   /director、/studio 无场景时的兜底：
   上下文里已有场景 → 直接补到该场景（实现"一键直达"）；
   一本书都没有场景 → 明确引导，不留白页。
   ========================================================================== */
function SceneEntry({ kind }: { kind: 'director' | 'studio' }) {
  const ws = useWorkspace();
  const nav = useNavigate();

  useEffect(() => {
    if (ws.sceneId) nav(`/${kind}/${ws.sceneId}`, { replace: true });
  }, [ws.sceneId, kind, nav]);

  // 兜底页保留上下文条：否则「切到没有场景的章」会掉进一个连书/章都换不了的死页（进得去出不来）
  const curChapter = ws.chapters.find((c) => c.id === ws.chapterId);
  const anyScene = ws.chapters.some((c) => c.scenes.length > 0);
  const emptyChapter = !!curChapter && curChapter.scenes.length === 0;
  const title = emptyChapter ? '本章还没有场景' : '还没有场景可以进入';
  const desc = ws.chapters.length === 0
    ? '这本书还没有章节与场景。去主笔创作：建书 → 加章 → 加场景。'
    : emptyChapter
      ? anyScene
        ? '在上下文条把「章」切到有场景的一章，或去主笔给本章加场景。'
        : '这本书还没有场景，去主笔给章节添加场景。'
      : '从上下文条的「章 / 场景」下拉选一幕，即可开始或继续推演。'
  ;
  return (
    <div className="app-shell">
      <div className="view workbench active">
        <ContextBar step={kind} />
        <div className="ws-entry ws-entry-body">
          <EmptyState
            icon="▣"
            title={title}
            desc={desc}
            primary={{ label: '去主笔创作', onClick: () => nav('/maestro') }}
            secondary={{ label: '回书架', onClick: () => nav('/') }}
          />
        </div>
      </div>
    </div>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <DialogProvider>
        <BrowserRouter>
          <WorkspaceProvider>
            <DegradedBanner />
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/director" element={<SceneEntry kind="director" />} />
              <Route path="/director/:sceneId" element={<DirectorPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/maestro" element={<MaestroPage />} />
              {/* v1.65：章级工作台是主入口；场级页降级为「二级精修页」（批注/选中问责编在这里）。
                  /studio 无 id 时不再给空态页 —— 直接回章级工作台。 */}
              <Route path="/studio" element={<StudioRedirect />} />
              <Route path="/studio/:sceneId" element={<StudioPage />} />
              {/* v1.46 直写链路：章级写作主线（章是最小可写单位，场是可选细分） */}
              {/* v1.64：/write 无 id 也能进（落到当前章/第一章）——以前只有 /write/:chapterId，
                  从侧栏点「正文协作」若目标是 /write 会被 * 兜底跳回首页（实测落点 "/?book=…"） */}
              <Route path="/write" element={<WritePage />} />
              <Route path="/write/:chapterId" element={<WritePage />} />
              <Route path="/proofread" element={<ProofreadPage />} />
        <Route path="/characters" element={<CharactersPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </WorkspaceProvider>
        </BrowserRouter>
      </DialogProvider>
    </ThemeProvider>
  );
}
