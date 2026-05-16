import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'path'
import { execSync } from 'child_process'

// Inject build metadata at compile time
// git describe: on tagged commit → "v1.0.0", ahead of tag → "v1.0.0-3-gabc1234"
const gitDescribe = (() => {
  try { return execSync('git describe --tags --always').toString().trim(); } catch { return 'dev'; }
})();
const gitSha = process.env.VITE_GIT_SHA || (() => {
  try { return execSync('git rev-parse --short HEAD').toString().trim(); } catch { return 'dev'; }
})();
const buildEnv = process.env.VITE_BUILD_ENV || 'local';
// Commit time in PST for staging display
const commitTime = (() => {
  try {
    return execSync('git log -1 --format=%cd --date=format:"%b %d, %I:%M %p PST"', {
      env: { ...process.env, TZ: 'America/Los_Angeles' },
    }).toString().trim();
  } catch { return ''; }
})();

// Parse git describe to extract version info
// Tagged exactly: "v1.0.0" → version="v1.0.0", commitsAhead=0
// Ahead of tag: "v1.0.0-3-gabc1234" → version="v1.0.0", commitsAhead=3
const descParts = gitDescribe.match(/^(v[\d.]+)(?:-(\d+)-g[a-f0-9]+)?$/);
const tagVersion = descParts?.[1] || null;
const commitsAhead = descParts?.[2] ? parseInt(descParts[2]) : 0;

// Production: show tag version (e.g., "v1.0.0")
// Staging: show "abc1234 · 3 ahead of v1.0.0"
// Local: show "dev · abc1234"
const appVersion = buildEnv === 'production' && tagVersion
  ? tagVersion
  : buildEnv === 'staging' && tagVersion
    ? commitsAhead > 0
      ? `${gitSha} · ${commitsAhead} ahead of ${tagVersion}${commitTime ? ` · ${commitTime}` : ''}`
      : `${gitSha} · ${tagVersion}${commitTime ? ` · ${commitTime}` : ''}`
    : `dev · ${gitSha}${commitTime ? ` · ${commitTime}` : ''}`;

export default defineConfig({
  // Load env files from the project root (one level up) so the same .env
  // feeds both the Python backend and the Vite build. VITE_* vars are
  // exposed to the browser bundle and substituted into index.html.
  envDir: '../',
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'public',
      filename: 'sw.js',
      injectRegister: null,
      manifest: false,
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2,wasm,mjs}'],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
    }),
    {
      name: 'spa-fallback',
      configureServer(server) {
        // Pre-middleware: redirect /macro_app (no slash) -> /macro_app/
        server.middlewares.use((req, res, next) => {
          if (req.url === '/macro_app') {
            res.writeHead(302, { Location: '/macro_app/' })
            res.end()
            return
          }
          next()
        })

        // Post-middleware (returned function): fallback deep routes to index.html
        return () => {
          server.middlewares.use((req, _res, next) => {
            const url = req.url || ''
            if (
              url.startsWith('/macro_app/') &&
              !url.startsWith('/macro_app/api') &&
              !url.startsWith('/macro_app/assets') &&
              !url.startsWith('/macro_app/@') &&
              !url.startsWith('/macro_app/node_modules') &&
              !url.startsWith('/macro_app/src') &&
              !url.includes('.')
            ) {
              req.url = '/macro_app/'
            }
            next()
          })
        }
      },
    },
  ],
  resolve: {
    alias: {
      // @undecaf/barcode-detector-polyfill imports zbar-wasm from a CDN URL.
      // Redirect to the local package so it's bundled and works offline.
      'https://cdn.jsdelivr.net/npm/@undecaf/zbar-wasm@0.9.16/dist/main.js':
        path.resolve(__dirname, 'node_modules/@undecaf/zbar-wasm/dist/index.mjs'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __BUILD_ENV__: JSON.stringify(buildEnv),
  },
  base: '/macro_app/',
  server: {
    proxy: {
      '/macro_app/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
