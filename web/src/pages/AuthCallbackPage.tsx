/**
 * Shown for at most an instant while the router finishes signing in.
 *
 * All the work happens in the /auth/callback route's beforeLoad (router.tsx),
 * which always redirects — to /projects with a token, to /login without one.
 * This component used to do that work in a useEffect, which broke under React
 * StrictMode's double-run in development; see the comment on that route.
 */
export default function AuthCallbackPage() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <p className="ui" style={{ color: 'var(--text-muted)', fontSize: 14 }}>
        Completing sign in…
      </p>
    </div>
  );
}
