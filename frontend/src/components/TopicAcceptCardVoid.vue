<script setup lang="ts">
// 作废这张卡: the human exit out of a card that can no longer move.
//
// `conflict` and `pr_open` are refused by accept / reject / revoke / reassign
// alike, and a live card is itself what stops the topic filing a new one — so a
// room that reaches either can never deliver again. 真实案例: PR #545 被人工关闭
// 后卡永久停在 pr_open，界面上一个能点的东西都没有。The backend has had the exit
// since 2026-08-11; until now nothing on screen called it.
//
// The third status `void` takes, `pending_gate`, deliberately gets no face here:
// nothing mints it since the machine gate retired, and `gate_sweep.condemn`
// ages the rows that predate the retirement into `gate_failed`, which does not
// block a new card. A button there would be a step built onto a closed road.
//
// It is NOT 放行: the card goes to a terminal state, and the way forward is
// 重新递卡. That distinction is the reason void exists at all (letting the card
// back to `pending` would put the box's green tick behind code nobody checked),
// so the copy here has to state the terminal part rather than imply a retry.
//
// It sits behind a collapsed text button with a reason field, the same shape as
// 人工放行并合并 next to it: both are decisions the platform will never make on
// its own, and neither should be reachable by a stray click.
import type { AcceptCard } from '@/cx_types'

import { ref } from 'vue'

import { voidAcceptCard } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ card: AcceptCard; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'voided'): void }>()
const store = useWorkspaceStore()

const showInput = ref(false)
const note = ref('')
const busy = ref(false)

async function onVoid() {
  busy.value = true
  try {
    await voidAcceptCard(props.card.id, note.value)
    showInput.value = false
    note.value = ''
    emit('voided')
  } catch (e) {
    // 后端拒绝时说的是原因（不是验收人、卡已是终态、芝士不能作废），照原话显示
    // —— 一个只写「作废失败」的提示会让人以为平台坏了。
    store.reportError(e, '作废失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="mt-3">
    <v-btn
      v-if="!showInput"
      size="small"
      variant="text"
      class="text-medium-emphasis"
      prepend-icon="mdi-cancel"
      :disabled="disabled"
      @click="showInput = true"
    >
      作废这张卡
    </v-btn>
    <template v-else>
      <div class="text-caption text-medium-emphasis mb-1">
        这次采纳就此停下，卡不再推进，也不会合并。话题可以重新递卡。
        <template v-if="card.pr_url">PR 仍然开在 GitHub 上，平台不会替你关闭它。</template>
      </div>
      <v-textarea
        v-model="note"
        label="理由"
        rows="2"
        auto-grow
        density="compact"
        variant="outlined"
        hide-details
        class="mb-2"
      />
      <div class="d-flex ga-2">
        <v-btn size="small" color="warning" variant="flat" :loading="busy" :disabled="busy" @click="onVoid">
          确认作废
        </v-btn>
        <v-btn size="small" variant="text" :disabled="busy" @click="showInput = false"> 取消 </v-btn>
      </div>
    </template>
  </div>
</template>
