import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';

// 全局样式：tokens（设计变量）→ app（共用）→ dashboard/characters（页面级）。
// 顺序与 webapp 各页 <link> 一致，保证 CSS 变量与页面样式 1:1。
import './styles/tokens.css';
import './styles/app.css';
import './styles/dashboard.css';
import './styles/characters.css';
import './styles/maestro.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);