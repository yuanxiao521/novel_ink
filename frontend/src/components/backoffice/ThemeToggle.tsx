import { useTheme } from '../../theme/ThemeContext';

/** 概览/人物页顶部右侧的「纸墨」主题切换钮 */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button className="theme-toggle" title="切换纸墨主题" onClick={toggleTheme}>
      <span className="theme-icon">☯</span>
      <span className="theme-label">{theme === 'paper' ? '纸' : '墨'}</span>
    </button>
  );
}