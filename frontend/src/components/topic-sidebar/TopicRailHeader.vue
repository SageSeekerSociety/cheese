<script setup lang="ts">
// 项目名那一行：标识 + 那颗搜索 + 项目菜单。整页形态（手机上的话题列表）下这一行
// 不长在页面上，而是填进顶栏那一格（`#app-bar-slot`）：手机上只有一条顶栏，页面
// 自己再画一条就是两条横条一上一下写同类的东西。
//
// 名字连着那颗 ⌄ 是**一个**按钮，点下去弹出项目菜单（手机上是底部面板）：除了项目
// 名下那一行的看板和资料库，这个项目的其余几页、项目文档、项目设置、转让或退出都在
// 这里。点名字回看板会让人分不清点名字到底是什么（2026-10-05 用户定的），看板在
// 项目名下那一行里。
//
// 这里只画：这个项目叫什么、露出来了哪几页、转不转得动项目，都是父级算好递进来的。
import TopicRailBadge from './TopicRailBadge.vue'

import { t } from '@/i18n'

defineProps<{
  /** 整页形态（手机）：这一行填进顶栏，高度和底线归顶栏。 */
  page: boolean
  /** 两栏（平板）：留在左栏顶上，高度和底线照桌面那一条。 */
  column: boolean
  projectName: string
  /** 有人找你：私聊的未读总数（0 就不画）。 */
  privateUnreadTotal: number
  /** 搜索入口 hover 时说的话（桌面上写快捷键，手机上不写）。 */
  searchTitle: string
  /** 手机上的项目菜单开着没（那颗 ⌄ 的选中态）。 */
  menuOpen: boolean
  /** 菜单里的那几页：项目名下那一行之外的全部。 */
  menuPages: { key: string; label: string; icon: string }[]
  /** 项目文档开着没（菜单里那一项的选中态）。 */
  docsActive: boolean
  /** 当前页的名字，用来画菜单里的选中态。 */
  routeName: string | null
  /** 壳换了词之后的项目词汇表。 */
  terms: { project: string; topic: string }
  /** 还没选项目：菜单里那几项点不动。 */
  projectSelected: boolean
  canTransfer: boolean
  canLeave: boolean
}>()

const emit = defineEmits<{
  (e: 'open-page', key: string): void
  (e: 'open-palette'): void
  (e: 'open-sheet'): void
  (e: 'open-transfer'): void
  (e: 'open-leave'): void
  (e: 'select-docs'): void
}>()
</script>

<template>
  <Teleport to="#app-bar-slot" :disabled="!page || column">
    <div
      class="sidebar-header rail-header"
      :class="{ 'rail-header--bar': page && !column, 'rail-header--column': column }"
    >
      <!-- 手机：同一个入口从底部升起一张面板（见父级的 MobileActionSheet）。 -->
      <button
        v-if="page"
        type="button"
        class="rail-header__home tap-target"
        :class="{ 'rail-header__home--active': menuOpen }"
        :title="projectName"
        :aria-label="t('work.sidebar.projectMenu')"
        aria-haspopup="dialog"
        :aria-expanded="menuOpen ? 'true' : 'false'"
        :disabled="!projectSelected"
        @click="emit('open-sheet')"
      >
        <span class="rail-header__name" data-user-content>{{ projectName }}</span>
        <v-icon class="rail-header__caret" size="18" icon="mdi-chevron-down" />
      </button>
      <v-menu v-else location="bottom start">
        <template #activator="{ isActive, props: menuProps }">
          <!-- 名字是省略号截断的，title 留着全名。 -->
          <button
            v-bind="menuProps"
            type="button"
            class="rail-header__home"
            :class="{ 'rail-header__home--active': isActive }"
            :title="projectName"
            :aria-label="t('work.sidebar.projectMenu')"
            :disabled="!projectSelected"
          >
            <span class="rail-header__name" data-user-content>{{ projectName }}</span>
            <v-icon class="rail-header__caret" size="18" icon="mdi-chevron-down" />
          </button>
        </template>
        <v-list density="compact" nav max-height="60vh">
          <v-list-item
            prepend-icon="mdi-file-document-outline"
            :title="t('navigation.project.docs')"
            :active="docsActive"
            @click="emit('select-docs')"
          />
          <v-list-item
            v-for="p in menuPages"
            :key="p.key"
            :prepend-icon="p.icon"
            :title="t(p.label, terms)"
            :active="routeName === p.key"
            @click="emit('open-page', p.key)"
          >
            <!-- 有人找你：私聊的未读挂在「成员」上。 -->
            <template v-if="p.key === 'project-members' && privateUnreadTotal > 0" #append>
              <TopicRailBadge :count="privateUnreadTotal" />
            </template>
          </v-list-item>
          <v-divider class="my-1" />
          <v-list-item
            prepend-icon="mdi-cog-outline"
            :title="t('work.projectSettings.title')"
            :active="routeName === 'project-settings'"
            @click="emit('open-page', 'project-settings')"
          />
          <!-- 只有转得动的人看得见：必然被拒的按钮比不给更糟。 -->
          <v-list-item
            v-if="canTransfer"
            prepend-icon="mdi-account-arrow-right-outline"
            :title="t('work.members.transfer')"
            @click="emit('open-transfer')"
          />
          <!-- 另一半：我不是所有者时换「退出项目」。所有者退不掉，只能先把项目交出去。 -->
          <v-list-item
            v-if="canLeave"
            prepend-icon="mdi-exit-to-app"
            :title="t('work.members.leave')"
            @click="emit('open-leave')"
          />
        </v-list>
      </v-menu>
      <!-- 有人找你：私聊的未读。它是主导航上唯一会亮的「有人在等你回话」，跟着菜单
           入口走（菜单里「成员」那一项上也有）。 -->
      <TopicRailBadge v-if="privateUnreadTotal > 0" class="me-1" :count="privateUnreadTotal" />
      <!-- 命令面板的入口。桌面上 ⌘K / Ctrl K 也能叫出来，快捷键写在 title 里，不常驻
           界面；手机上没有键盘快捷键，这颗就是唯一的入口。 -->
      <button
        type="button"
        class="rail-header__more"
        :class="{ 'tap-target': page }"
        :title="searchTitle"
        :aria-label="t('navigation.palette.open')"
        aria-haspopup="dialog"
        @click="emit('open-palette')"
      >
        <v-icon class="rail-header__caret" size="18" icon="mdi-magnify" />
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
/* 项目头：高度和内边距来自全局 .sidebar-header（48px 基线），三条标题线才落在
   同一水平上。
 *
 * 底部那条分隔线必须在这里再声明一遍，不能指望全局 .sidebar-header 那条。
 * 原因是 style.css 的 `button:not(.v-btn) { border: none }`——那条选择器权重是
 * (0,1,1)，压过 .sidebar-header 的 (0,1,0)，而这条 rail 是四个侧栏里唯一把
 * .sidebar-header 放在 <button> 上的（首页/空间/设置都是 <div>），所以**只有
 * 工作台**这条线被抹掉了，其余三个照常显示。那条 reset 自己的注释也写明了这个
 * 约定：「buttons that declare their own border override this」。
 *
 * 颜色读 --app-page-header-rule，和 .sidebar-header / PageHeader 是同一个值：目标
 * 是和右边内容区顶栏那条线同款同高，能接成一条。
 * 删掉这一行，线就会静默消失，而且沙箱里跑不了渲染、任何测试都抓不到。 */
.rail-header {
  width: 100%;
  /* 它必须退出收缩：下面那段 .rail-scroll 的 flex-basis 是 auto = 那一长列话题
     的内容高度，几十个话题就足以把整列撑得比侧栏高。弹性盒于是按各自 basis 分摊
     收缩量，这一条虽只有 48px 也照分，一路被压到自己的最小内容高度（8+8 内边距
     + 一行字 ≈ 38px）为止——右边内容区顶栏钉死在 48px，两条分隔线就再也接不上。
     下面那段自己有 overflow-y:auto，min-height 解析为 0，该吸收收缩量的本来就
     是它。 */
  flex: none;
  border: 0;
  border-block-end: var(--app-page-header-rule);
  background: none;
  font: inherit;
  color: inherit;
  text-align: start;
  cursor: pointer;
}
/* 填进顶栏的那一份不画自己的高度和底线——那两样归顶栏。 */
.rail-header--bar {
  height: 100%;
  border-block-end: 0;
  padding-inline: 0;
}
/* 顶栏里左边紧挨着 ←：往左探的那 4px 会压到 ← 能点的那一块上。两颗按钮都比手指
   小，能点的范围由 .tap-target 撑到 44（相对定位给它用）。 */
.rail-header--bar .rail-header__home {
  position: relative;
  margin-inline-start: 0;
}
/* 顶栏里搜索那颗本身就是 44 见方，不靠 .tap-target 往外撑：撑出来的范围会压到
   左边的项目名上。 */
.rail-header--bar .rail-header__more {
  position: relative;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
}
/* 两栏（平板）时这一行留在左栏顶上，高度和底线照桌面那一条（手机外壳里是 56，和
   右边的顶栏接成一条线）。两颗按钮一样由 .tap-target 撑到 44，所以一样要有定位。 */
.rail-header--column .rail-header__home,
.rail-header--column .rail-header__more {
  position: relative;
}
/* 这一条里现在有两个按钮，所以描边长在按钮上，不长在整条上。 */
.rail-header__home,
.rail-header__more {
  border: 0;
  background: none;
  font: inherit;
  color: inherit;
  cursor: pointer;
  border-radius: var(--radius-sm);
}
/* 名字的左缘落在下面各行的图标列上（离侧栏左缘 16px），底色的左缘落在各行底色的
   左缘上（8px）。按钮只有名字连 ⌄ 那么宽，后面留空：右边那颗搜索不算进名字里。 */
.rail-header__home {
  flex: 0 1 auto;
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  margin-inline-end: auto;
  text-align: start;
  padding: 4px 8px;
  margin-inline-start: -4px;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
/* 菜单开着：底色和悬停同一档。 */
.rail-header__home--active {
  background: var(--fill);
}
.rail-header__more {
  flex: none;
  display: inline-flex;
  align-items: center;
  padding: 4px;
}
.rail-header__home:hover,
.rail-header__more:hover,
.rail-header__more--active {
  background: var(--fill);
}
.rail-header__home:focus-visible,
.rail-header__more:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.rail-header__caret {
  flex: none;
  color: var(--muted);
}
.rail-header__name {
  min-width: 0;
  /* 15/600 = .t-title，和话题头、手机顶栏同一号：这三条横条在屏幕上接着。 */
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
