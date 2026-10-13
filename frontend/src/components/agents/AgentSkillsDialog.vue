<script setup lang="ts">
// 技能面板：带什么、还能从哪儿加。黄底一块灯板，一颗一颗「洞」按下去就亮。
//
// 亮/灭 = 带不带（人的选择，存在队友身上）；右上角小圆点 = 现在能不能跑（后端探的，
// 只探平台那几颗）。两件事分开说，所以灭了也可以有绿点。
//
// 只管画、只往上发：池子、机器索引、落库都在 `useAgentSkills` 里。桌面上是居中
// 弹窗，手机上是一整页 —— 外壳由 AdaptiveDialog 决定，这一块在两边都是同一张灯板。
//
// 文案还在 `work.room.skills.*` 命名空间下（重命名命名空间不在这次改动范围内），
// 虽然它现在是从项目设置里打开的。
import { ref, watch } from 'vue'

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
// 落盘前给人看的那份清单：一颗技能，和会跟着写进去的文件名（含 SKILL.md）。
interface InstallPlanItem {
  name: string
  description: string
  files: string[]
}
interface PendingInstall {
  url: string
  kind: 'skill' | 'plugin'
  plan: InstallPlanItem[]
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
    /** 读回来、检验过、还没落盘的网上下载。非空时弹确认框。 */
    pending?: PendingInstall | null
    /** 别人正占着这个队友的「编辑中」租约：这一轮面板只读。 */
    editingBlocked?: boolean
    /** 占着租约的那个人的 handle，画在人看的那一句里。 */
    editingBy?: string | null
    max: number
    loading?: boolean
    scanning?: boolean
    saving?: boolean
    canManage?: boolean
  }>(),
  {
    market: null,
    marketLoading: false,
    pending: null,
    editingBlocked: false,
    editingBy: null,
    loading: false,
    scanning: false,
    saving: false,
    canManage: true,
  }
)

const emit = defineEmits<{
  (e: 'add', name: string): void
  (e: 'remove', name: string): void
  (e: 'fetchWeb', url: string): void
  (e: 'install', payload: { url: string; kind: 'skill' | 'plugin' }): void
  (e: 'confirmInstall'): void
  (e: 'cancelInstall'): void
}>()

// 健康圆点说的话。四个状态加上「还没探到」这一种。
const HEALTH: Record<SkillHealthItem['status'] | 'unknown', string> = {
  ok: 'work.room.skills.healthOk',
  degraded: 'work.room.skills.healthDegraded',
  unavailable: 'work.room.skills.healthUnavailable',
  unknown: 'work.room.skills.healthUnknown',
}
const status = (name: string): SkillHealthItem['status'] | 'unknown' => props.health[name]?.status ?? 'unknown'
const healthLabel = (name: string) => t(HEALTH[status(name)])

// 「+ 添加」是一格，按下去在下面展开池子。关掉再开就收起来：还没加完的那张列表
// 留着，会让人以为已经加上了。
const adding = ref(false)
// 「互联网直连」那一格的地址。关掉再开就清掉没交出去的那一段。
const newWebUrl = ref('')
// 市场停在哪个页签（页签照 Claude Desktop 的 Discover：技能 / 插件 / MCP，外加第四个
// 货架「Agent 模板」）。市场那段画面在共享组件 SkillMarket 里，这里只留这份状态，好在
// 每次打开面板时把它复位。
type MarketTab = 'skills' | 'plugins' | 'mcp' | 'agents'
const marketTab = ref<MarketTab>('skills')
watch(open, () => {
  adding.value = false
  newWebUrl.value = ''
  marketTab.value = 'skills'
})

// 改队友的 userskill 要 manager（后端 `projects_agents.py`）；别人正占着「编辑中」
// 的租约时也改不动 —— 那是同一件事的另一半：现在改会撞上他。
const locked = () => !props.canManage || props.editingBlocked

/** 从网上取一份技能：地址发上去，取回、合规检验、落盘都在别处。 */
function addWebSkill() {
  const url = newWebUrl.value.trim()
  if (!url) return
  newWebUrl.value = ''
  emit('fetchWeb', url)
}
</script>

<template>
  <AdaptiveDialog v-model="open" :title="t('work.room.skills.title')" max-width="560">
    <div class="skills">
      <p class="skills__hint t-meta-read">{{ t('work.room.skills.hint') }}</p>

      <div class="board">
        <div class="board__head">
          <span class="board__title t-eyebrow">{{ t('work.room.skills.carriedTitle', { name: agentName }) }}</span>
          <span class="board__cap t-meta-read t-num">{{ t('work.room.skills.cap', { max }) }}</span>
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
            <button
              v-for="s in carried"
              :key="s.name"
              type="button"
              class="socket socket--on"
              :disabled="locked() || saving"
              :title="s.description || s.name"
              @click="emit('remove', s.name)"
            >
              <span class="socket__hole"
                ><v-icon size="20">{{ skillIcon(s.name) }}</v-icon></span
              >
              <span class="socket__name t-meta-read">{{ s.name }}</span>
              <span class="socket__desc t-meta-read">{{ s.description }}</span>
              <span class="socket__dot" :class="`socket__dot--${status(s.name)}`" :title="healthLabel(s.name)" />
            </button>
            <button
              type="button"
              class="socket socket--add"
              :disabled="locked() || saving"
              :aria-expanded="adding"
              @click="adding = !adding"
            >
              <span class="socket__hole"><v-icon size="20">mdi-plus</v-icon></span>
              <span class="socket__name t-meta-read">{{ t('work.room.skills.add') }}</span>
            </button>
          </div>
          <p v-if="!carried.length" class="skills__note t-meta-read">{{ t('work.room.skills.emptyCarried') }}</p>

          <div v-if="adding" class="pool">
            <p v-if="!available.length" class="skills__note t-meta-read">{{ t('work.room.skills.empty') }}</p>
            <button
              v-for="s in available"
              :key="s.name"
              type="button"
              class="pool__row"
              :disabled="saving"
              @click="emit('add', s.name)"
            >
              <v-icon size="18">{{ skillIcon(s.name) }}</v-icon>
              <span class="pool__body">
                <span class="pool__name t-body">{{ s.name }}</span>
                <span class="pool__desc t-meta-read">{{ s.description }}</span>
              </span>
              <span class="pool__from t-meta-read">{{ t(`work.room.skills.from.${s.source}`) }}</span>
            </button>

            <!-- 机器那一段（扫描、扫到的技能、工作区根登记）暂时不在这块板上摆：
                 扫描/采纳的代码仍在（`useAgentSkills` + 后端 `machine_skills`），这里只
                 不展示。留下的这一格是「互联网直连」——贴一个地址取一份技能到机器上，
                 取回时按 agentskills.io 规范检验，过了才落盘。 -->
            <div class="machine">
              <p v-if="!deviceId" class="skills__note t-meta-read">{{ t('work.room.skills.noMachine') }}</p>
              <div v-else class="roots">
                <p class="skills__note t-meta-read">{{ t('work.room.skills.webHint') }}</p>
                <div class="roots__add">
                  <input
                    v-model="newWebUrl"
                    class="roots__input t-meta-read"
                    type="text"
                    autocomplete="off"
                    :placeholder="t('work.room.skills.webPlaceholder')"
                    :disabled="scanning"
                    @keyup.enter="addWebSkill"
                  />
                  <button
                    type="button"
                    class="machine__scan"
                    :disabled="scanning || !newWebUrl.trim()"
                    @click="addWebSkill"
                  >
                    {{ t('work.room.skills.webAdd') }}
                  </button>
                </div>
              </div>
            </div>
          </div>
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
      :pending="pending"
      @install="emit('install', $event)"
      @confirm-install="emit('confirmInstall')"
      @cancel-install="emit('cancelInstall')"
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
.board__cap {
  opacity: 0.75;
}
/* 洞的数量跟着技能走：窄了自动换行，不写死几行几个。 */
.skills__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
  gap: 10px;
}
.socket {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 12px 6px 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
}
.socket--add {
  border: 1px dashed color-mix(in srgb, var(--warn-ink) 40%, transparent);
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
/* 健康：右上角一颗圆点。绿=能跑、黄=受限、红=不能跑、灰=还没探到。 */
.socket__dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
}
.socket__dot--ok {
  background: var(--ok);
}
.socket__dot--degraded {
  background: var(--warn);
}
.socket__dot--unavailable {
  background: var(--danger);
}
.socket__dot--unknown {
  background: var(--faint);
}
.pool {
  margin-top: 12px;
  border-top: 1px dashed color-mix(in srgb, var(--warn-ink) 30%, transparent);
  padding-top: 10px;
}
.pool__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--warn-ink);
  text-align: left;
  cursor: pointer;
}
.pool__row:hover:not(:disabled) {
  background: color-mix(in srgb, var(--warn-ink) 8%, transparent);
}
.pool__row:disabled {
  cursor: default;
  opacity: 0.6;
}
.pool__body {
  flex: 1;
  min-width: 0;
}
.pool__name {
  display: block;
}
.pool__desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  opacity: 0.75;
}
.pool__from {
  flex: none;
  opacity: 0.75;
}
/* 「互联网直连」那一格不是「灯板」：它是本机自己的事，单独落在中性底上，不跟着
   上面的琥珀走。 */
.machine {
  margin-top: 10px;
  padding: 8px;
  background: var(--surface);
  border-radius: var(--radius-md);
}
.machine__scan {
  border: 0;
  background: transparent;
  color: var(--warn-ink);
  /* 编辑窗里同一类控件（那个「高级设置」开关）手写的就是 13px，这里照抄同一个值：
     阶梯上 13px 那一档（.t-dense）是 600 重的读数，安静按钮跟它不对路。 */
  font-size: 13px;
  text-decoration: underline;
  cursor: pointer;
}
/* 互联网直连那一格：一枚输入 + 一颗按钮。 */
.roots {
  margin: 4px 0 0;
}
.roots__add {
  display: flex;
  align-items: center;
  gap: 8px;
}
.roots__input {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid color-mix(in srgb, var(--warn-ink) 35%, transparent);
  border-radius: var(--radius-md);
  background: transparent;
}
</style>
