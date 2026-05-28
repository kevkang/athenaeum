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

const TOKEN_KEY = 'ath_token';
const USER_KEY = 'ath_user';

function loadPersisted(): { user: AuthUser | null; token: string | null } {
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    const user = JSON.parse(localStorage.getItem(USER_KEY) ?? 'null') as AuthUser | null;
    return { token, user };
  } catch {
    return { token: null, user: null };
  }
}

const { token: _savedToken, user: _savedUser } = loadPersisted();
if (_savedToken) setToken(_savedToken);

export const useAuthStore = create<AuthState>(() => ({
  user: _savedUser,
  isAuthenticated: !!(_savedToken && _savedUser),

  login(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    setToken(token);
    useAuthStore.setState({ user, isAuthenticated: true });
  },

  logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    useAuthStore.setState({ user: null, isAuthenticated: false });
  },
}));
