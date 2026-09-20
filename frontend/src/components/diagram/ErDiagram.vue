<script setup lang="ts">
/**
 * ErDiagram.vue — 数据库关系图。
 *
 * 不引 mermaid / vue-flow：预览产物是单文件内联的，一个图库要加几百 KB，
 * 而且它们自带的配色跟这套设计令牌对不上。这里用 SVG 手绘，颜色全部走令牌。
 *
 * 布局不做自动排布（那是个大工程，还容易排得难看）：分组决定列，组内按给的
 * 顺序竖排。连线在挂载后量一次真实位置再画 —— 量真实位置而不是自己算高度，
 * 是因为表里的字换行与否取决于字宽，自己算一定会错位。
 */
import type { Point } from '@/lib/diagramGeometry'
import type { DiagramEntity, DiagramRelation } from '@/lib/diagramSpec'

import { computed, ref } from 'vue'

import { useMeasuredBoxes } from './useMeasuredBoxes'

import { bezierPoint, curvePath } from '@/lib/diagramGeometry'
import { cardinalityEnds } from '@/lib/diagramSpec'

const props = defineProps<{
  entities: DiagramEntity[]
  relations: DiagramRelation[]
  title?: string
  subtitle?: string
}>()

interface Wire {
  key: string
  /** 连线的 path d。 */
  line: string
  soft: boolean
  from: string
  to: string
  /** 两端的基数记号（鸦爪 / 单竖），已经是 path d。 */
  marks: string[]
  label: { x: number; y: number; text: string } | null
}

const { host, boxes, size, setNodeRef } = useMeasuredBoxes(() => [props.entities, props.relations])

/** 当前高亮的表 id；为 null 时全亮。 */
const active = ref<string | null>(null)

const groups = computed(() => {
  const out: string[] = []
  for (const entity of props.entities) {
    const group = entity.group ?? '其他'
    if (!out.includes(group)) out.push(group)
  }
  return out
})

function byGroup(group: string): DiagramEntity[] {
  return props.entities.filter((entity) => (entity.group ?? '其他') === group)
}

/** 一端的基数记号。「1」画一根垂直短竖，「N」画鸦爪（三根线张向实体）。 */
function cardinalityMark(end: Point, away: number, kind: string): string | null {
  if (kind === '1') {
    const x = end.x + away * 9
    return `M ${x} ${end.y - 6} L ${x} ${end.y + 6}`
  }
  if (kind === 'N') {
    const x = end.x + away * 11
    return [
      `M ${x} ${end.y} L ${end.x} ${end.y}`,
      `M ${x} ${end.y} L ${end.x} ${end.y - 7}`,
      `M ${x} ${end.y} L ${end.x} ${end.y + 7}`,
    ].join(' ')
  }
  return null
}

const wires = computed<Wire[]>(() => {
  const box = boxes.value
  const out: Wire[] = []
  props.relations.forEach((relation, index) => {
    const a = box[relation.from]
    const b = box[relation.to]
    if (!a || !b) return

    // 横向有重叠就是同一列里的两张表 —— 那种情况左右绕会穿模，改成向右弓出去。
    const sameColumn = a.x + a.w > b.x && b.x + b.w > a.x
    let p0: Point
    let p1: Point
    let p2: Point
    let p3: Point

    if (relation.from === relation.to) {
      // 自引用（评论的 parent_id）。两端落在同一个盒子上，不岔开高度的话
      // 曲线会出去又原路折回来，看起来像一条没画完的线。
      const bow = 44
      p0 = { x: a.x + a.w, y: a.y + a.h * 0.35 }
      p3 = { x: a.x + a.w, y: a.y + a.h * 0.65 }
      p1 = { x: p0.x + bow, y: p0.y + 10 }
      p2 = { x: p3.x + bow, y: p3.y - 10 }
    } else if (sameColumn) {
      const bow = 60
      p0 = { x: a.x + a.w, y: a.y + a.h / 2 }
      p3 = { x: b.x + b.w, y: b.y + b.h / 2 }
      p1 = { x: p0.x + bow, y: p0.y }
      p2 = { x: p3.x + bow, y: p3.y }
    } else {
      const aOnLeft = a.x + a.w <= b.x
      p0 = { x: aOnLeft ? a.x + a.w : a.x, y: a.y + a.h / 2 }
      p3 = { x: aOnLeft ? b.x : b.x + b.w, y: b.y + b.h / 2 }
      const reach = Math.max(30, Math.min(180, Math.abs(p3.x - p0.x) * 0.45))
      const dir = aOnLeft ? 1 : -1
      p1 = { x: p0.x + dir * reach, y: p0.y }
      p2 = { x: p3.x - dir * reach, y: p3.y }
    }

    const ends = cardinalityEnds(relation.cardinality)
    const away0 = Math.sign(p1.x - p0.x) || 1
    const away3 = Math.sign(p2.x - p3.x) || 1
    const marks = [cardinalityMark(p0, away0, ends.from), cardinalityMark(p3, away3, ends.to)].filter(
      (mark): mark is string => !!mark
    )

    const mid = bezierPoint(p0, p1, p2, p3, 0.5)
    const selfLoop = relation.from === relation.to
    const label = relation.label && (!sameColumn || selfLoop) ? { x: mid.x, y: mid.y - 7, text: relation.label } : null

    out.push({
      key: `${relation.from}-${relation.to}-${index}`,
      line: curvePath(p0, p1, p2, p3),
      soft: !!relation.soft,
      from: relation.from,
      to: relation.to,
      marks,
      label,
    })
  })
  return out
})

/** 与高亮表相连的关系线保持点亮，其余压暗。 */
function wireState(wire: Wire): string {
  if (!active.value) return ''
  return wire.from === active.value || wire.to === active.value ? 'is-on' : 'is-off'
}

function cardState(id: string): string {
  if (!active.value || active.value === id) return ''
  const linked = props.relations.some(
    (relation) =>
      (relation.from === active.value && relation.to === id) || (relation.to === active.value && relation.from === id)
  )
  return linked ? 'is-linked' : 'is-dim'
}

const summary = computed(() =>
  props.relations
    .map((relation) => `${relation.from} 到 ${relation.to}（${relation.cardinality ?? '未标注'}）`)
    .join('；')
)
</script>

<template>
  <figure class="er">
    <figcaption v-if="title || subtitle" class="er__caption">
      <h3 v-if="title" class="er__title">{{ title }}</h3>
      <p v-if="subtitle" class="er__sub">{{ subtitle }}</p>
    </figcaption>

    <div ref="host" class="er__canvas" role="img" :aria-label="summary">
      <svg class="er__wires" :width="size.w" :height="size.h" :viewBox="`0 0 ${size.w} ${size.h}`">
        <g v-for="wire in wires" :key="wire.key" class="er-wire" :class="wireState(wire)">
          <path class="er-wire__hit" :d="wire.line" />
          <path class="er-wire__line" :d="wire.line" :class="{ 'is-soft': wire.soft }" />
          <path v-for="(mark, i) in wire.marks" :key="i" class="er-wire__mark" :d="mark" />
          <text v-if="wire.label" class="er-wire__label" :x="wire.label.x" :y="wire.label.y">
            {{ wire.label.text }}
          </text>
        </g>
      </svg>

      <div class="er__cols">
        <section v-for="group in groups" :key="group" class="er__col">
          <h4 class="er__col-title">{{ group }}</h4>
          <article
            v-for="entity in byGroup(group)"
            :key="entity.id"
            :ref="(el) => setNodeRef(entity.id, el)"
            class="er-card"
            :class="[cardState(entity.id), { 'is-new': entity.isNew, 'is-tentative': entity.tentative }]"
            tabindex="0"
            @mouseenter="active = entity.id"
            @mouseleave="active = null"
            @focusin="active = entity.id"
            @focusout="active = null"
          >
            <header class="er-card__head">
              <span class="er-card__name">{{ entity.title }}</span>
              <span v-if="entity.isNew" class="er-card__new">新增</span>
              <span v-if="entity.tentative" class="er-card__todo">待定</span>
            </header>
            <ul v-if="entity.columns.length" class="er-card__rows">
              <li v-for="column in entity.columns" :key="column.name" class="er-card__row">
                <span v-if="column.badge" class="er-card__badge">{{ column.badge }}</span>
                <span class="er-card__col">{{ column.name }}</span>
                <span v-if="column.type" class="er-card__type">{{ column.type }}</span>
                <span v-if="column.note" class="er-card__note" :title="column.note">
                  {{ column.note }}
                </span>
              </li>
            </ul>
            <p v-if="entity.note" class="er-card__foot">{{ entity.note }}</p>
          </article>
        </section>
      </div>
    </div>

    <p class="er__legend">
      <span class="er__legend-item"><b>鸦爪</b> 多 · <b>单竖</b> 一</span>
      <span class="er__legend-item"><span class="er-legend-line" /> 外键</span>
      <span class="er__legend-item"><span class="er-legend-line is-soft" /> 逻辑关联（无外键）</span>
      <span class="er__legend-item"><span class="er-legend-swatch" /> 本次新建的表</span>
      <span class="er__legend-item"><span class="er-legend-swatch is-tentative" /> 方案里还没定的表</span>
      <span class="er__legend-item">悬停任意一张表，只看跟它有关的线</span>
    </p>
  </figure>
</template>

<style scoped>
.er {
  margin: 0;
}

.er__caption {
  margin-bottom: 16px;
}

.er__title {
  margin-bottom: 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.er__sub {
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.er__canvas {
  position: relative;
  overflow-x: auto;
  padding: 4px;
}

.er__wires {
  position: absolute;
  top: 0;
  left: 0;
  overflow: visible;
  pointer-events: none;
}

.er-wire__hit {
  fill: none;
  stroke: transparent;
  stroke-width: 14;
  pointer-events: stroke;
}

.er-wire__line {
  fill: none;
  stroke: var(--line-2);
  stroke-width: 1.5;
  transition: stroke 0.2s ease;
}

.er-wire__mark {
  fill: none;
  stroke: var(--line-2);
  stroke-width: 1.5;
  stroke-linecap: round;
  transition: stroke 0.2s ease;
}

.er-wire__label {
  font-size: 11px;
  text-anchor: middle;
  fill: var(--faint);
}

.er-wire.is-off {
  opacity: 0.18;
}

.er-wire.is-on .er-wire__line,
.er-wire.is-on .er-wire__mark {
  stroke: var(--v-theme-primary);
}

.er-wire.is-on .er-wire__label {
  fill: var(--v-theme-primary);
}

.er-wire__line.is-soft {
  stroke-dasharray: 4 4;
}

/* 宽度不够时**横向滚动，不换行**。换行会把第三栏折到下面去，而连线是按真实
   位置画的：栏一折，同一张表的关系线就横穿整张图，读起来全错。 */
.er__cols {
  display: flex;
  align-items: flex-start;
  gap: 44px;
  width: max-content;
}

/* 一栏 236px × 四栏 + 三个 44px 间距 = 1076px，正好落在 1100 的宽容器里。
   再宽一点就要横向滚动了，所以这个数字跟 `.page-container--wide` 是绑的。 */
.er__col {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 236px;
  flex: 0 0 auto;
}

.er__col-title {
  margin-bottom: 2px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--faint);
  text-transform: uppercase;
}

.er-card {
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition:
    border-color 0.2s ease,
    opacity 0.2s ease;
}

.er-card:hover,
.er-card:focus-visible {
  border-color: var(--v-theme-primary);
  outline: none;
}

.er-card.is-new {
  border-color: var(--line-2);
  background: var(--fill);
}

.er-card.is-new:hover,
.er-card.is-new:focus-visible {
  border-color: var(--v-theme-primary);
}

.er-card.is-tentative {
  border-style: dashed;
}

.er-card.is-linked {
  border-color: var(--line-2);
}

.er-card.is-dim {
  opacity: 0.35;
}

.er-card__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.er-card__name {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.er-card__new {
  padding: 1px 6px;
  font-size: 10px;
  color: var(--ok-ink);
  background: var(--ok-wash);
  border-radius: var(--radius-sm);
}

.er-card__todo {
  padding: 1px 6px;
  font-size: 10px;
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}

.er-card__rows {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.er-card__row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}

.er-card__badge {
  width: 30px;
  flex: 0 0 auto;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--faint);
}

.er-card__col {
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--text);
}

.er-card__type {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--faint);
}

.er-card__note {
  overflow: hidden;
  font-size: 11px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.er-card__foot {
  padding-top: 8px;
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--muted);
  border-top: 1px solid var(--line);
}

.er__legend {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding-top: 16px;
  margin-top: 16px;
  font-size: 11px;
  color: var(--faint);
  border-top: 1px solid var(--line);
}

.er__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.er__legend-item b {
  font-weight: 600;
  color: var(--muted);
}

.er-legend-line {
  width: 18px;
  height: 0;
  border-top: 1.5px solid var(--line-2);
}

.er-legend-line.is-soft {
  border-top-style: dashed;
}

.er-legend-swatch {
  width: 14px;
  height: 10px;
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.er-legend-swatch.is-tentative {
  border-style: dashed;
}
</style>
