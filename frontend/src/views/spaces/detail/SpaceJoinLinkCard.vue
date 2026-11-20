<script setup lang="ts">
// 空间成员页上的「加入链接」：一条长期有效、可以重置的链接，和「加入需要批准」那个开关。
// 和团队那张（`views/teams/detail/TeamJoinLinkCard.vue`）同一个形状。
//
// 这一件只吃数据、往上报动作：链接取没取回来、写成了没有、复制成没成，都是页面
// （`Members.vue`）那一层的事。
import type { SpaceJoinLink } from '@/types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 这个空间的加入链接；还没读回来时是 `null`。 */
  link: SpaceJoinLink | null
  /** 有事在做：按钮和开关都按不动。 */
  busy: boolean
  /** 上一次没做成的那一句。 */
  error: string
  /** 刚复制过：按钮上的字换成「已复制」。 */
  copied: boolean
}>()

const emit = defineEmits<{
  /** 换一条新的链接，旧的立刻失效。 */
  reset: []
  /** 加入需不需要批准。 */
  setApproval: [approval: boolean]
  /** 复制这一条链接。 */
  copy: [url: string]
}>()

const url = computed(() => (props.link ? `${window.location.origin}/space-invites/${props.link.token}` : ''))
</script>

<template>
  <section class="settings-card">
    <h2 class="settings-card__title join__title">{{ t('spaces.members.joinLink.heading') }}</h2>
    <p v-if="error" role="alert" class="t-body join__error">{{ error }}</p>

    <div class="srow join__row">
      <span class="srow__k">{{ t('spaces.members.joinLink.title') }}</span>
      <div class="join__stack">
        <v-progress-linear v-if="busy && !link" indeterminate :aria-label="t('spaces.members.joinLink.loading')" />
        <div v-if="link" class="join__link">
          <v-text-field
            autocomplete="off"
            :model-value="url"
            :aria-label="t('spaces.members.joinLink.title')"
            readonly
            hide-details
            density="compact"
            variant="outlined"
            class="join__field"
          />
          <BaseButton kind="secondary" :disabled="busy" @click="emit('copy', url)">
            {{ copied ? t('spaces.members.joinLink.copied') : t('spaces.members.joinLink.copy') }}
          </BaseButton>
          <BaseButton :disabled="busy" @click="emit('reset')">{{ t('spaces.members.joinLink.reset') }}</BaseButton>
        </div>
        <span class="join__note">{{ t('spaces.members.joinLink.hint') }}</span>
      </div>
    </div>

    <div v-if="link" class="srow join__row">
      <span class="srow__k">{{ t('spaces.members.joinLink.approval') }}</span>
      <div class="join__switch">
        <span class="join__note">{{ t('spaces.members.joinLink.approvalHint') }}</span>
        <v-switch
          :model-value="link.approval"
          :aria-label="t('spaces.members.joinLink.approval')"
          :disabled="busy"
          color="primary"
          hide-details
          inset
          density="compact"
          @update:model-value="(value) => emit('setApproval', !!value)"
        />
      </div>
    </div>
  </section>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.join__title {
  margin: 0;
}

.join__error {
  padding: 0 24px 8px;
  margin: 0;
  color: var(--danger-ink);
}

/* 一行放一组控件：标签对着控件的第一行。 */
.join__row {
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 16px 24px;
}

.join__row > .srow__k {
  padding-top: 8px;
}

.join__stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.join__link {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.join__field {
  flex: 1 1 260px;
  min-width: 0;
}

.join__switch {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
}

.join__switch :deep(.v-switch) {
  flex: none;
}

.join__note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

@media (max-width: 767.98px) {
  .join__row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .join__row > .srow__k {
    padding-top: 0;
  }
}
</style>
