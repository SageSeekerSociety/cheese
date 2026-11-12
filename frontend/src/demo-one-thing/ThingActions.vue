<script setup lang="ts">
// What moves the open 一件事 along, and who is doing it: the same row
// wherever the 一件事's header is (ThingPanel; under 方案 W1, the 工作台's own
// header in the center column while it is open there). 开工 hands a 讨论 to
// 芝士; 接手 takes 芝士's work over, in the same 工作台; 交给芝士 (with a note,
// in a sheet under the 一件事's header) hands it back, or hands on a 一件事 a
// person started; 改好了 says a person is done; 采纳, once it waits, puts it
// into 正式版, and waits while a block both sides changed has no side picked.
// A course's 一件事 has no 接手 or 交给芝士: 芝士 only gives hints there.
import { computed } from 'vue'

import { NAMES } from './data'
import { acceptCopy, canTake, canWork, copyState, doingNow, kickoff, markDone, S, takeOver, thingById } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

/** `quiet`: leave out who is doing it, where the 工作台's own bar already says so. `faces`: only their faces
 *  (the name on hover), where the header is narrow. */
const props = defineProps<{ quiet?: boolean; faces?: boolean }>()
const t = computed(() => thingById(S.thing)!)
const talk = computed(() => t.value.status === 'talk')
const who = computed(() => doingNow(t.value))
const working = computed(() => canWork(t.value))
/** It can be started: 演示一's task, or a 讨论 about code where 芝士 has a change to offer. */
const startable = computed(() => talk.value && (t.value.id === 't4' || !!t.value.copy))
const states = computed(() => (t.value.copy ? copyState(t.value) : []))
/** Something to look at in 改动: 改好了 waits for it. */
const changed = computed(() => states.value.length > 0 || Object.keys(t.value.adds ?? {}).length > 0)
/** Blocks both changed and nobody has picked a side for yet: 采纳 waits on them. */
const open = computed(() => states.value.reduce((n, x) => n + x.open, 0))

/** Who is doing it now (started and not done), or who is 负责 once it waits or is done. */
const people = computed(() => (t.value.team && who.value ? t.value.team : who.value ? [who.value] : []))
const doingLabel = computed(() => (who.value ? `${people.value.map((h) => NAMES[h]).join('、')}在做` : ''))
const ownerLabel = computed(() => (talk.value || who.value ? null : NAMES[t.value.owner]))

function handOpen() {
  S.handNote = ''
  S.hand = true
}
</script>

<template>
  <span v-if="doingLabel && !props.quiet" class="ot-acts__who" :title="doingLabel" data-doing>
    <span class="ot-acts__faces">
      <template v-for="h in people" :key="h">
        <CheeseAvatar v-if="h === 'cheese'" :size="20" name="芝士" handle="cheese" />
        <UserAvatar v-else :name="NAMES[h]" :seed="h" :size="20" />
      </template>
    </span>
    <template v-if="!props.faces">{{ doingLabel }}</template>
  </span>
  <span v-else-if="ownerLabel && !props.quiet" class="ot-acts__who" :title="`${ownerLabel} 负责`">
    <UserAvatar :name="ownerLabel" :seed="t.owner" :size="20" /><template v-if="!props.faces"
      >{{ ownerLabel }} 负责</template
    >
  </span>
  <BaseButton v-if="startable" id="b-kick" kind="primary" size="sm" @click="kickoff">开工</BaseButton>
  <BaseButton
    v-else-if="canTake(t)"
    id="b-take"
    kind="secondary"
    size="sm"
    title="芝士先停下，你在同一个工作台里接着改"
    @click="takeOver(t.id)"
    >接手</BaseButton
  >
  <template v-else-if="working">
    <BaseButton v-if="!t.course" id="b-hand" kind="ghost" size="sm" title="芝士在这个工作台里接着做" @click="handOpen"
      >交给芝士</BaseButton
    >
    <BaseButton id="b-done" kind="primary" size="sm" :disabled="!changed" @click="markDone(t.id)">改好了</BaseButton>
  </template>
  <BaseButton
    v-else-if="t.status === 'wait' && t.copy"
    id="b-accept"
    kind="primary"
    size="sm"
    :disabled="open > 0"
    :title="open ? '先在「改动」里挑两边都改了的那几行用哪边' : ''"
    @click="acceptCopy(t.id)"
  >
    采纳
  </BaseButton>
</template>

<style scoped>
.ot-acts__who {
  display: inline-flex;
  flex: none;
  gap: 6px;
  align-items: center;
  margin-right: 4px;
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
}

.ot-acts__faces {
  display: inline-flex;
}

.ot-acts__faces > * + * {
  margin-left: -6px;
  border-radius: 50%;
  box-shadow: 0 0 0 2px var(--surface);
}
</style>
