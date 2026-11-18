<script setup lang="ts">
// 文档顶栏上的「谁也在这儿」：同一篇文档打开着的其他人的头像。连没连上写在顶栏的
// 左边（DocTopBar）。
//
// 头像形状照 GitHub：人是圆的，AI 队友是方的（CheeseAvatar）。
import type { DocPeer } from '../../../composables/useDocCollab'

import { computed } from 'vue'

import CheeseAvatar from '../../CheeseAvatar.vue'
import UserAvatar from '../../common/UserAvatar.vue'

const props = defineProps<{
  peers: DocPeer[]
  /** handle 读成名字：传了就用它（名册 + 当前查看者的个人备注），没传就用广播里的名字。 */
  nameOf?: (handle: string) => string
}>()

/** 这一位在这块屏幕上叫什么：查看者给他设过备注就是备注，否则是他自己报的名字。 */
function displayName(peer: DocPeer): string {
  const resolved = props.nameOf?.(peer.handle)
  return resolved && resolved !== peer.handle ? resolved : peer.name
}

const MAX_FACES = 4

// 同一个人开了两个标签页是两份在线状态，头像只画一次。
const people = computed(() => {
  const seen = new Set<string>()
  return props.peers.filter((peer) => {
    if (seen.has(peer.handle)) return false
    seen.add(peer.handle)
    return true
  })
})
const shown = computed(() => people.value.slice(0, MAX_FACES))
const more = computed(() => people.value.length - shown.value.length)
</script>

<template>
  <div class="doc-presence">
    <div v-if="shown.length" class="doc-presence__faces">
      <span
        v-for="peer in shown"
        :key="peer.handle"
        class="doc-presence__face"
        :title="`${displayName(peer)} · ${peer.handle}`"
      >
        <CheeseAvatar v-if="peer.agent" :size="22" :name="displayName(peer)" :handle="peer.handle" />
        <UserAvatar v-else :size="22" :name="displayName(peer)" :avatar="peer.avatar" :alt="displayName(peer)" />
      </span>
      <span v-if="more > 0" class="doc-presence__more">+{{ more }}</span>
    </div>
  </div>
</template>

<style scoped>
.doc-presence {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.doc-presence__faces {
  display: inline-flex;
  align-items: center;
}
.doc-presence__face {
  display: inline-flex;
  margin-left: -4px;
  border-radius: var(--radius-pill);
  box-shadow: 0 0 0 2px var(--surface);
}
.doc-presence__face:first-child {
  margin-left: 0;
}
.doc-presence__more {
  margin-left: 4px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
</style>
