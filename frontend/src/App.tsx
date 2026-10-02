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
import { SettingsPage } from './pages/SettingsPage';
import { DegradedBanner } from './components/common/DegradedBanner';
import { EmptyState } from './components/common/EmptyState';
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

  return (
    <div className="ws-entry">
      <EmptyState
        icon="▣"
        title="还没有场景可以进入"
        desc="去主笔创作：建书 → 加章 → 加场景；之后导演台与正文协作都能一键直达。"
        primary={{ label: '去主笔创作', onClick: () => nav('/maestro') }}
        secondary={{ label: '回书架', onClick: () => nav('/') }}
      />
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
              <Route path="/studio" element={<SceneEntry kind="studio" />} />
              <Route path="/studio/:sceneId" element={<StudioPage />} />
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
