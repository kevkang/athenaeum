import { execSync } from 'node:child_process';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

function resolveGitSha(): string {
  try {
    return execSync('git rev-parse --short HEAD').toString().trim();
  } catch {
    return 'unknown';
  }
}

// Exposed as import.meta.env.VITE_GIT_SHA for a small "which build is this"
// marker in the UI — there's no release/version-tagging process yet, so the
// commit itself is the only meaningful identifier. Set here (not via a CI
// workflow env var) so local dev, CI, and prod all get one automatically
// without every build invocation needing to remember to pass it.
process.env.VITE_GIT_SHA ??= resolveGitSha();

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiUrl = env.VITE_API_URL ?? 'http://localhost:8000';

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: apiUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
