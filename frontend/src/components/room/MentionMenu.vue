<script setup lang="ts">
// `@` 候选菜单，浮在输入框上方。
//
// 从 `RoomComposer.vue` 里拆出来的（#2143）：它只吃 props、只往上发事件，所以能
// 单独摆在预览站里。候选是什么、高亮哪一项、在第几级，全在外面算好——这里不认识
// 名册，也不认识资料库。
//
// 它**不传送到 body**，就是一个 `position: absolute` 的 div，定位的参照是输入区
// 那个 `position: relative` 的盒子。所以它自己那套样式写在下面就够了，不需要谁从
// 外面 `:deep` 进来（浮层要传送的才需要）。
import type { MentionItem } from '@/composables/useRoomMentionPicker'

import { nextTick, ref } from 'vue'

import CheeseAvatar from '../CheeseAvatar.vue'
import ExternalTag from '../common/ExternalTag.vue'
import UserAvatar from '../common/UserAvatar.vue'

import { t } from '@/i18n'

defineProps<{
  /** 菜单是不是该露出来。Esc 收起之后它是 false（`@` 还留在正文里）。
   *  一条候选都没有时它仍是 true——那时候菜单画的是一句「暂无匹配」。 */
  open: boolean
  /** 候选。空数组不是「什么都没发生」：菜单会画空态那句话。 */
  matches: MentionItem[]
  /** 高亮的是第几项。鼠标划过和 ↑/↓ 改的是同一个值。 */
  activeIndex: number
  /** 进来翻资料库了没有：二级菜单的头和往左让开的动画都看它。 */
  level: 'root' | 'library'
  /** 触摸屏上回车是换行，那条「Enter 挑这一项」的提示就不该出现。 */
  enterSends: boolean
  /** 贴在这个位置（文档里的光标处），而不是浮在输入框上方。 */
  at?: { top: number; left: number; width?: number } | null
}>()

const emit = defineEmits<{
  (e: 'pick', item: MentionItem): void
  (e: 'hover', index: number): void
  /** 从资料库退回一级（二级菜单头上那颗 ‹）。 */
  (e: 'back'): void
}>()

const menuEl = ref<HTMLElement | null>(null)

// 菜单自己有高度上限、自己滚（见 .mention-menu）：不把高亮项滚进视野，按 ↓ 走到
// 底之后高亮就跑到看不见的地方去了——那时键盘看起来又坏了。
//
// 只由 ↑/↓ 触发（输入区在换高亮之后叫这一下）；鼠标划过不需要滚，指针指着的
// 那一项本来就在眼前。
async function scrollActiveIntoView() {
  await nextTick()
  menuEl.value?.querySelector('.mention-menu-item.is-active')?.scrollIntoView?.({ block: 'nearest' })
}

defineExpose({ scrollActiveIntoView })
</script>

<template>
  <Transition name="menu-rise">
    <div
      v-if="open"
      ref="menuEl"
      class="mention-menu"
      :class="{ 'mention-menu--at': at }"
      :style="
        at ? { top: `${at.top}px`, left: `${at.left}px`, width: at.width ? `${at.width}px` : undefined } : undefined
      "
    >
      <!-- 进资料库是往里走一层：这一层往左让开，下一层从右边进来；退回来反过来。 -->
      <Transition :name="level === 'library' ? 'level-in' : 'level-out'" mode="out-in">
        <div :key="level" class="mention-menu-level">
          <!-- 整个头就是「退回一级」那颗按钮：进来了就得有路回去。键盘上是 Esc、←，
               或者 @ 后面没打字时的退格（RoomComposer 的 onComposerKey）。 -->
          <button
            v-if="level === 'library'"
            type="button"
            class="mention-menu-head"
            :aria-label="t('work.room.mention.back')"
            :title="t('work.room.mention.back')"
            @click="emit('back')"
          >
            <v-icon size="16">mdi-chevron-left</v-icon>
            <span class="mention-menu-name">{{ t('work.room.mention.library') }}</span>
          </button>
          <template v-for="(mm, i) in matches" :key="mm.kind + mm.insert">
            <div v-if="mm.group && mm.group !== matches[i - 1]?.group" class="mention-menu-group">
              {{ mm.group }}
            </div>
            <button
              type="button"
              class="mention-menu-item"
              :class="{ 'is-active': i === activeIndex }"
              @click="emit('pick', mm)"
              @mouseenter="emit('hover', i)"
            >
              <span v-if="mm.kind === 'broadcast'" class="mention-avatar mention-avatar--broadcast">
                <v-icon size="13">mdi-bullhorn-outline</v-icon>
              </span>
              <CheeseAvatar
                v-else-if="mm.kind === 'member' && mm.agent"
                :size="22"
                :name="mm.label"
                :handle="mm.handle"
              />
              <UserAvatar
                v-else-if="mm.kind === 'member'"
                :size="22"
                :name="mm.label"
                :seed="mm.handle"
                :avatar="mm.avatar ?? ''"
              />
              <span v-else-if="mm.kind === 'category'" class="mention-avatar mention-avatar--file">
                <v-icon size="13">mdi-folder-outline</v-icon>
              </span>
              <span v-else-if="mm.kind === 'file'" class="mention-avatar mention-avatar--file">
                <v-icon size="13">mdi-file-outline</v-icon>
              </span>
              <span v-else class="mention-avatar mention-avatar--topic">
                <v-icon size="13">mdi-pound</v-icon>
              </span>
              <span class="mention-menu-name">{{ mm.label }}</span>
              <span v-if="mm.agent" class="mention-agent-badge">{{ t('work.room.roster.agentBadge') }}</span>
              <ExternalTag v-else-if="mm.external" />
              <span class="mention-menu-sub">{{ mm.sub }}</span>
              <span v-if="mm.kind === 'category'" class="mention-menu-hint">›</span>
              <span v-else-if="i === activeIndex && enterSends" class="mention-menu-hint">Enter</span>
            </button>
          </template>
          <!-- No candidates at all: the menu does not disappear, it just says so.
               Collapsing the whole box reads as if that @ did nothing. The library
               level speaks of files; the root searches people, topics and files
               together, so it says something broader. -->
          <div v-if="!matches.length" class="mention-menu-empty">
            {{ t(level === 'library' ? 'work.room.mention.noFiles' : 'work.room.mention.noMatch') }}
          </div>
        </div>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
/* @-autocomplete popup — mirrors TopicView's composer picker. */
/* 浮在输入区上方，不占位置。它原来是输入区里的一个普通块：菜单一出现输入区就长高，
   贴在输入框上面的验收横条被整条顶上去，菜单一收又掉回来。浮层本来就该带投影、
   盖在别的东西上面。 */
.mention-menu {
  position: absolute;
  right: 12px;
  bottom: 100%;
  left: 12px;
  z-index: var(--z-raised-5);
  display: flex;
  flex-direction: column;
  margin-bottom: 4px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  /* 横向仍旧裁边（圆角靠它），纵向改自滚：规范只在某一侧是 `visible` 时才把另一
     侧算成 `auto`，所以这两条不冲突。 */
  overflow-x: hidden;
  overflow-y: auto;
  /* 菜单最多 7 项（`mentionMatches` 里 slice(0, 7)），每项 min-height 36px，展开
     就是 254px；而 `.composer` 是 `.chat`（flex column）里不肯收缩的那一项。面板
     一矮（尤其手机上），多出来的部分连同输入框一起从 `.chat` 底部溢出、被外壳裁
     掉，还没有滚动条。给个上限让它自己滚，量的是看得见的那一截：手机上键盘弹起来
     时 40vh 还是按整屏算，菜单会伸到顶栏底下（--app-height / --keyboard-inset 见
     lib/keyboardInset.ts）。 */
  max-height: calc((var(--app-height, 100dvh) - var(--keyboard-inset, 0px)) * 0.4);
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.mention-menu--at {
  right: auto;
  bottom: auto;
  width: 280px;
  margin-bottom: 0;
  max-height: 264px;
}
.mention-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  min-height: 36px;
  border-radius: var(--radius-md);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
/* 高亮只有一套：鼠标划过（`@mouseenter` 把下标挪过去）和 ↑/↓ 改的是同一个下标。
   两边各画一次的话，指针停在第三行、键盘走到第四行时屏幕上会同时亮着两行，
   而回车只会挑其中一行。 */
.mention-menu-item.is-active {
  background: var(--fill);
}
.mention-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  /* 「所有人」、话题、文件这些不是人的是圆角方块（下面各自改回）。人和 AI 队友不走
     这一个 span：人画 UserAvatar（真图，没有就按 handle 取色的彩色首字母），队友画
     CheeseAvatar。 */
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 600;
  /* 群播那颗图标坐在 --ink 上，两套主题下都不变，所以上面的字也得是定值。
     design-system §唯一的例外：这种地方必须写明理由，否则下一个人会顺手换成 token
     （深色下就变成浅灰压浅底）。 */
  /* stylelint-disable-next-line color-no-hex -- 压在头像底色上的墨色，底色不随主题变。 */
  color: #fff;
  flex: none;
}
.mention-avatar--broadcast {
  /* --ink inverts with the theme (near-black → near-white), so the ink on it
     has to invert too; --surface is #fff in light (unchanged) and #1B1D20 dark. */
  color: var(--surface);
  background: var(--ink);
}
.mention-avatar--broadcast,
.mention-avatar--topic,
.mention-avatar--file {
  border-radius: var(--radius-sm);
}
.mention-avatar--topic,
.mention-avatar--file {
  background: var(--fill);
  color: var(--muted);
}
.menu-rise-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.menu-rise-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.menu-rise-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.menu-rise-leave-to {
  opacity: 0;
}
.mention-menu-level {
  display: flex;
  flex-direction: column;
}
.level-in-enter-active,
.level-out-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.level-in-leave-active,
.level-out-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.level-in-enter-from,
.level-out-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
.level-in-leave-to,
.level-out-enter-from {
  opacity: 0;
  transform: translateX(-16px);
}
/* 二级菜单的头是「退回一级」那颗按钮，不是一个候选：它不参与 ↑/↓ 的高亮，
   只在指针划过时变底色。 */
.mention-menu-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 12px 7px 8px;
  min-height: 36px;
  border-bottom: 1px solid var(--line-2);
  color: var(--muted);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.mention-menu-head:hover {
  background: var(--fill);
}
.mention-menu-group {
  padding: 6px 12px 2px;
  font-size: 12px;
  color: var(--faint);
}
/* 一条候选都没有。它顶替的是一整列候选行，所以内边距和字号跟着候选行走（13px、
   上下 10px），而不是跟着组标题走——不然空态看起来像一个没写完的小标题。 */
.mention-menu-empty {
  padding: 10px 12px;
  font-size: 13px;
  color: var(--faint);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--fill);
}
.mention-menu-sub {
  font-size: 12px;
  color: var(--faint);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--faint);
}
/* 触屏上手指点得中（设计系统 §10.1）：行画出来的样子不变，能点的范围撑到 44px 高。 */
@media (pointer: coarse) {
  .mention-menu-item,
  .mention-menu-head {
    min-height: 44px;
  }
}
</style>
