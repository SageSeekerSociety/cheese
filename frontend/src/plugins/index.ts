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

import { useAttachmentImages } from '@/composables/useAttachmentImages'

import i18n from '../i18n'
import router from '../router'
import pinia from '../stores'

import { createDialogPlugin } from './dialog'
import vuetify from './vuetify'

import { ATTACHMENT_IMAGE_SOURCE } from '@/components/common/Editor/attachmentImageSource'

export function registerPlugins(app: App) {
  app.use(i18n).use(vuetify).use(router).use(pinia).use(viewer).use(createDialogPlugin)
  app.provide(ATTACHMENT_IMAGE_SOURCE, useAttachmentImages())
}
