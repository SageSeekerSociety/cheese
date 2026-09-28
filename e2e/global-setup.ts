/** Compile the whole app in the dev server before the first test runs.
 *
 * The suite runs against `vite` in dev mode, which compiles a module the first
 * time a browser asks for it. CI starts it cold, so whichever test first opened
 * a route paid for compiling that route inside its own assertions: opening a
 * topic for the first time spent 4-7 s loading modules before the URL changed,
 * past the default 5 s wait. Loading every module once here moves that cost out
 * of the tests; afterwards the same import takes about 0.1 s.
 *
 * The walk follows the module graph the way the browser would, from the entry
 * in `index.html` through static and dynamic imports (the routes are lazy). It
 * also reaches dependencies that only a transform reveals, so vite re-optimizes
 * and reloads here instead of in the middle of a test.
 */
import type { FullConfig } from '@playwright/test';

const ENTRY = '/src/main.ts';
// `import x from "/src/a.vue"`, `import "/src/a.css"`, `import("/src/b.vue")`:
// vite rewrites every import to an absolute path from the server root.
const IMPORT = /(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\/[^"']+)["']/g;
// Enough to keep the server busy; hundreds at once overflow its accept queue.
const CONCURRENCY = 16;

export default async function warmDevServer(config: FullConfig): Promise<void> {
  const base = config.projects[0].use.baseURL;
  if (!base) throw new Error('no baseURL to warm');
  const seen = new Set([ENTRY]);
  const pending = [ENTRY];
  let loading = 0;
  await new Promise<void>((resolve, reject) => {
    const next = () => {
      if (pending.length === 0 && loading === 0) {
        resolve();
        return;
      }
      while (loading < CONCURRENCY && pending.length > 0) {
        const path = pending.shift()!;
        loading += 1;
        load(new URL(path, base))
          .then((code) => {
            for (const [, url] of code.matchAll(IMPORT)) {
              if (!seen.has(url)) {
                seen.add(url);
                pending.push(url);
              }
            }
            loading -= 1;
            next();
          })
          .catch(reject);
      }
    };
    next();
  });
}

async function load(url: URL): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`warming ${url.pathname}: HTTP ${response.status}`);
  return response.text();
}
