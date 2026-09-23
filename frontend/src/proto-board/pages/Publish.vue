<script setup lang="ts">
// 发题。这一页最大的变化是**它现在对所有人都开着** —— 今天后端在
// `may_publish_in_space` 里只放管理员过，普通用户点了会吃 403。
//
// 「领取人数」和「小队限制」两组字段原样保留（`participant_limit` / `min_team_size`
// / `max_team_size` 是真列名），只是重排成「先说要几个人，再说什么时候截止」。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import PanelCard from '../components/PanelCard.vue'
import { bilibiliBvid, CATEGORIES, PDF_PREVIEW } from '../fixtures'
import { isManager, publishFromPdf, publishTask } from '../store'

const router = useRouter()

/** 两条发题路：手写一道，或从一份 PDF 里批量生成（真平台是
 *  `POST /tasks/publish/from-pdf/preview|confirm`）。 */
const mode = ref<'write' | 'pdf'>('write')

const title = ref('')
const summary = ref('')
const category = ref(CATEGORIES[0])
const tagText = ref('')
const limitUnlimited = ref(false)
const limit = ref(20)
const minTeam = ref(1)
const maxTeam = ref(1)
const deadlineDays = ref(14)
const videoUrl = ref('')

/** 非 B 站链接能存不能播，所以这里不是拦，是问一句（与真平台表单同一个做法）。 */
const videoWarn = ref(false)
const pendingSubmit = ref(false)

const videoBvid = computed(() => bilibiliBvid(videoUrl.value))

const tags = computed(() =>
  tagText.value
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 5)
)

const errors = computed(() => {
  const out: string[] = []
  if (title.value.trim().length < 4) out.push('标题至少 4 个字')
  if (summary.value.trim().length < 10) out.push('题干至少 10 个字')
  if (maxTeam.value < minTeam.value) out.push('小队上限不能小于下限')
  if (!limitUnlimited.value && limit.value < 1) out.push('领取人数上限至少 1')
  return out
})

function submit() {
  if (errors.value.length) return
  if (videoUrl.value.trim() && !videoBvid.value) {
    videoWarn.value = true
    return
  }
  create()
}

function create() {
  const task = publishTask({
    title: title.value.trim(),
    summary: summary.value.trim(),
    category: category.value,
    tags: tags.value,
    participantLimit: limitUnlimited.value ? null : limit.value,
    minTeamSize: minTeam.value,
    maxTeamSize: maxTeam.value,
    deadlineDays: deadlineDays.value,
    videoUrl: videoUrl.value.trim() || null,
  })
  pendingSubmit.value = false
  videoWarn.value = false
  router.push(`/task/${task.id}`)
}

// --- 从 PDF 生成 ---------------------------------------------------------------

/** 解析是假的一步：真平台里这一步要跑 LLM，几秒到几十秒，还会把 PDF 里的图片
 *  抽出来上传。原型里点一下直接出结果，但**结果长什么样、要花多少 token 是真的会
 *  报回来的信息**，所以照样摆在界面上。 */
const parsing = ref(false)
const parsed = ref(false)
/** 确认发布之后的回执：显示「刚发了几道」，并给一个去队列的入口。 */
const publishedCount = ref(0)

interface Draft {
  key: string
  picked: boolean
  title: string
  summary: string
  category: string
  images: number
  sourcePage: number
}

const drafts = ref<Draft[]>([])

const pickedDrafts = computed(() => drafts.value.filter((d) => d.picked))

function parsePdf() {
  parsing.value = true
  drafts.value = []
  parsed.value = false
  window.setTimeout(() => {
    drafts.value = PDF_PREVIEW.drafts.map((d) => ({ ...d, picked: true }))
    parsing.value = false
    parsed.value = true
  }, 900)
}

function resetPdf() {
  parsing.value = false
  parsed.value = false
  drafts.value = []
  publishedCount.value = 0
}

function confirmPdf() {
  const list = pickedDrafts.value
  if (!list.length) return
  publishFromPdf(
    list.map((d) => ({
      title: d.title.trim(),
      summary: d.summary.trim(),
      category: d.category,
      sourcePage: d.sourcePage,
    }))
  )
  // 就地给回执，不跳走：审核队列是「先审自己的、再按提交时间从早到晚」排的，
  // 刚发的题会落在队列靠后，跳过去反而看不见自己刚做了什么。
  publishedCount.value = list.length
  parsed.value = false
  drafts.value = []
}
</script>

<template>
  <div class="pub">
    <div class="pub__head">
      <h1>{{ mode === 'write' ? '出一道题' : '从 PDF 生成题目' }}</h1>
      <p v-if="isManager" class="pub__note">
        你是所有者/管理员，发出来的题同样先进审核队列 —— 但<b>你自己就能审</b>，不必等别人。
      </p>
      <p v-else class="pub__note">发出来的题会先进审核队列，由所有者或管理员看过之后上板。</p>

      <!-- 两条路：手写一道，或让 PDF 先解析成草稿。 -->
      <v-btn-toggle v-model="mode" density="compact" variant="outlined" divided mandatory class="pub__mode">
        <v-btn value="write" size="small" prepend-icon="mdi-pencil-outline">手写一道</v-btn>
        <v-btn value="pdf" size="small" prepend-icon="mdi-file-pdf-box">从 PDF 生成</v-btn>
      </v-btn-toggle>
    </div>

    <!-- ============ 从 PDF 生成 ============ -->
    <div v-if="mode === 'pdf'" class="pub__pdf">
      <!-- 发布回执：不跳走，就地告诉人刚做了什么、下一步去哪儿看。 -->
      <PanelCard v-if="publishedCount" title="已发布">
        <div class="pdf__done">
          <v-icon icon="mdi-check-circle-outline" size="22" color="success" />
          <div>
            刚发的 <b>{{ publishedCount }}</b> 道题已经进了<b>待审核</b>队列 —— 解析不会绕开审核，上板还是要人看一眼。
            <div class="pdf__done-hint">
              去「审核」能看到它们（队列里你自己的题排在最前，按提交时间从早到晚）；在「我的」里也能看到这几道的状态。
            </div>
          </div>
        </div>
        <div class="pdf__actions">
          <v-btn variant="text" @click="resetPdf">再解析一份 PDF</v-btn>
          <v-spacer />
          <v-btn variant="tonal" to="/mine">去「我的」看这几道</v-btn>
          <v-btn color="primary" variant="flat" to="/review">去审核队列</v-btn>
        </div>
      </PanelCard>

      <PanelCard v-else title="上传 PDF" subtitle="真平台只收 PDF，单个文件最大 15MB，一次最多解析 20 道题">
        <div class="pdf__drop">
          <v-icon icon="mdi-file-pdf-box" size="34" />
          <div>
            <b>{{ PDF_PREVIEW.fileName }}</b>
            <span>{{ PDF_PREVIEW.pages }} 页 · 拖进来自动解析，或点右边按钮</span>
          </div>
          <v-spacer />
          <v-btn color="primary" variant="flat" :loading="parsing" @click="parsePdf">
            {{ parsed ? '重新解析' : '解析成题目草稿' }}
          </v-btn>
        </div>

        <div v-if="parsing" class="pdf__hint">正在读 PDF、抽出插图、按题目模板生成草稿……（真平台这一步要跑大模型）</div>
        <div v-else-if="parsed" class="pdf__meta">
          <span
            >模板：<b>{{ PDF_PREVIEW.templateUsed }}</b></span
          >
          <span
            >抽出插图 <b>{{ PDF_PREVIEW.imageCount }}</b> 张</span
          >
          <span
            >消耗 <b>{{ PDF_PREVIEW.tokenUsed.toLocaleString() }}</b> tokens</span
          >
          <span class="pdf__meta-warn">草稿<b>还没有成为题目</b>，确认之后才会进审核队列</span>
        </div>
      </PanelCard>

      <PanelCard
        v-if="parsed"
        title="解析出的草稿"
        :subtitle="`勾选要发的 ${pickedDrafts.length} / ${drafts.length} 道，标题和题干都能就地改`"
      >
        <ul class="pdf__list">
          <li v-for="d in drafts" :key="d.key" class="pdf__row" :class="{ 'pdf__row--off': !d.picked }">
            <v-checkbox v-model="d.picked" density="compact" hide-details class="pdf__pick" />
            <div class="pdf__body">
              <v-text-field
                v-model="d.title"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                class="pdf__title"
              />
              <v-textarea
                v-model="d.summary"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                rows="2"
                auto-grow
              />
              <div class="pdf__tags">
                <v-chip size="x-small" label variant="text">{{ d.category }}</v-chip>
                <v-chip size="x-small" label variant="text">原文第 {{ d.sourcePage }} 页</v-chip>
                <v-chip v-if="d.images" size="x-small" label variant="tonal" class="tone-warn">
                  含 {{ d.images }} 张插图
                </v-chip>
              </div>
            </div>
          </li>
        </ul>

        <div class="pdf__actions">
          <span class="pdf__actions-note">
            确认后这 {{ pickedDrafts.length }} 道都会进<b>待审核</b>队列 —— 解析归解析，上板还是要人审。
          </span>
          <v-spacer />
          <v-btn variant="text" @click="resetPdf">取消</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!pickedDrafts.length" @click="confirmPdf">
            确认发布 {{ pickedDrafts.length }} 道
          </v-btn>
        </div>
      </PanelCard>

      <v-empty-state
        v-else-if="!parsing"
        icon="mdi-file-search-outline"
        title="还没有解析结果"
        text="先点上面的「解析成题目草稿」；解析是只读的，不会直接发出去。"
      />
    </div>

    <!-- ============ 手写一道 ============ -->
    <div v-else class="pub__grid">
      <div class="pub__main">
        <PanelCard title="题目内容">
          <v-text-field
            v-model="title"
            autocomplete="off"
            label="标题"
            variant="outlined"
            density="comfortable"
            counter="80"
            maxlength="80"
          />
          <v-textarea
            v-model="summary"
            autocomplete="off"
            label="题干"
            variant="outlined"
            density="comfortable"
            rows="6"
            placeholder="说清楚要做什么、交付什么、怎么算完成。"
          />
          <div class="pub__row">
            <v-select
              v-model="category"
              autocomplete="off"
              :items="CATEGORIES"
              label="分类"
              variant="outlined"
              density="comfortable"
              hide-details
            />
            <v-text-field
              v-model="tagText"
              autocomplete="off"
              label="标签"
              variant="outlined"
              density="comfortable"
              hide-details
              placeholder="逗号分隔，最多 5 个"
            />
          </div>

          <!-- 讲解视频。真平台只支持 B 站内嵌播放，其它链接能存不能播 —— 所以这里
               不拦你，但会问一句；详情页上它是一块 16:9 的播放器。 -->
          <div class="pub__video">
            <v-text-field
              v-model="videoUrl"
              autocomplete="off"
              label="讲解视频（可选）"
              variant="outlined"
              density="comfortable"
              hide-details
              prepend-inner-icon="mdi-play-circle-outline"
              placeholder="https://www.bilibili.com/video/BV…"
            />
            <div class="pub__video-hint">
              <template v-if="videoBvid">
                <v-icon icon="mdi-check-circle-outline" size="14" />
                识别到 <b>{{ videoBvid }}</b
                >，详情页里会直接嵌成播放器。
              </template>
              <template v-else-if="videoUrl.trim()">
                <v-icon icon="mdi-alert-circle-outline" size="14" />
                只支持 Bilibili 视频嵌入播放，这个链接别人点开会播放不了。
              </template>
              <template v-else>只支持 Bilibili 链接；不填也可以。</template>
            </div>
          </div>
        </PanelCard>

        <PanelCard title="领取与小队" subtitle="这两组限制原样保留，只是排在了一起">
          <div class="pub__field">
            <div class="pub__field-label">
              <span>领取人数上限</span>
              <span class="pub__field-help">到上限后别人就点不动「领取」了。不限也可以。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="limit"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                :disabled="limitUnlimited"
                style="max-width: 120px"
              />
              <v-checkbox v-model="limitUnlimited" label="不限" density="compact" hide-details />
            </div>
          </div>

          <div class="pub__field">
            <div class="pub__field-label">
              <span>小队规模</span>
              <span class="pub__field-help">都填 1 就是单人题；否则领取时要凑够下限才成组。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="minTeam"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                label="下限"
                style="max-width: 110px"
              />
              <span class="pub__dash">–</span>
              <v-text-field
                v-model.number="maxTeam"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                label="上限"
                style="max-width: 110px"
              />
            </div>
          </div>

          <div class="pub__field">
            <div class="pub__field-label">
              <span>截止时间</span>
              <span class="pub__field-help">从今天算起。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="deadlineDays"
                type="number"
                suffix="天后"
                variant="outlined"
                density="compact"
                hide-details
                style="max-width: 150px"
              />
            </div>
          </div>
        </PanelCard>
      </div>

      <aside class="pub__side">
        <PanelCard title="发出去之后">
          <ol class="pub__steps">
            <li><b>待审核</b> —— 题目只有你自己和管理员看得到。</li>
            <li>
              <b>有人审了</b> ——
              <template v-if="isManager">你可以直接通过（自己发的题自己审）。</template>
              <template v-else>所有者或管理员通过后就上板。</template>
            </li>
            <li><b>上板</b> —— 所有人可见可领，领取进度开始计。</li>
            <li><b>你能看到</b> —— 「我的 → 我发布的」里有这道题的领取走势、领取者名单和完成情况。</li>
          </ol>
          <p class="pub__side-note">被驳回会带原因退回，改完可以重新提交，不用重写一遍。</p>
        </PanelCard>

        <PanelCard title="提交前">
          <ul v-if="errors.length" class="pub__errors">
            <li v-for="e in errors" :key="e">{{ e }}</li>
          </ul>
          <p v-else class="pub__ok">看起来没问题。</p>
          <v-btn block color="primary" variant="flat" :disabled="errors.length > 0" @click="submit">提交审核</v-btn>
        </PanelCard>
      </aside>
    </div>

    <!-- 非 B 站链接的提示：不是拦，是问一句（真平台表单里就是这句话）。 -->
    <v-dialog v-model="videoWarn" max-width="460" persistent>
      <v-card class="pa-2">
        <v-card-title class="text-body-1 font-weight-bold">视频链接提示</v-card-title>
        <v-card-text>
          <v-alert type="warning" variant="tonal" class="mb-0">
            无法解析该视频链接，视频链接可能有误。当前仅支持 Bilibili 视频嵌入播放，是否继续保存？
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="videoWarn = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="create">继续保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.pub__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.pub__note {
  margin: 6px 0 18px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
}

.pub__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .pub__grid {
    grid-template-columns: 1fr;
  }
}

.pub__main,
.pub__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pub__row {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 12px;
  margin-top: 4px;
}

.pub__field {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.pub__field-label {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.pub__field-label > span:first-child {
  font-size: 0.86rem;
}

.pub__field-help {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.pub__field-control {
  display: flex;
  gap: 12px;
  align-items: center;
}

.pub__dash {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.pub__steps {
  padding-left: 18px;
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.83rem;
  line-height: 1.9;
}

.pub__side-note {
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.76rem;
  line-height: 1.6;
}

.pub__errors {
  padding-left: 18px;
  margin: 0 0 12px;
  color: rgb(var(--v-theme-error));
  font-size: 0.8rem;
  line-height: 1.8;
}

.pub__ok {
  margin: 0 0 12px;
  color: rgb(var(--v-theme-success));
  font-size: 0.8rem;
}

.pub__mode {
  margin-bottom: 16px;
}

.pub__video {
  margin-top: 10px;
}

.pub__video-hint {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
}

/* ---- 从 PDF 生成 ---- */

.pub__pdf {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pdf__drop {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 18px;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.22);
  border-radius: var(--radius-md);
}

.pdf__drop b {
  display: block;
  font-size: 0.92rem;
}

.pdf__drop span {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}

.pdf__hint {
  margin-top: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
}

.pdf__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 0.78rem;
}

.pdf__meta-warn {
  color: rgb(var(--v-theme-warning));
}

.pdf__done {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 0.86rem;
  line-height: 1.7;
}

.pdf__done-hint {
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}

.pdf__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.pdf__row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.pdf__row--off {
  opacity: 0.5;
}

.pdf__pick {
  flex: 0 0 auto;
  margin-top: 2px;
}

.pdf__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.pdf__title :deep(input) {
  font-weight: 600;
}

.pdf__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.pdf__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__actions-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}
</style>
