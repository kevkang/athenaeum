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

        <a href={`${API_URL}/auth/orcid/login`} className={styles.loginBtn}>
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
