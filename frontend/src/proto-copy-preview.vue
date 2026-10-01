<script setup lang="ts">
/**
 * 出错态文案的「现状 / 建议」对照页（预览专用，只挂在 `proto-feedback.ts` 那个入口上）。
 *
 * 为什么要有这一页：出错态的文案在七个页面里各写各的，而且**不是一处小差别** ——
 * 有的把服务端原话当标题，有的把原话挂在悬停上，有三处干脆把原话丢了换成一句固定话。
 * 只在纸上写「建议改成某某」看不出来这些结构差别，所以这里把**真实组件**摆出来，
 * 左边按现状传 props、右边按建议传 props，一眼看出差别在哪。
 *
 * 组件一行没改：`AdminEmptyState` 是七页（含队列那两视图、模型那三段）实际用的那一件，
 * `AdminQueueEmpty` 是队列专用的四态壳（它把服务端原话挂在外层 `title` 上）。这里只是
 * 换了传进去的 props，所以「建议」长什么样，就是产品改完之后长什么样。
 *
 * 建议的形状只有一种：**中性标题 + 服务端原话作说明 + 一颗对应动作的按钮**。
 * 有「重试」的地方就放「重试」；写失败横幅没有按钮，就写真实的下一步，不新加入口。
 */
import { computed } from 'vue'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminQueueEmpty from '@/components/admin/queue/AdminQueueEmpty.vue'

/** 服务端那句原话。预览的假服务在 `?fail=1` 下给的就是这一句，截图里看到的也是它。 */
const RAW = '服务暂时不可用（样例错误）'

type Slot = {
  /** 唯一编号：每个真实调用点一个，表和 PDF 都按它对齐。 */
  id: string
  /** 这一处在产品里的位置。 */
  where: string
  /** 用的是哪一件真实组件，以及它在结构上和别处的差别。 */
  shape: string
  now: { title: string; desc?: string; action: string; raw?: string | null }
  next: { title: string; desc: string; action: string }
  /** 按钮到底可不可见。 */
  button: '有「重试」' | '没有按钮'
  /** 服务端那句原话在这一处最后去了哪。 */
  rawFate: string
  diff: string
}

/** 读失败：十处结构，全部有「重试」按钮。 */
const LOAD: Slot[] = [
  {
    id: '#1',
    where: '反馈队列 · 列表视图',
    shape: 'AdminQueueEmpty 壳 → AdminEmptyState（四态块）',
    now: { title: '队列加载失败', desc: '检查网络后重试。', action: '重试', raw: RAW },
    next: { title: '队列加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '挂在外层 title 属性上，只有悬停才看得到',
    diff: '说明行从「检查网络后重试。」换成服务端原话；悬停那句取消（已经看得见了）。标题、按钮不动。',
  },
  {
    id: '#2',
    where: '反馈队列 · 表格视图',
    shape: '同上，只是插槽挂在表格上',
    now: { title: '表格加载失败', desc: '检查网络后重试。', action: '重试', raw: RAW },
    next: { title: '表格加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '挂在外层 title 属性上，只有悬停才看得到',
    diff: '同 #1 列表视图。',
  },
  {
    id: '#3',
    where: '看板',
    shape: 'AdminEmptyState（整块）',
    now: { title: '看板加载失败', desc: RAW, action: '重试' },
    next: { title: '看板加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '直接作说明行，可见',
    diff: '不动。这一处已经是建议的形状，它是别处照着改的样板。',
  },
  {
    id: '#4',
    where: '功能数据',
    shape: 'AdminEmptyState（整块）',
    now: { title: '读不到功能清单。', action: '重试' },
    next: { title: '功能数据加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '丢了 —— 页面只存一个布尔 failed',
    diff: '标题改成中性句式；新增说明行显示原话。要改组件：failed 得从布尔换成字符串。',
  },
  {
    id: '#5',
    where: '模型管理 · 模型段',
    shape: 'AdminModelsTable → AdminEmptyState（段内）',
    now: { title: RAW, action: '重试' },
    next: { title: '模型加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '当标题显示 —— 原话常常很长，标题行被撑得不像标题',
    diff: '标题换成中性「模型加载失败」，原话移到说明行。',
  },
  {
    id: '#6',
    where: '模型管理 · 额度段',
    shape: 'AdminModelsBudgets → AdminEmptyState（段内）',
    now: { title: RAW, action: '重试' },
    next: { title: '额度加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '当标题显示',
    diff: '同 #5 模型段，标题换成「额度加载失败」。',
  },
  {
    id: '#7',
    where: '模型管理 · 操作记录段',
    shape: 'AdminModelsAudit → AdminEmptyState（段内）',
    now: { title: RAW, action: '重试' },
    next: { title: '最近操作加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '当标题显示',
    diff: '同 #5 模型段，标题换成「最近操作加载失败」。',
  },
  {
    id: '#8',
    where: '空间申请',
    shape: 'AdminEmptyState（紧凑版，在卡里）',
    now: { title: '加载失败，请重试。', action: '重试' },
    next: { title: '空间申请加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '丢了 —— 页面把 loadError 直接写成那句固定话',
    diff: '标题改成中性句式；新增说明行显示原话。要改组件：loadError 得存原话而不是固定话。',
  },
  {
    id: '#9',
    where: '成员管理',
    shape: 'AdminEmptyState（表格的 #error 槽）',
    now: { title: RAW, action: '重试' },
    next: { title: '成员加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '当标题显示',
    diff: '标题换成中性「成员加载失败」，原话移到说明行。',
  },
  {
    id: '#10',
    where: '飞书应用',
    shape: 'AdminEmptyState（整块）',
    now: { title: '无法读取', action: '重试' },
    next: { title: '飞书应用加载失败', desc: RAW, action: '重试' },
    button: '有「重试」',
    rawFate: '丢了 —— 页面只存一个布尔 loadError',
    diff: '标题改成中性句式并说清是哪一页；新增说明行显示原话。要改组件：布尔换成字符串。',
  },
]

/** 写失败横幅：五处，都没有「重试」按钮 —— 别在这里新加入口。 */
const WRITE: { id: string; where: string; now: string; next: string; diff: string }[] = [
  {
    id: 'W1',
    where: '空间申请 · 审核未完成',
    now: '审核未完成，请刷新确认申请状态后重试。',
    next: '审核未完成，刷新页面确认申请状态。',
    diff: '现在这句里的「重试」旁边没有按钮，容易看成丢了按钮。改成真实下一步（刷新确认状态），不新加入口。',
  },
  {
    id: 'W2',
    where: '空间申请 · 写失败',
    now: '服务端原话',
    next: '服务端原话',
    diff: '不动。已经显示原话。',
  },
  {
    id: 'W3',
    where: '成员管理 · 写失败',
    now: '服务端原话',
    next: '服务端原话',
    diff: '不动。',
  },
  {
    id: 'W4',
    where: '模型管理 · 写失败',
    now: '服务端原话',
    next: '服务端原话',
    diff: '不动。',
  },
  {
    id: 'W5',
    where: '飞书应用 · 保存失败',
    now: '服务端原话',
    next: '服务端原话',
    diff: '不动。',
  },
]

const loadCount = computed(() => LOAD.length)
const lostCount = computed(() => LOAD.filter((s) => s.rawFate.startsWith('丢了')).length)
const titleIsRawCount = computed(() => LOAD.filter((s) => s.rawFate.startsWith('当标题')).length)
const shellCount = computed(() => LOAD.filter((s) => s.rawFate.startsWith('挂在外层')).length)
const descCount = computed(() => LOAD.filter((s) => s.rawFate.startsWith('直接作说明行')).length)
</script>

<template>
  <div class="cp">
    <header class="cp__head">
      <h1 class="cp__h1 t-title">出错态文案 · 现状与建议</h1>
      <p class="cp__lede t-body">
        下面每一块的左右两栏都是<strong>真实组件</strong>：左边按现状传 props，右边按建议传 props。
        组件一行没改，所以右边就是产品改完之后的样子。服务端那句原话在
        <code>«&nbsp;{{ RAW }}&nbsp;»</code>。
      </p>
      <ul class="cp__facts t-meta-read">
        <li>
          读失败 <strong>{{ loadCount }}</strong> 处真实调用点，编号 <strong>#1–#{{ loadCount }}</strong>
          （一页里有几段就编几个号，不按页数算），<strong>全部有「重试」按钮</strong>。
        </li>
        <li>
          服务端原话去向：<strong>丢了 {{ lostCount }}</strong
          >（#4 #8 #10）· <strong>当标题 {{ titleIsRawCount }}</strong
          >（#5 #6 #7 #9）· <strong>只在悬停 {{ shellCount }}</strong
          >（#1 #2）· <strong>作说明行 {{ descCount }}</strong
          >（#3）。四类相加 {{ lostCount + titleIsRawCount + shellCount + descCount }}，与 {{ loadCount }} 对得上。
        </li>
        <li>只有<strong>看板 #3</strong> 一处是「中性标题 + 原话作说明 + 按钮」，它是别处照着改的样板。</li>
        <li>
          写失败横幅 <strong>{{ WRITE.length }}</strong> 处编号 <strong>W1–W{{ WRITE.length }}</strong
          >，<strong>没有按钮</strong>，不新加入口，也不与读失败混算。
        </li>
      </ul>
    </header>

    <h2 class="cp__h2 t-title">一、读失败（都有「重试」按钮）</h2>
    <section v-for="s in LOAD" :key="s.id" class="cp__row">
      <div class="cp__meta">
        <h3 class="cp__where t-title">{{ s.id }} {{ s.where }}</h3>
        <p class="cp__shape t-meta-read">{{ s.shape }}</p>
        <p class="cp__badge" :class="{ 'cp__badge--warn': s.rawFate.startsWith('丢了') }">
          按钮：{{ s.button }} · 原话去向：{{ s.rawFate }}
        </p>
        <p class="cp__diff t-body">{{ s.diff }}</p>
      </div>
      <div class="cp__pair">
        <figure class="cp__cell">
          <figcaption class="cp__cap t-meta-read">现状</figcaption>
          <div class="cp__stage">
            <AdminEmptyState
              :title="s.now.title"
              :desc="s.now.desc"
              :action="s.now.action || undefined"
              tone="error"
              compact
            />
          </div>
        </figure>
        <figure class="cp__cell">
          <figcaption class="cp__cap t-meta-read">建议</figcaption>
          <div class="cp__stage">
            <AdminEmptyState :title="s.next.title" :desc="s.next.desc" :action="s.next.action" tone="error" compact />
          </div>
        </figure>
      </div>
      <!-- 队列那两处还多一层壳：原话挂在外层 title 上。这里把壳也摆出来，悬停看得到。 -->
      <div v-if="s.now.raw" class="cp__shellnote t-meta-read">
        队列那一层 <code>AdminQueueEmpty</code> 现在把原话挂在外层 <code>title</code> 上（悬停可见）：
        <span class="cp__rawchip" :title="s.now.raw || undefined">{{ s.now.raw }}</span>
      </div>
    </section>

    <h2 class="cp__h2 t-title">二、写失败横幅（没有按钮，不新加入口）</h2>
    <table class="cp__table">
      <thead>
        <tr>
          <th>编号</th>
          <th>位置</th>
          <th>现状</th>
          <th>建议</th>
          <th>变化</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="w in WRITE" :key="w.id">
          <td>{{ w.id }}</td>
          <td>{{ w.where }}</td>
          <td>{{ w.now }}</td>
          <td>{{ w.next }}</td>
          <td>{{ w.diff }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
/* 预览脚手架自己的版面。它不是产品界面，所以这里只用设计令牌摆位置，不写产品规则。 */
.cp {
  max-width: 1440px;
  margin: 0 auto;
  padding: 24px 16px 64px;
}

.cp__h1 {
  margin: 0 0 8px;
  font-size: 20px;
}

.cp__lede {
  max-width: 720px;
  margin: 0 0 12px;
  color: var(--muted);
}

.cp__facts {
  max-width: 720px;
  margin: 0 0 24px;
  padding-left: 20px;
  color: var(--muted);
}

.cp__h2 {
  margin: 32px 0 12px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  font-size: 16px;
}

.cp__row {
  margin-bottom: 20px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.cp__where {
  margin: 0 0 4px;
  font-size: 15px;
}

.cp__shape {
  margin: 0 0 8px;
  color: var(--faint);
}

.cp__badge {
  display: inline-block;
  margin: 0 0 8px;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
}

.cp__badge--warn {
  color: var(--danger);
}

.cp__diff {
  margin: 0 0 12px;
  color: var(--ink);
}

.cp__pair {
  display: flex;
  gap: 12px;
}

.cp__cell {
  flex: 1 1 0;
  min-width: 0;
  margin: 0;
}

.cp__cap {
  margin: 0 0 4px;
  color: var(--faint);
}

.cp__stage {
  padding: 8px 0;
  background: var(--canvas);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.cp__shellnote {
  margin: 8px 0 0;
  color: var(--faint);
}

.cp__rawchip {
  border-bottom: 1px dotted var(--line-2);
  cursor: help;
}

.cp__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.cp__table th,
.cp__table td {
  padding: 8px 10px;
  border: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}

.cp__table th {
  background: var(--surface);
  color: var(--muted);
  font-weight: 600;
}
</style>
