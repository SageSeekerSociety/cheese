/**
 * plugins/index.ts
 *
 * Automatically included in `./src/main.ts`
 */

// Plugins
import 'viewerjs/dist/viewer.css'

// Types
import type { App } from 'vue'

import viewer from 'v-viewer'

import i18n from '../i18n'
import router from '../router'
import pinia from '../stores'

import { createDialogPlugin } from './dialog'
import vuetify from './vuetify'

// vuetify-pro-tiptap is deliberately absent: installing it here dragged
// vuetify-pro-tiptap + prosemirror + tiptap (~1.09 MB) into the entry's static
// import graph, so every page — the login screen included — modulepreloaded a
// rich-text editor almost none of them render. It installs itself on the first
// editor mount instead; see `installVuetifyProTipTap` in ./tiptap.
export function registerPlugins(app: App) {
  app.use(i18n).use(vuetify).use(router).use(pinia).use(viewer).use(createDialogPlugin)
}
