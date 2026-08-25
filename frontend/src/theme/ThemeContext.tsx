import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

export type Theme = 'ink' | 'paper';
export type Mood = 'suspense' | 'tension' | 'warmth' | 'serene';

const MOOD_ORDER: Mood[] = ['suspense', 'tension', 'warmth', 'serene'];
const MOOD_LABEL: Record<Mood, string> = {
  suspense: '悬疑',
  tension: '紧张',
  warmth: '温情',
  serene: '宁静',
};

interface ThemeCtx {
  theme: Theme;
  mood: Mood;
  moodLabel: string;
  toggleTheme: () => void;
  cycleMood: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

/** 主题/mood 落在 <html data-theme data-mood>，CSS 变量据此换肤，与 webapp 一致 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const el = document.documentElement.dataset.theme;
    return el === 'paper' ? 'paper' : 'ink';
  });
  const [mood, setMood] = useState<Mood>(() => {
    const el = document.documentElement.dataset.mood as Mood;
    return MOOD_ORDER.includes(el) ? el : 'suspense';
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    document.documentElement.dataset.mood = mood;
  }, [mood]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === 'paper' ? 'ink' : 'paper'));
  }, []);
  const cycleMood = useCallback(() => {
    setMood((m) => MOOD_ORDER[(MOOD_ORDER.indexOf(m) + 1) % MOOD_ORDER.length]);
  }, []);

  return (
    <Ctx.Provider
      value={{ theme, mood, moodLabel: MOOD_LABEL[mood], toggleTheme, cycleMood }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useTheme 必须在 ThemeProvider 内使用');
  return ctx;
}