import * as esbuild from 'esbuild';
import { cpSync, mkdirSync, existsSync, rmSync } from 'node:fs';

const watch = process.argv.includes('--watch');

if (existsSync('dist')) rmSync('dist', { recursive: true });
mkdirSync('dist', { recursive: true });

const buildOptions = {
  entryPoints: {
    'background/index': 'src/background/index.ts',
    'content/relay': 'src/content/relay.ts',
  },
  bundle: true,
  outdir: 'dist/src',
  format: 'esm',
  target: 'chrome120',
  sourcemap: true,
  logLevel: 'info',
};

cpSync('manifest.json', 'dist/manifest.json');
if (existsSync('icons')) cpSync('icons', 'dist/icons', { recursive: true });

if (watch) {
  const ctx = await esbuild.context(buildOptions);
  await ctx.watch();
  console.log('watching for changes...');
} else {
  await esbuild.build(buildOptions);
}
