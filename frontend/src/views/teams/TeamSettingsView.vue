<template>
  <SettingsOverlay
    :label="t('teams.settings.overlay')"
    :groups="groups"
    :active="active"
    :index-to="backTo"
    :close-label="t('teams.settings.close')"
    :close-title="t('teams.settings.closeHint')"
    :back-label="t('teams.settings.back')"
    @close="emit('close')"
  >
    <template #head>
      <div class="whose">
        <UserAvatar :avatar="avatar" :name="name" :seed="handle" size="32" kind="org" />
        <div class="whose__text">
          <span class="whose__name" data-user-content>{{ name }}</span>
          <span class="whose__sub">{{ t('teams.settings.overlay') }}</span>
        </div>
      </div>
    </template>
    <slot />
  </SettingsOverlay>
</template>

<script setup lang="ts">
// 团队设置那一层的画面：谁的设置、左边几栏。和个人、空间、项目的设置同一个外框；
// 每一栏自己画标题、说明和这一栏的操作（`styles/settings-card.css` 的 `.settings-page`），
// 和个人设置的各页一样。读地址、算有哪几栏的是 `Detail.vue`，这里只吃 props。
import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'
import type { NavTarget } from '@/lib/navTarget'

import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

defineProps<{
  name: string
  handle: string
  avatar?: string
  groups: SettingsGroup[]
  /** 目录里选中的那一栏；手机上停在目录时为空。 */
  active: string | null
  /** 手机上左上角返回目录。 */
  backTo: NavTarget
}>()

const emit = defineEmits<{ close: [] }>()

defineSlots<{ default?: () => unknown }>()
</script>

<style scoped>
.whose {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 10px 4px;
}

.whose__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.whose__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.whose__sub {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
