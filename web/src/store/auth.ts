import { create } from 'zustand';
import { setToken } from '../api/client';

interface AuthUser {
  id: string;
  orcid: string;
  name: string;
}

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (token: string, user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,

  login(token, user) {
    setToken(token);
    set({ user, isAuthenticated: true });
  },

  logout() {
    setToken(null);
    set({ user: null, isAuthenticated: false });
  },
}));
