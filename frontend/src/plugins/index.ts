/**
 * plugins/index.ts
 *
 * Automatically included in `./src/main.ts`
 */

// Types
import type { App } from 'vue'

import { useAttachmentImages } from '@/composables/useAttachmentImages'

import i18n from '../i18n'
import router from '../router'
import pinia from '../stores'

import { createDialogPlugin } from './dialog'
import vuetify from './vuetify'

import { ATTACHMENT_IMAGE_SOURCE } from '@/components/common/Editor/attachmentImageSource'

export function registerPlugins(app: App) {
  app.use(i18n).use(vuetify).use(router).use(pinia).use(createDialogPlugin)
  app.provide(ATTACHMENT_IMAGE_SOURCE, useAttachmentImages())
}
