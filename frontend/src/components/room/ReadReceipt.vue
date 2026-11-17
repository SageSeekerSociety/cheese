<script setup lang="ts">
// 自己那条消息谁读了（飞书式）：一个小圈。谁都没读是空圈，读了一部分是按比例填的一块，
// 全读了是实心带勾。点开列出已读和未读的人，人和 AI 队友一起，按名册的顺序。
//
// 「读了」是真读到：人是消息在前台的页面上露过面，AI 队友是消息进了它某一轮的输入
// （后端 `block/message_reads.py`）。这里只画：谁读了谁没读由房间算好传进来。
import type { Receipt } from '../../lib/readReceipts'

import { computed, ref } from 'vue'

import CheeseAvatar from '../CheeseAvatar.vue'
import UserAvatar from '../common/UserAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{ receipt: Receipt }>()

const open = ref(false)
const total = computed(() => props.receipt.read.length + props.receipt.unread.length)
const fraction = computed(() => (total.value ? props.receipt.read.length / total.value : 0))
const label = computed(() => {
  if (!props.receipt.unread.length) return t('work.room.readReceipt.all')
  if (props.receipt.read.length) return t('work.room.readReceipt.some', { count: props.receipt.read.length })
  return t('work.room.readReceipt.unread')
})
// 填的那一块：半径 3、线宽 6 的圆，描边从 12 点钟顺时针走 fraction 圈。
const PIE_R = 3
const PIE_C = 2 * Math.PI * PIE_R
const pieDash = computed(() => `${PIE_C * fraction.value} ${PIE_C}`)
const columns = computed(() => [
  { key: 'read', title: t('work.room.readReceipt.read'), people: props.receipt.read },
  { key: 'unread', title: t('work.room.readReceipt.unread'), people: props.receipt.unread },
])
</script>

<template>
  <span class="rc-anchor">
    <v-menu v-model="open" location="bottom end" :offset="4" :close-on-content-click="false">
      <template #activator="{ props: menu }">
        <button v-bind="menu" type="button" class="rc" :aria-label="label" :title="label" @click.stop>
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <template v-if="fraction === 1">
              <circle cx="8" cy="8" r="7" class="rc__full" />
              <path d="M4.6 8.2l2.2 2.2 4.6-4.6" class="rc__tick" />
            </template>
            <template v-else>
              <circle cx="8" cy="8" r="7" class="rc__ring" />
              <circle
                v-if="fraction > 0"
                cx="8"
                cy="8"
                :r="PIE_R"
                class="rc__pie"
                :stroke-width="PIE_R * 2"
                :stroke-dasharray="pieDash"
                transform="rotate(-90 8 8)"
              />
            </template>
          </svg>
        </button>
      </template>
      <div class="rpop" role="dialog" :aria-label="t('work.room.readReceipt.dialog')">
        <section v-for="col in columns" :key="col.key" class="rpop__col" :aria-label="col.title">
          <div class="rpop__h">
            {{ col.title }}<span class="rpop__n">{{ col.people.length }}</span>
          </div>
          <ul v-if="col.people.length" class="rpop__list">
            <li v-for="person in col.people" :key="person.handle" class="rpop__p">
              <CheeseAvatar v-if="person.isAgent" :size="20" :name="person.name" :handle="person.handle" />
              <UserAvatar v-else :avatar="person.avatar ?? ''" :name="person.name" :seed="person.handle" :size="20" />
              <span class="rpop__name">{{ person.name }}</span>
            </li>
          </ul>
          <div v-else class="rpop__none">{{ t('work.room.readReceipt.none') }}</div>
        </section>
      </div>
    </v-menu>
  </span>
</template>

<style scoped>
.rc-anchor {
  display: inline-flex;
}
.rc {
  flex: none;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rc:hover,
.rc[aria-expanded='true'] {
  background: var(--fill);
}
.rc__ring {
  fill: none;
  stroke: var(--faint);
  stroke-width: 1.5;
}
.rc__pie {
  fill: none;
  stroke: var(--muted);
}
.rc__full {
  fill: var(--ok);
}
.rc__tick {
  fill: none;
  stroke: var(--surface);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
/* The product's menu surface: --raised, --line-2, the large radius, --shadow-2. */
.rpop {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 260px;
  max-width: calc(100vw - 48px);
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.rpop__col {
  min-width: 0;
}
.rpop__h {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.rpop__n {
  font-family: var(--font-mono);
}
.rpop__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.rpop__p {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 3px 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
}
.rpop__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rpop__none {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
