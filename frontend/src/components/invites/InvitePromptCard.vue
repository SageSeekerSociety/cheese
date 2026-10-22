<script setup lang="ts">
/**
 * 第一次采纳之后那一张「邀请好友」小卡（接线在 `composables/useInvitePrompt.ts`，
 * 挂在 `layouts/AppShell.vue`）。浮在右下角、不挡页面：他还要看刚采纳的那一份。
 * 手机上贴着底栏上方，左右各留 16px。
 */
import type { NavTarget } from '@/lib/navTarget'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

defineProps<{
  open: boolean
  /** 邀请好友页；没有登录的人时为 null，不画链接。 */
  invitesTo: NavTarget | null
  /** 画在页面里（组件目录、截图）而不是浮在右下角。 */
  inline?: boolean
}>()

const emit = defineEmits<{ copyLink: []; dismiss: [] }>()
</script>

<template>
  <Teleport to="body" :disabled="inline">
    <section
      v-if="open"
      class="invite-prompt"
      :class="{ 'invite-prompt--inline': inline }"
      role="dialog"
      aria-labelledby="invite-prompt-title"
    >
      <div class="invite-prompt__head">
        <v-icon icon="mdi-account-plus-outline" size="20" class="invite-prompt__icon" aria-hidden="true" />
        <h2 id="invite-prompt-title" class="invite-prompt__title">{{ t('account.invitePrompt.title') }}</h2>
        <BaseButton
          class="invite-prompt__close"
          size="sm"
          icon="mdi-close"
          :aria-label="t('account.invitePrompt.close')"
          @click="emit('dismiss')"
        />
      </div>
      <p class="invite-prompt__text">{{ t('account.invitePrompt.seats', { n: 3 }) }}</p>
      <ul class="invite-prompt__gains">
        <li>
          <span>{{ t('account.invitePrompt.friend') }}</span>
          <span class="t-num">{{ t('account.invitePrompt.friendGain') }}</span>
        </li>
        <li>
          <span>{{ t('account.invitePrompt.you') }}</span>
          <span class="t-num">{{ t('account.invitePrompt.youGain') }}</span>
        </li>
      </ul>
      <div class="invite-prompt__foot">
        <NavLink v-if="invitesTo" class="invite-prompt__link" :to="invitesTo" @click="emit('dismiss')">
          {{ t('account.invitePrompt.more') }}
        </NavLink>
        <span class="invite-prompt__actions">
          <BaseButton size="sm" @click="emit('dismiss')">{{ t('account.invitePrompt.later') }}</BaseButton>
          <BaseButton kind="primary" size="sm" prepend-icon="mdi-link-variant" @click="emit('copyLink')">
            {{ t('account.invitePrompt.copyLink') }}
          </BaseButton>
        </span>
      </div>
    </section>
  </Teleport>
</template>

<style scoped>
.invite-prompt {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: var(--z-overlay);
  box-sizing: border-box;
  display: grid;
  gap: 10px;
  width: 360px;
  padding: 14px 16px 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
  animation: invite-prompt-in var(--dur-base) var(--ease-out);
}

.invite-prompt--inline {
  position: static;
  animation: none;
}

.invite-prompt__head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.invite-prompt__icon {
  color: var(--accent);
}

.invite-prompt__title {
  flex: 1;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.invite-prompt__close {
  margin: -4px -6px -4px 0;
}

.invite-prompt__text {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.invite-prompt__gains {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 12px;
  padding: 10px 12px;
  margin: 0;
  list-style: none;
  border-radius: var(--radius-md);
  background: var(--fill);
}

.invite-prompt__gains li {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: subgrid;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invite-prompt__gains li > :last-child {
  color: var(--text);
}

.invite-prompt__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 2px;
}

.invite-prompt__link {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.invite-prompt__actions {
  display: flex;
  gap: 6px;
}

@keyframes invite-prompt-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

@media (max-width: 767.98px) {
  .invite-prompt {
    right: 16px;
    /* 底栏 56px（BottomAppBar.vue）：卡贴在它上方。 */
    bottom: calc(56px + 16px + env(safe-area-inset-bottom, 0px));
    left: 16px;
    width: auto;
  }

  .invite-prompt--inline {
    width: auto;
  }
}
</style>
