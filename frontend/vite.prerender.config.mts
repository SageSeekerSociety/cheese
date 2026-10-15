// The build of src/prerender/entry-server.ts that scripts/prerender.mjs runs in
// Node to render the public pages into static HTML. It is a delta over
// vite.config.ts, not a second config, for the reason vite.probe.config.mts
// gives: the same plugins (Vue, Vuetify, the icon font) compile the same
// components the same way, so the scoped-style ids in the rendered HTML are the
// ones the client build's stylesheets select. Only the build itself differs.
import { defineConfig, type UserConfig } from 'vite'

export default defineConfig(async () => {
  const base = (await import('./vite.config.ts')).default as UserConfig
  return {
    ...base,
    build: {
      ssr: 'src/prerender/entry-server.ts',
      outDir: 'dist-ssr',
      emptyOutDir: true,
      target: 'node22',
      // Run once at build time and deleted: nothing gains from minifying it.
      minify: false,
      ssrManifest: false,
    },
    // Bundle the dependencies too: Vuetify's components import their own .css,
    // which Node cannot load.
    ssr: { noExternal: true },
  }
})
