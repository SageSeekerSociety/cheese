<script setup lang="ts">
// What moves the open 一件事 along, and who is doing it, in its header
// (ThingPanel). 开工 hands a 讨论 to 芝士, offered only where 芝士 may change
// files (项目设置「芝士能改文件」); 接手 takes 芝士's work over in the same
// 一件事; 交给芝士 (with a note, in a sheet under the header) hands it back;
// 采纳, once it waits, puts it into the project, and waits itself while 芝士
// is catching up with a project that changed.
import { computed } from 'vue'

import { NAMES } from './data'
import { acceptCopy, canTake, canWork, doingNow, freshness, kickoff, S, startable, takeOver, thingById } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

/** `faces`: only their faces (the name on hover), where the header is narrow. */
const props = defineProps<{ faces?: boolean }>()
const t = computed(() => thingById(S.thing)!)
const talk = computed(() => t.value.status === 'talk')
const who = computed(() => doingNow(t.value))
const working = computed(() => canWork(t.value))
const fresh = computed(() => freshness(t.value))

/** Who is doing it now (started and not done), or who is 负责 once it waits or is done. */
const doingLabel = computed(() => (who.value ? `${NAMES[who.value]}在做` : ''))
const ownerLabel = computed(() => (talk.value || who.value ? null : NAMES[t.value.owner]))

function handOpen() {
  S.handNote = ''
  S.hand = true
}
</script>

<template>
  <span v-if="who" class="ot-acts__who" :title="doingLabel" data-doing>
    <CheeseAvatar v-if="who === 'cheese'" :size="20" name="芝士" handle="cheese" />
    <UserAvatar v-else :name="NAMES[who]" :seed="who" :size="20" />
    <template v-if="!props.faces">{{ doingLabel }}</template>
  </span>
  <span v-else-if="ownerLabel" class="ot-acts__who" :title="`${ownerLabel} 负责`">
    <UserAvatar :name="ownerLabel" :seed="t.owner" :size="20" /><template v-if="!props.faces"
      >{{ ownerLabel }} 负责</template
    >
  </span>
  <BaseButton v-if="startable(t)" id="b-kick" kind="primary" size="sm" @click="kickoff">开工</BaseButton>
  <BaseButton
    v-else-if="canTake(t)"
    id="b-take"
    kind="secondary"
    size="sm"
    title="芝士先停下，你在这件事的文件里接着改"
    @click="takeOver(t.id)"
    >接手</BaseButton
  >
  <BaseButton
    v-else-if="working"
    id="b-hand"
    kind="primary"
    size="sm"
    title="芝士接着做：先跟上项目的最新版，再跑一遍"
    @click="handOpen"
    >交给芝士</BaseButton
  >
  <BaseButton
    v-else-if="t.status === 'wait' && t.copy"
    id="b-accept"
    kind="primary"
    size="sm"
    :disabled="!fresh.fresh || !!t.ask"
    :title="fresh.fresh ? '' : '项目又改过：芝士跟上、跑过之后再采纳'"
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
</style>
