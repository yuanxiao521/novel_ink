import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from './theme/ThemeContext';
import { DialogProvider } from './components/common/Dialog';
import { HomePage } from './pages/HomePage';
import { DirectorPage } from './pages/DirectorPage';
import { DashboardPage } from './pages/DashboardPage';
import { CharactersPage } from './pages/CharactersPage';
import { MaestroPage } from './pages/MaestroPage';
import { StudioPage } from './pages/StudioPage';
import { DegradedBanner } from './components/common/DegradedBanner';

export function App() {
  return (
    <ThemeProvider>
      <DialogProvider>
        <BrowserRouter>
          <DegradedBanner />
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/director" element={<DirectorPage />} />
            <Route path="/director/:sceneId" element={<DirectorPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/maestro" element={<MaestroPage />} />
            <Route path="/studio/:sceneId" element={<StudioPage />} />
            <Route path="/characters" element={<CharactersPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </DialogProvider>
    </ThemeProvider>
  );
}
