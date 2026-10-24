<script setup lang="ts">
// 技能面板：带什么、还能从哪儿加。黄底一块灯板，一颗一颗「洞」按下去就亮。
//
// 技能名来自队友装备清单；灯亮表示当前可用，灯灭表示不可用或状态未知。
//
// 只管画、只往上发：池子、机器索引、落库都在 `useAgentSkills` 里。桌面上是居中
// 弹窗，手机上是一整页 —— 外壳由 AdaptiveDialog 决定，这一块在两边都是同一张灯板。
//
// 文案还在 `work.room.skills.*` 命名空间下（重命名命名空间不在这次改动范围内），
// 虽然它现在是从项目设置里打开的。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import SkillMarket from '@/components/skills/SkillMarket.vue'
import { t } from '@/i18n'
import { skillIcon } from '@/lib/skillIcons'

// 只声明它真画的那几个字段，不从 API 层连类型一起搬进来：组件按规矩不认识 API 层
// （scripts/import-boundary-ratchet-core.mjs），而父组件传下来的是结构相同的对象。
interface SkillItem {
  name: string
  description: string
  source: 'platform' | 'machine' | 'project'
}
interface SkillHealthItem {
  status: 'ok' | 'degraded' | 'unavailable'
  detail: string
}
// 市场的一张卡片与整份清单。和 API 层结构相同，但按组件规矩不搬那边的类型。
interface MarketCardItem {
  name: string
  description: string
  url: string
  source: string
  kind: 'skill' | 'plugin' | 'mcp'
}
interface MarketItem {
  skills: MarketCardItem[]
  plugins: MarketCardItem[]
  plugin_total: number
  mcp: MarketCardItem[]
  notes: string[]
}
const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    agentName: string
    carried: SkillItem[]
    available: SkillItem[]
    health: Record<string, SkillHealthItem>
    deviceId: string | null
    market?: MarketItem | null
    marketLoading?: boolean
    /** 别人正占着这个队友的「编辑中」租约：这一轮面板只读。 */
    editingBlocked?: boolean
    /** 占着租约的那个人的 handle，画在人看的那一句里。 */
    editingBy?: string | null
    loading?: boolean
    scanning?: boolean
    saving?: boolean
    canManage?: boolean
  }>(),
  {
    market: null,
    marketLoading: false,
    editingBlocked: false,
    editingBy: null,
    loading: false,
    scanning: false,
    saving: false,
    canManage: true,
  }
)

const emit = defineEmits<{
  (e: 'install', payload: { url: string; kind: 'skill' | 'plugin' }): void
}>()

const status = (name: string): SkillHealthItem['status'] | 'unknown' => props.health[name]?.status ?? 'unknown'
const availableNow = (skill: SkillItem) => (skill.source === 'platform' ? status(skill.name) === 'ok' : true)

const boardSkills = computed(() => {
  const seen = new Set<string>()
  return [...props.carried, ...props.available].filter((skill) => {
    if (seen.has(skill.name)) return false
    seen.add(skill.name)
    return true
  })
})
// 市场停在哪个页签（页签照 Claude Desktop 的 Discover：技能 / 插件 / MCP，外加第四个
// 货架「Agent 模板」）。市场那段画面在共享组件 SkillMarket 里，这里只留这份状态，好在
// 每次打开面板时把它复位。
type MarketTab = 'skills' | 'plugins' | 'mcp' | 'agents'
const marketTab = ref<MarketTab>('skills')
watch(open, () => {
  marketTab.value = 'skills'
})

// 改队友的 userskill 项目里每个人都做得的（后端 `projects_agents.py` 不再要 manager）；
// 别人正占着「编辑中」的租约时改不动 —— 那是同一件事的另一半：现在改会撞上他。
const locked = () => !props.canManage || props.editingBlocked
</script>

<template>
  <AdaptiveDialog v-model="open" :title="t('work.room.skills.title')" max-width="560">
    <div class="skills">
      <p class="skills__hint t-meta-read">{{ t('work.room.skills.hint') }}</p>

      <div class="board">
        <div class="board__head">
          <span class="board__title t-eyebrow">{{ t('work.room.skills.carriedTitle', { name: agentName }) }}</span>
        </div>
        <p v-if="loading" class="skills__note t-meta-read">{{ t('work.room.skills.loading') }}</p>
        <template v-else>
          <!-- 别人正占着「编辑中」时先说这一句：那比「你不是管理者」更贴近此刻的原因。 -->
          <p v-if="editingBlocked" class="skills__note t-meta-read">
            {{
              editingBy ? t('work.room.skills.editingBy', { name: editingBy }) : t('work.room.skills.editingBlocked')
            }}
          </p>
          <p v-else-if="locked()" class="skills__note t-meta-read">{{ t('work.room.skills.managerOnly') }}</p>
          <div class="skills__grid">
            <div
              v-for="s in boardSkills"
              :key="s.name"
              class="socket"
              :class="{ 'socket--on': availableNow(s) }"
              :title="s.description || s.name"
            >
              <span class="socket__hole"
                ><v-icon size="20">{{ skillIcon(s.name) }}</v-icon></span
              >
              <span class="socket__name t-meta-read">{{ s.name }}</span>
              <span class="socket__desc t-meta-read">{{ s.description }}</span>
            </div>
          </div>
          <p v-if="!boardSkills.length" class="skills__note t-meta-read">{{ t('work.room.skills.emptyCarried') }}</p>
        </template>
      </div>
    </div>

    <!-- 技能市场：琥珀块**下面**另一块，抽成共享组件 SkillMarket（全局市场页画同一份）。
         弹窗只把目录、机器和设备往下传，收到的意图原样往上发。 -->
    <SkillMarket
      v-model:tab="marketTab"
      :market="market"
      :loading="marketLoading"
      :device-id="deviceId"
      :scanning="scanning"
      :saving="saving"
      @install="emit('install', $event)"
    />
  </AdaptiveDialog>
</template>

<style scoped>
/* 黄底：整块面板是一张灯板，底色用 warn 的 wash、字用 warn 的 ink。两种主题下这套
   记号的意思不变（浅色是浅黄底深黄字，深色是深棕底浅黄字），所以不写死颜色。 */
.skills {
  padding: 14px;
  background: var(--warn-wash);
  border-radius: var(--radius-lg);
}
/* 黄板上的字一律是 warn 三件套里的 ink：底色是 --warn-wash，文字色就配 --warn-ink
   （frontend.md「状态色成三件，不可互换」）。下面每个元素挂的是排版阶梯类，阶梯类
   各自带颜色，在这一块里让位；字号、行距、字距照收。 */
.skills .t-eyebrow,
.skills .t-meta,
.skills .t-meta-read,
.skills .t-body {
  color: var(--warn-ink);
}
.skills__hint {
  margin: 0 0 12px;
}
.skills__note {
  margin: 0 0 8px;
}
.board {
  border-top: 1px dashed color-mix(in srgb, var(--warn-ink) 30%, transparent);
  padding-top: 12px;
}
.board__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}
/* 洞的数量跟着技能走：窄了自动换行，不写死几行几个。 */
.skills__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
  gap: 10px;
}
.socket {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 12px 6px 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
}
/* 那颗「洞」：底色跟浮层自己的背景走（`--surface`，对话框里重映射成 `--raised`，
   浅色即 #fff），不再用 `--code-bg` 那口近黑的井 —— 两套主题下它都从底上「长」出来，
   而不是嵌一颗黑/灰圆片。占位的「＋」和点亮的技能共用这一颗井，空位不另画底色。
   没点亮时图标是灰的，点亮转琥珀、外面再围一圈琥珀；动的是图标和那圈光，井本身不动，
   一口气点亮好几颗时整块板都不会跳。 */
.socket__hole {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--surface);
  color: var(--faint);
  transition:
    color var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard);
}
.socket--on .socket__hole {
  color: var(--warn);
  box-shadow:
    inset 0 0 0 2px var(--warn),
    0 0 10px var(--warn);
}
.socket__name {
  text-align: center;
  overflow-wrap: anywhere;
}
/* 描述是判断「它会不会在该触发时触发」的依据（skill-creator 的规矩：description
   才是唯一的触发机制），只给名字判断不了。截两行，hover 出全文。 */
.socket__desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-align: center;
  opacity: 0.7;
}
</style>
