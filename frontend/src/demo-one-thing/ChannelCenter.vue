<script setup lang="ts">
// The center column while a channel is open: its header, the main line drawn
// by the product's message rows, a 一件事 card under the message that opened it,
// and the product's composer.
import { computed, nextTick, ref, watch } from 'vue'

import { CHANNELS, NAMES } from './data'
import { block, ownerName, REFS, taskLine } from './shapes'
import { openThing, S, send, thingById } from './state'

import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerEditor from '@/components/room/ComposerEditor.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TaskCard from '@/components/room/TaskCard.vue'

const channel = computed(() => CHANNELS.find((c) => c.id === S.chan)!)
const lines = computed(() => S.chat[S.chan] ?? [])
const scroller = ref<HTMLElement | null>(null)

watch(
  () => lines.value.length,
  async () => {
    await nextTick()
    scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' })
  }
)

function runStart(i: number): boolean {
  return i === 0 || lines.value[i - 1]!.who !== lines.value[i]!.who
}
</script>

<template>
  <section class="ot-chan" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title"># {{ channel.title }}</h2>
      <span class="ot-head__sub">{{ channel.about }}</span>
      <span class="ot-head__gap" />
      <span class="ot-head__sub">4 人 · 芝士</span>
    </header>
    <div ref="scroller" class="ot-chan__lines">
      <div v-for="(line, i) in lines" :key="line.id" class="ot-chan__line">
        <RoomMessage
          :block="block(line)"
          :parent="null"
          :parent-name="null"
          :run-start="runStart(i)"
          :mine="line.who === 'linxiao'"
          :topic-id="null"
          :author-name="NAMES[line.who] ?? line.who"
          :avatar="null"
          :is-agent="line.who === 'cheese'"
          :time="line.time"
          :refs="REFS"
          viewer="linxiao"
          :ask-busy="false"
        />
        <div v-if="line.thing && thingById(line.thing)" class="ot-chan__card">
          <TaskCard
            :id="`card-${line.thing}`"
            :class="{ 'ot-sel': S.thing === line.thing, 'ot-flash': S.morph && line.thing === 't4' }"
            :task="taskLine(thingById(line.thing)!)"
            :owner-name="ownerName(thingById(line.thing)!)"
            @open="openThing"
          />
        </div>
      </div>
    </div>
    <div class="ot-composer">
      <div class="ot-composer__box" data-zone="composer">
        <ComposerEditor v-model="S.typing" :names="REFS" placeholder="说点什么，@芝士 就开一个讨论" />
        <ComposerActions
          :uploading="false"
          :can-send="true"
          :show-image-picker="false"
          :enter-sends="true"
          :can-voice="false"
          @send="send"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-chan {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-chan__lines {
  flex: 1;
  min-height: 0;
  padding: 16px 0 8px;
  overflow: auto;
}

.ot-chan__card {
  /* Under the message text: lines up with the message body, past the avatar column. */
  padding: 4px 16px 8px 60px;
}

.ot-composer {
  padding: 8px 16px 12px;
}

.ot-composer__box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.ot-composer__box:focus-within {
  border-color: var(--faint);
}
</style>
