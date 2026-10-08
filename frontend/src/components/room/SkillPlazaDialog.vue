<script setup lang="ts">
// 技能广场：黄底一块灯板，一颗一颗「洞」按下去就亮。亮了的技能，这间房里每一个
// agent 从下一轮起都照它的说明书做（后端把它们拼进提示词）。
//
// 只管画、只往上发：列表从哪来、点亮存到哪、健康怎么探，都在 `useTopicSkills` 里。
// 桌面上是居中弹窗，手机上是一整页 —— 外壳由 AdaptiveDialog 决定，这一块在两边
// 都是同一张灯板。
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

// 只声明它真画的那几个字段，不从 API 层连类型一起搬进来：组件按规矩不认识 API 层
// （scripts/import-boundary-ratchet-core.mjs），而父组件传下来的是结构相同的对象。
interface PlazaSkill {
  name: string
  description: string
  enabled: boolean
}
interface PlazaHealth {
  status: 'ok' | 'degraded' | 'unavailable'
  detail: string
}

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    skills: PlazaSkill[]
    health: Record<string, PlazaHealth>
    loading?: boolean
  }>(),
  { loading: false }
)

const emit = defineEmits<{ (e: 'toggle', name: string): void }>()

// 每颗的技能图标。认不出名字就画一颗通用拼图 —— 广场是数据驱动的（洞的数量跟着
// 技能走，不是写死的 22 个），多一个技能不认识，也不该少一格。
const ICONS: Record<string, string> = {
  cheese: 'mdi-cheese',
  documents: 'mdi-file-document-outline',
  showcase: 'mdi-view-dashboard-outline',
  wolfram: 'mdi-function-variant',
}
const icon = (name: string) => ICONS[name] ?? 'mdi-puzzle-outline'

// 健康圆点说的话。文案在这里只写键名，真正取词在 healthLabel 里 —— 四个状态加上
// 「还没探到」这一种。
const HEALTH: Record<PlazaHealth['status'] | 'unknown', string> = {
  ok: 'work.room.skills.healthOk',
  degraded: 'work.room.skills.healthDegraded',
  unavailable: 'work.room.skills.healthUnavailable',
  unknown: 'work.room.skills.healthUnknown',
}
const status = (name: string): PlazaHealth['status'] | 'unknown' => props.health[name]?.status ?? 'unknown'
const healthLabel = (name: string) => t(HEALTH[status(name)])
</script>

<template>
  <AdaptiveDialog v-model="open" :title="t('work.room.skills.title')" max-width="520">
    <div class="plaza">
      <p class="plaza__hint">{{ t('work.room.skills.hint') }}</p>
      <p v-if="loading" class="plaza__note">{{ t('work.room.skills.loading') }}</p>
      <p v-else-if="!skills.length" class="plaza__note">{{ t('work.room.skills.empty') }}</p>
      <div v-else class="plaza__grid">
        <button
          v-for="s in skills"
          :key="s.name"
          type="button"
          class="socket"
          :class="{ 'socket--on': s.enabled }"
          :aria-pressed="s.enabled"
          :title="s.description || s.name"
          @click="emit('toggle', s.name)"
        >
          <span class="socket__hole"
            ><v-icon size="20">{{ icon(s.name) }}</v-icon></span
          >
          <span class="socket__name">{{ s.name }}</span>
          <span class="socket__dot" :class="`socket__dot--${status(s.name)}`" :title="healthLabel(s.name)" />
        </button>
      </div>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
/* 黄底：整块广场是一张灯板，底色用 warn 的 wash、字用 warn 的 ink。两种主题下这套
   记号的意思不变（浅色是浅黄底深黄字，深色是深棕底浅黄字），所以不写死颜色。 */
.plaza {
  padding: 14px;
  background: var(--warn-wash);
  border-radius: var(--radius-lg);
}
.plaza__hint {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--warn-ink);
}
.plaza__note {
  margin: 0;
  font-size: 13px;
  color: var(--warn-ink);
}
/* 洞的数量跟着技能走：窄了自动换行，不写死几行几个。 */
.plaza__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
  gap: 10px;
}
.socket {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 6px 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
}
/* 那颗「洞」：两种主题下都是一枚深色的井 —— 用的那对记号（代码块）本来就是
   「两边都深」，所以浅色主题里它不会跟着 --ink 翻成一颗白点。没点亮时图标是灰的，
   点亮转琥珀、外面再围一圈琥珀；动的是图标和那圈光，井本身不动，一口气点亮好几颗
   时整块板都不会跳。 */
.socket__hole {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--code-bg);
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
  font-size: 12px;
  line-height: 1.4;
  text-align: center;
  color: var(--warn-ink);
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
</style>
