import { create } from 'zustand';

export type Theme = 'dark' | 'light';

function readStoredTheme(): Theme {
  return localStorage.getItem('theme') === 'light' ? 'light' : 'dark';
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('theme', theme);
}

interface UIState {
  accountModalOpen: boolean;
  openAccountModal: () => void;
  closeAccountModal: () => void;
  theme: Theme;
  toggleTheme: () => void;
}

export const useUIStore = create<UIState>((set, get) => ({
  accountModalOpen: false,
  openAccountModal: () => set({ accountModalOpen: true }),
  closeAccountModal: () => set({ accountModalOpen: false }),
  theme: readStoredTheme(),
  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    set({ theme: next });
  },
}));
