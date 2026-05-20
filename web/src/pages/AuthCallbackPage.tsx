import { useEffect } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useAuthStore } from '../store/auth';

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    const orcid = params.get('orcid');
    const name = params.get('name');
    const userId = params.get('user_id');

    if (token && orcid && userId) {
      login(token, { id: userId, orcid, name: name ?? orcid });
      // Clear credentials from URL before navigating
      window.history.replaceState({}, '', '/auth/callback');
      navigate({ to: '/projects' });
    } else {
      navigate({ to: '/login' });
    }
  }, [login, navigate]);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <p className="ui" style={{ color: 'var(--text-muted)', fontSize: 14 }}>
        Completing sign in…
      </p>
    </div>
  );
}
