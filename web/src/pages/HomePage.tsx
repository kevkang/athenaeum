import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import renderMathInElement from 'katex/contrib/auto-render';
import 'katex/dist/katex.min.css';
import '../styles/athmdPygments.css';
import { principlesApi } from '../api/principles';
import styles from './HomePage.module.css';

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default function HomePage() {
  const { data: html, isLoading, isError } = useQuery({
    queryKey: ['public-principles'],
    queryFn: () => principlesApi.get(),
  });
  const proseRef = useRef<HTMLDivElement>(null);

  // The fragment leaves $..$/$$..$$ as literal text on purpose (see
  // archive/athmd_render.py's module docstring) -- KaTeX renders it after
  // the HTML lands in the DOM, same delimiter config as the backend's own
  // standalone-document bootstrap script.
  useEffect(() => {
    if (!html || !proseRef.current) return;
    renderMathInElement(proseRef.current, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
      ],
      throwOnError: false,
    });
  }, [html]);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.brandMark} />
          <span className={styles.brandName}>Athenaeum</span>
        </div>
        <Link to="/login" className={styles.signInBtn}>Sign in</Link>
      </header>

      <div className={styles.intro}>
        <p>
          This page is itself a <code>.athmd</code> file — Athenaeum&rsquo;s own portable manuscript
          format — rendered live. It doubles as Athenaeum&rsquo;s founding principles and as a worked
          template of the format.
        </p>
        <div className={styles.introLinks}>
          <a href={`${API_URL}/public/principles.athmd`} className={styles.introLink}>
            ↓ Download the .athmd template
          </a>
          <a href={`${API_URL}/public/principles.pdf`} className={styles.introLink}>
            ↓ Download as PDF
          </a>
        </div>
      </div>

      {isLoading && <div className={styles.status}>Loading…</div>}
      {isError && <div className={styles.status}>Could not load this document.</div>}

      {html && (
        <div
          ref={proseRef}
          className={styles.prose}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
    </div>
  );
}
