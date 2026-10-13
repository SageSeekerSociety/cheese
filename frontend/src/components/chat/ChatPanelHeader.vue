<script setup lang="ts">
// The topic header: the PR-style one for real work topics (话题 = PR) and the
// plain one for the root topic / 私聊. Drawing only — which of the two shows is
// decided by the props, and 工作台 passes hideHeader because it puts ONE topic
// header above both columns (see TopicHeader.vue).
import type { Topic } from '../../cx_types'
import type { PersonIdentity } from '../../lib/personIdentity'

import BaseButton from '@/components/base/BaseButton.vue'
import IdentityView from '@/components/common/IdentityView.vue'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'

withDefaults(
  defineProps<{
    topic: Topic
    connected: boolean
    prHeader: boolean
    hideHeader: boolean
    prShortId: string
    prState: { cls: string; label: string }
    titleOverride: string | null
    // 私聊顶栏上那个人的一张脸：`titleOverride` 是它的名字，这里补上认人的 handle、
    // 自己挑过的头像和「是不是队友」。null = 这幅标题不是一个人（本体、其它话题），
    // 照旧只写一行字。
    titlePeer?: PersonIdentity | null
    backLabel: string | null
  }>(),
  { titlePeer: null }
)

const emit = defineEmits<{
  // 标题左边那颗 ← 被按了。去哪儿由拥有这个地址的人决定，不是这里。
  (e: 'back'): void
}>()
</script>

<template>
  <!-- GitHub-PR-style header (Fix 4): only for real work topics (话题 = PR).
           The root topic (本体) and private chat use the plain header below. -->
  <div v-if="!hideHeader && prHeader" class="pr-header px-4 py-3">
    <div class="d-flex align-center ga-2 flex-wrap">
      <span class="t-title">{{ topicTitle(topic) }}</span>
      <span class="pr-num t-meta">#{{ prShortId }}</span>
      <v-spacer />
      <span class="pr-state ms-1" :class="prState.cls">{{ prState.label }}</span>
      <span
        class="status-dot"
        :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
        :title="connected ? t('work.room.header.connected') : t('work.room.header.disconnected')"
      />
    </div>
  </div>

  <!-- Plain chat header — normal chat (飞书私聊 / 本体): title + 已连接 -->
  <div v-else-if="!hideHeader" class="pr-header px-4 py-3">
    <div class="d-flex align-center ga-2">
      <BaseButton
        v-if="backLabel"
        kind="ghost"
        size="sm"
        density="comfortable"
        prepend-icon="mdi-arrow-left"
        @click="emit('back')"
      >
        {{ backLabel }}
      </BaseButton>
      <!-- The peer's face + name when this is a 私聊 (titlePeer), the plain title
           otherwise. The name is NOT printed twice: IdentityView carries it. -->
      <IdentityView
        v-if="titlePeer"
        class="pr-header__peer"
        :handle="titlePeer.handle"
        :name="titlePeer.name || titleOverride"
        :avatar-id="titlePeer.avatarId"
        :agent="titlePeer.agent"
        :size="20"
      />
      <span v-else class="t-title">{{ titleOverride || topicTitle(topic) }}</span>
      <v-spacer />
      <span
        class="status-dot"
        :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
        :title="connected ? t('work.room.header.connected') : t('work.room.header.disconnected')"
      />
    </div>
  </div>
</template>

<style scoped>
/* ---- GitHub PR header ---- */
.pr-header {
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.pr-num {
  font-weight: 400;
}
/* 私聊顶栏上那一位：字号字重照 `.t-title` 那一档，但行高得跟着它走 ——
   `identity-view__name` 自带一行 14px 行高，15px 的字会在那一行里被切掉。 */
.pr-header__peer {
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}
.pr-header__peer :deep(.identity-view__name) {
  line-height: var(--lh-15);
}
/* PR state badge — semantic for Open, muted otherwise. */
.pr-state {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}
.pr-state--open {
  /* --surface, not #fff: the ground (--ok) lightens on dark (#3FBF7F), where
     white ink drops to 2.34:1. --surface IS #fff in light, so the badge looks
     exactly as it does today, and flips to near-black ink on dark. (§1.5's
     canonical chip is --ok-ink on --ok-wash, which would also lift the light
     side above AA, but that changes how the badge looks — a call for the
     design owner, not this pass.) */
  color: var(--surface);
  background: var(--ok);
}
.pr-state--merged {
  color: var(--muted);
  background: var(--fill);
}
.pr-state--draft {
  color: var(--faint);
  background: var(--fill);
}
</style>
