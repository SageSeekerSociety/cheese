<template>
  <SettingsOverlay
    :label="label"
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
        <UserAvatar :avatar="avatar" :name="name" :seed="handle" size="32" :kind="personal ? 'person' : 'org'" />
        <div class="whose__text">
          <span class="whose__name" data-user-content>{{ name }}</span>
          <span class="whose__sub">{{ label }}</span>
        </div>
      </div>
    </template>

    <div class="team-settings">
      <div v-if="title" class="team-settings__head">
        <h1 class="t-page-title">{{ title }}</h1>
        <div v-if="actions.length" class="team-settings__actions">
          <BaseButton
            v-for="command in actions"
            :key="command.id"
            :kind="command.header?.accent ? 'primary' : 'ghost'"
            :prepend-icon="command.icon"
            size="sm"
            :to="command.to"
            :loading="command.loading"
            :disabled="command.disabled"
            @click="command.run?.()"
          >
            {{ command.title }}
          </BaseButton>
        </div>
      </div>
      <slot />
    </div>
  </SettingsOverlay>
</template>

<script setup lang="ts">
// 团队设置那一层的画面：谁的设置、左边几栏、右边这一栏。读地址、算有哪几栏的是
// `TeamSettings.vue`，这里只吃 props。
import type { HeaderCommand } from '@/commands'
import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const props = defineProps<{
  name: string
  handle: string
  avatar?: string
  /** 自己名下的：头像是圆的，这一层只叫「设置」。 */
  personal: boolean
  groups: SettingsGroup[]
  /** 目录里选中的那一栏；手机上停在目录时为空。 */
  active: string | null
  /** 这一栏的标题。 */
  title: string
  /** 手机上左上角返回目录。 */
  backTo: NavTarget
  /** 这一栏的页头动作，画在标题右边。 */
  actions: readonly HeaderCommand[]
}>()

const emit = defineEmits<{ close: [] }>()

defineSlots<{ default?: () => unknown }>()

const label = computed(() => (props.personal ? t('teams.settings.mine') : t('teams.settings.overlay')))
</script>

<style scoped>
/* 和空间设置同一条内容列（SettingsOverlay 的 `.so__content`）：这里只留竖向节奏。 */
.team-settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 0 48px;
}

.team-settings__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  justify-content: space-between;
}

.team-settings__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

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

@media (max-width: 767.98px) {
  .team-settings {
    padding: 16px 0 32px;
  }
}
</style>
