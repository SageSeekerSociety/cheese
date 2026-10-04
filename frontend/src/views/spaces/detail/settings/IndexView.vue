<template>
  <SettingsOverlay
    :label="t('spaces.settings.overlay')"
    :groups="groups"
    :active="active"
    :index-to="backTo"
    :close-label="t('spaces.settings.close')"
    :close-title="t('spaces.settings.closeHint')"
    :back-label="t('spaces.settings.back')"
    @close="emit('close')"
  >
    <template #head>
      <div class="whose">
        <UserAvatar :avatar="avatar" :name="spaceName" size="32" kind="org" />
        <div class="whose__text">
          <span class="whose__name">{{ spaceName }}</span>
          <span class="whose__sub">{{ t('spaces.settings.overlay') }}</span>
        </div>
      </div>
    </template>

    <div class="space-settings">
      <h1 v-if="title" class="t-page-title">{{ title }}</h1>
      <slot />
    </div>
  </SettingsOverlay>
</template>

<script setup lang="ts">
// 空间设置那一层的画面：谁的设置、左边五栏、右边这一栏。读地址、取空间的是
// `Index.vue`，这里只吃 props。
import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'
import type { NavTarget } from '@/lib/navTarget'

import { useI18n } from 'vue-i18n'

import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

defineProps<{
  spaceName?: string
  avatar?: string
  groups: SettingsGroup[]
  /** 目录里选中的那一栏；手机上停在目录时为空。 */
  active: string | null
  /** 这一栏的标题；模板表单自己画标题，传空。 */
  title: string
  /** 手机上左上角返回去哪：一般是目录，模板表单回模板列表。 */
  backTo: NavTarget
}>()

const emit = defineEmits<{ close: [] }>()

defineSlots<{ default?: () => unknown }>()

const { t } = useI18n()
</script>

<style scoped>
/* 空间设置这一栏也住在浮层那一条内容列里（SettingsOverlay 的 `.so__content`，
   720 居中）：宽度和水平内距不在这里，只留这一页自己的竖向节奏，四类设置页才对齐。 */
.space-settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 0 48px;
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
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .space-settings {
    padding: 16px 0 32px;
  }
}
</style>
