<script setup lang="ts">
/**
 * 主应用的快捷键表：按 `?`（焦点不在输入框里时）或点输入框旁那颗键盘按钮打开。
 *
 * 这里只列真的绑上了的键，按「焦点在哪才管用」分三组：全局、对话、输入框。输入框
 * 那几个键原来只写在输入框的 `title` 里，键盘用户和读屏都拿不到。
 */
import type { ShortcutGroup } from './shortcutSheet'

import { computed } from 'vue'

import { railShortcut } from './Navigation/destinations'
import { appShortcutSheetOpen } from './shortcutSheet'
import ShortcutSheet from './ShortcutSheet.vue'

import { t } from '@/i18n'
import { inDesktopApp } from '@/lib/desktopApp'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const mod = isMac ? '⌘' : 'Ctrl'

const groups = computed<ShortcutGroup[]>(() => {
  const desktop = inDesktopApp()
  const rail = railShortcut(1, desktop).keys.map((key) => (key === '1' ? '1–9' : key === '⌘' ? mod : key))
  return [
    {
      scope: t('global.shortcuts.scope.global'),
      rows: [
        { keys: [`${mod}+K`], action: t('global.shortcuts.palette') },
        desktop
          ? { keys: [rail.join('+')], action: t('global.shortcuts.railSwitch') }
          : {
              keys: rail,
              sequence: true,
              action: t('global.shortcuts.railSwitch'),
              note: t('global.shortcuts.within1s'),
            },
        { keys: ['?'], action: t('global.shortcuts.openSheet'), note: t('global.shortcuts.notInInputs') },
      ],
    },
    {
      scope: t('global.shortcuts.scope.timeline'),
      rows: [
        { keys: ['↑', '↓'], action: t('global.shortcuts.scrollTimeline'), note: t('global.shortcuts.timelineFocus') },
        { keys: ['PgUp', 'PgDn'], action: t('global.shortcuts.scrollTimeline') },
      ],
    },
    {
      scope: t('global.shortcuts.scope.composer'),
      rows: [
        { keys: ['Enter'], action: t('global.shortcuts.send') },
        { keys: ['Shift+Enter'], action: t('global.shortcuts.newline') },
        { keys: [`${mod}+Enter`], action: t('global.shortcuts.summon', { name: t('work.room.defaultAgentName') }) },
        { keys: ['↑', '↓'], action: t('global.shortcuts.mentionPick') },
        { keys: ['Esc'], action: t('global.shortcuts.mentionClose') },
      ],
    },
  ]
})
</script>

<template>
  <ShortcutSheet
    :model-value="appShortcutSheetOpen"
    :title="t('global.shortcuts.title')"
    :groups="groups"
    @update:model-value="appShortcutSheetOpen = $event"
  />
</template>
