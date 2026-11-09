<script setup lang="ts">
// The 1280×800 workspace: project rail, sidebar, the center column (always the
// channel, or 需要我的 / 项目文件 / 设置) and the right panel (the open 一件事,
// or a file's 待定改动 and 历史). The scripted cursor, the notes overlay and the
// toast sit on top of it.
import { computed } from 'vue'

import ChannelCenter from './ChannelCenter.vue'
import { CHANNELS } from './data'
import FilesCenter from './FilesCenter.vue'
import FilesPanel from './FilesPanel.vue'
import InboxCenter from './InboxCenter.vue'
import SettingsCenter from './SettingsCenter.vue'
import { ownerName, taskLine } from './shapes'
import SideNav from './SideNav.vue'
import { openThing, S, thingById } from './state'
import ThingPanel from './ThingPanel.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import TaskCard from '@/components/room/TaskCard.vue'

defineProps<{
  notes: {
    key: string
    x: number
    y: number
    w: number
    h: number
    title: string
    text: string
    nx: number
    ny: number
  }[]
}>()

const right = computed<'thing' | 'files' | 'chan' | null>(() => {
  if (S.center === 'files') return 'files'
  if (S.center === 'chan') return S.thing && thingById(S.thing) ? 'thing' : 'chan'
  return null
})

const wide = computed(() => right.value === 'thing' && S.thing === 't4' && thingById('t4')?.status !== 'talk')
const cols = computed(() => {
  const r = right.value === null ? '0px' : right.value === 'files' ? '320px' : wide.value ? '560px' : '440px'
  return `52px 232px minmax(0, 1fr) ${r}`
})

const chanThings = computed(() => S.things.filter((t) => t.chan === S.chan))
const chanTitle = computed(() => CHANNELS.find((c) => c.id === S.chan)?.title)
</script>

<template>
  <div id="ot-app" class="ot-app" :style="{ gridTemplateColumns: cols }">
    <div class="ot-rail">
      <UserAvatar kind="org" name="多模态检索" seed="proj-mm" :size="32" class="ot-rail__on" />
      <UserAvatar kind="org" name="知识图谱" seed="proj-kg" :size="32" />
      <UserAvatar kind="org" name="数据清洗" seed="proj-data" :size="32" />
    </div>
    <SideNav />
    <main class="ot-pane">
      <ChannelCenter v-if="S.center === 'chan'" />
      <InboxCenter v-else-if="S.center === 'inbox'" />
      <FilesCenter v-else-if="S.center === 'files'" />
      <SettingsCenter v-else />
    </main>
    <div class="ot-right">
      <ThingPanel v-if="right === 'thing'" :key="S.thing ?? ''" />
      <FilesPanel v-else-if="right === 'files'" />
      <aside v-else-if="right === 'chan'" class="ot-chanthings" data-zone="right">
        <header class="ot-chanthings__head"># {{ chanTitle }} 的事</header>
        <div class="ot-chanthings__body">
          <template v-if="chanThings.length">
            <TaskCard
              v-for="t in chanThings"
              :key="t.id"
              :task="taskLine(t)"
              :owner-name="ownerName(t)"
              class="ot-chanthings__card"
              @open="openThing"
            />
          </template>
          <BaseEmptyState v-else size="compact" icon="mdi-checkbox-blank-outline" title="暂无事" />
          <p class="ot-chanthings__hint">点开一件事，它在右栏打开，频道留在中间。</p>
        </div>
      </aside>
    </div>

    <div v-if="notes.length" class="ot-notes" aria-hidden="true">
      <template v-for="n in notes" :key="n.key">
        <div
          class="ot-notes__zone"
          :style="{ left: `${n.x}px`, top: `${n.y}px`, width: `${n.w}px`, height: `${n.h}px` }"
        />
        <div class="ot-notes__note" :style="{ left: `${n.nx}px`, top: `${n.ny}px` }">
          <b>{{ n.title }}</b
          >{{ n.text }}
        </div>
      </template>
    </div>
    <Transition name="ot-toast">
      <div v-if="S.toast" :key="S.toastTick" class="ot-toast" role="status">{{ S.toast }}</div>
    </Transition>
    <slot />
  </div>
</template>

<style scoped>
.ot-app {
  display: grid;
  position: relative;
  width: 1280px;
  height: 800px;
  overflow: hidden;
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  transition: grid-template-columns var(--dur-slow) var(--ease-standard);
}

.ot-rail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  background: var(--canvas);
}

.ot-rail__on {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* Content area: one surface with a hairline against the canvas and a 12px corner at its top-left. */
.ot-pane {
  min-width: 0;
  min-height: 0;
  background: var(--surface);
  border-left: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
}

.ot-right {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
  border-left: 1px solid var(--line);
}

.ot-chanthings {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.ot-chanthings__head {
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 16px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
}

.ot-chanthings__body {
  flex: 1;
  padding: 16px;
  overflow: auto;
}

.ot-chanthings__card + .ot-chanthings__card {
  margin-top: 12px;
}

.ot-chanthings__hint {
  padding: 8px 12px;
  margin: 16px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
}

.ot-notes {
  position: absolute;
  inset: 0;
  z-index: var(--z-panel);
  pointer-events: none;
}

.ot-notes__zone {
  position: absolute;
  border: 2px dashed var(--ink);
  border-radius: var(--radius-md);
}

.ot-notes__note {
  position: absolute;
  max-width: 260px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--inverse-ink);
  background: var(--inverse-surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
}

.ot-notes__note b {
  display: block;
  margin-bottom: 2px;
}

.ot-toast {
  position: absolute;
  bottom: 84px;
  left: 50%;
  z-index: var(--z-shell);
  padding: 8px 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--inverse-ink);
  white-space: nowrap;
  pointer-events: none;
  background: var(--inverse-surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  transform: translateX(-50%);
}

.ot-toast-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}

.ot-toast-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.ot-toast-enter-from,
.ot-toast-leave-to {
  opacity: 0;
}
</style>
