import styles from './LoginPage.module.css';

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default function LoginPage() {
  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <div className={styles.brandMark} />
          <span className={styles.brandName}>Athenaeum</span>
        </div>

        <p className={styles.tagline}>
          A traceability tool for working scientists.
          <br />
          Every claim. Every source. Every step.
        </p>

        {/* Tells the backend which frontend to send the user back to after
            ORCID auth completes — without this, every login (including on
            a PR preview) redirects back to the single, fixed production
            frontend URL instead of wherever the user actually started
            from. Validated server-side against an allowlist, so this is
            just a hint, not something this origin can abuse. */}
        <a
          href={`${API_URL}/auth/orcid/login?origin=${encodeURIComponent(window.location.origin)}`}
          className={styles.loginBtn}
        >
          <span className={styles.orcidLogo}>iD</span>
          Log in with ORCID
        </a>

        <p className={styles.note}>
          Athenaeum uses ORCID to identify researchers.
          <br />
          No password required.
        </p>
      </div>
    </div>
  );
}
