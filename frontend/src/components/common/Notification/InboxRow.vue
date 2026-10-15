<template>
  <!-- One row of the 待办 page, shared by 等你处理 and 动态 so the two read alike.
       A face or a faint mark on the left, a title line and details in the middle, the
       time on the right. Unread is an amber dot and a bold title (design-system §1.6);
       a settled row's title drops to the secondary colour.
       Actions come in two kinds: the ones that always show (accept / decline) go in
       `side` beside the time; the generic ones (mark read, delete) go in `hover` and
       replace the time only under the pointer or keyboard focus. Touch has no hover,
       so those are left to the long-press menu (§10.6). -->
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- right-click / long-press only opens a menu of actions that are also reachable as buttons (focus shows them) -->
  <div
    class="inbox-row"
    :class="{ 'inbox-row--unread': unread, 'inbox-row--done': done, 'inbox-row--swaps': !!$slots.hover }"
    @contextmenu="emit('menu', $event)"
  >
    <span class="inbox-row__dot" aria-hidden="true" />
    <span class="inbox-row__lead"><slot name="lead" /></span>
    <NavLink v-if="to" :to="to" class="inbox-row__main">
      <span class="inbox-row__title"><slot name="title" /></span>
      <slot />
    </NavLink>
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- it takes role="link", a tab stop and Enter whenever it can open; a <button> cannot hold the block content the renderers draw -->
    <div
      v-else
      class="inbox-row__main"
      :class="{ 'inbox-row__main--opens': opens }"
      :role="opens ? 'link' : undefined"
      :tabindex="opens ? 0 : undefined"
      @click="opens && emit('open')"
      @keydown.enter="opens && emit('open')"
    >
      <span class="inbox-row__title"><slot name="title" /></span>
      <slot />
    </div>
    <div class="inbox-row__side">
      <slot name="side" />
      <time v-if="time" class="inbox-row__time t-meta-read" :datetime="at">{{ time }}</time>
      <span v-if="$slots.hover" class="inbox-row__hover"><slot name="hover" /></span>
    </div>
    <div v-if="$slots.below" class="inbox-row__below"><slot name="below" /></div>
  </div>
</template>

<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'

import NavLink from '@/components/common/NavLink.vue'

defineProps<{
  /** 点这一行去哪儿。给了就是一条真链接；没给而 `opens` 为真时，点了只发 `open`。 */
  to?: NavTarget | null
  /** 没有 `to` 时这一行能不能点（点了由使用方决定去哪儿）。 */
  opens?: boolean
  /** 右边写的时间，已经按这一块的口径写好（「2 小时前」或「14:20」）。 */
  time?: string
  /** 那个时间的原值，给 `<time datetime>`。 */
  at?: string
  unread?: boolean
  /** 这件事已经办完：标题退成次要色。 */
  done?: boolean
}>()

const emit = defineEmits<{
  open: []
  /** 右键或长按这一行：使用方弹它自己的那份操作菜单。 */
  menu: [event: MouseEvent]
}>()
</script>

<style scoped>
.inbox-row {
  display: grid;
  grid-template-columns: 6px 20px minmax(0, 1fr) auto;
  column-gap: 10px;
  align-items: start;
  padding: 10px 16px;
  border-top: 1px solid var(--line);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.inbox-row:hover {
  background: var(--fill);
}

.inbox-row__dot {
  width: 6px;
  height: 6px;
  margin-top: 7px;
  border-radius: var(--radius-pill);
}

.inbox-row--unread .inbox-row__dot {
  background: var(--accent);
}

.inbox-row__lead {
  display: flex;
  justify-content: center;
  padding-top: 2px;
  color: var(--faint);
}

.inbox-row__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  color: inherit;
  text-decoration: none;
}

.inbox-row__main--opens {
  cursor: pointer;
}

.inbox-row__title {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inbox-row--unread .inbox-row__title {
  font-weight: 600;
}

.inbox-row--done .inbox-row__title {
  color: var(--muted);
}

.inbox-row__side {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 20px;
}

.inbox-row__time {
  white-space: nowrap;
}

.inbox-row__hover {
  display: none;
  gap: 2px;
}

/* 有指针的设备上，通用操作换掉时间；触屏不画，走长按菜单。 */
@media (hover: hover) {
  .inbox-row--swaps:hover .inbox-row__time,
  .inbox-row--swaps:focus-within .inbox-row__time {
    display: none;
  }

  .inbox-row--swaps:hover .inbox-row__hover,
  .inbox-row--swaps:focus-within .inbox-row__hover {
    display: flex;
  }
}

.inbox-row__below {
  grid-column: 3 / -1;
  margin-top: 8px;
}

/* 窄屏上右边那格挪到正文下面，免得把标题挤成一列一个字。 */
@media (max-width: 599.98px) {
  .inbox-row {
    grid-template-columns: 6px 20px minmax(0, 1fr);
  }

  /* 挪下去的那格排在最后：拍板的选项紧跟着问题，时间落在最底下。 */
  .inbox-row__side {
    grid-column: 3;
    order: 1;
    margin-top: 6px;
  }
}
</style>
