import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from './theme/ThemeContext';
import { HomePage } from './pages/HomePage';
import { DirectorPage } from './pages/DirectorPage';
import { DashboardPage } from './pages/DashboardPage';
import { CharactersPage } from './pages/CharactersPage';
import { MaestroPage } from './pages/MaestroPage';
import { PlanningPage } from './pages/PlanningPage';

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/director" element={<DirectorPage />} />
          <Route path="/director/:sceneId" element={<DirectorPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/maestro" element={<MaestroPage />} />
          <Route path="/characters" element={<CharactersPage />} />
          <Route path="/planning" element={<PlanningPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
