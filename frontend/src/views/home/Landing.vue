<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import logo from '@/assets/logo-plain.svg?url'
import HomepageMessage from '@/components/home/HomepageMessage.vue'
import AccountService from '@/services/account'

const router = useRouter()
const stages = [
  { id: 'discuss', label: '需求讨论' },
  { id: 'build', label: '协作执行' },
  { id: 'verify', label: '运行验证' },
  { id: 'review', label: '成果交付' },
]
const stage = ref('discuss')
const expanded = ref(false)
const sampleAnswer = ref(false)
const audiences = [
  {
    id: 'enterprise',
    label: '企业解决方案',
    title: '让团队把 AI 用进真实项目。',
    description:
      '为企业按需配置模型与云端资源，支持团队从需求讨论、协作执行到成果交付。企业也可以与高校团队共同开展项目，验证业务方案。',
    tags: ['模型与云端资源', '全流程协作', '按需采购'],
    steps: ['企业提出问题', '团队与 AI 推进项目', '共同评估成果'],
  },
  {
    id: 'institution',
    label: '高校与机构',
    title: '让实践育人，发生在真实项目里。',
    description:
      '面向学院、书院与创新创业项目，我们希望将项目实践、内容共创与 AI 协作结合起来，为未来学习中心建设提供支持。',
    tags: ['项目式学习', 'AI 指导支持', '组织协作'],
    steps: ['组织实践项目', '师生与 AI 共同参与', '积累过程与成果'],
  },
  {
    id: 'research',
    label: '科研与创新团队',
    title: '让讨论接得上，让探索继续往前。',
    description: '把选题讨论、资料梳理与项目产出放进同一个工作空间。团队和 AI 在工作中交换意见，保留判断的依据。',
    tags: ['研究讨论', '文献与代码', '成果审阅'],
    steps: ['提出一个问题', '团队与 AI 探索方案', '审阅共同的产出'],
  },
]
const audience = ref(audiences[0])
function selectStage(id: string) {
  stage.value = id
  expanded.value = false
  sampleAnswer.value = false
}
function moveTab(event: KeyboardEvent, group: 'stage' | 'audience') {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const buttons = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  if (group === 'stage') selectStage(stages[next].id)
  else audience.value = audiences[next]
  buttons[next].focus()
}
// A restored session may finish refreshing after the public page has mounted.
watch(
  () => AccountService.loggedIn,
  (loggedIn) => {
    if (loggedIn) void router.replace({ name: 'HomeSpaces' })
  }
)
</script>

<template>
  <main id="top" class="landing-page">
    <a class="skip-link" href="#experience"> 跳到产品演示 </a>
    <header class="site-header wrap">
      <a class="brand" href="#top" aria-label="知是首页">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span> cheese<span class="brand-cn">知是</span> </span>
      </a>
      <nav aria-label="主导航">
        <a href="#experience">产品体验</a>
        <a href="#content">内容共创</a>
        <a href="#teams">企业解决方案</a>
        <a href="#vision">我们的愿景</a>
      </nav>
      <a class="nav-entry" href="/account/signin">
        进入知是 <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="17" />
      </a>
    </header>
    <section class="hero wrap">
      <div class="hero-copy">
        <p class="eyebrow"><span class="amber-line" /> 全流程协作 · 从想法到成果</p>
        <h1>
          把项目，
          <br />
          真正
          <span class="highlight-word"> 做出来<span class="highlight-spark">✳</span> </span>
          。
        </h1>
        <p class="hero-description">
          和团队、AI 一起讨论、创作、运行和交付。
          <br class="desktop-break" />
          模型与云端环境，和项目协作放在一起，
          <br class="desktop-break" />
          让你把精力放在项目本身。
        </p>
        <div class="hero-actions">
          <a class="button button-dark" href="/account/signin">
            开始体验
            <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
          </a>
          <a class="watch-link" href="#teams">
            企业与高校方案 <v-icon class="landing-icon" icon="mdi-arrow-right" size="16" />
          </a>
        </div>
        <p class="free-entry">提供部分免费资源，体验项目协作</p>
        <div class="hero-audience">高校与机构 <span>·</span> 企业合作 <span>·</span> 科研与内容团队</div>
      </div>
      <figure class="collaboration-map" aria-label="团队、AI 队友、知识和成果围绕同一个项目协作">
        <div class="map-grid" />
        <span class="map-coordinate">A SHARED SPACE FOR GREAT IDEAS</span>
        <svg viewBox="0 0 560 450" class="map-lines" aria-hidden="true">
          <circle class="orbit" cx="280" cy="224" r="155" />
          <circle class="orbit orbit-inner" cx="280" cy="224" r="105" />
          <path
            d="M100 115 C190 115 170 224 280 224 S420 320 480 320 M420 80 C350 80 380 224 280 224 S150 360 110 360"
          />
          <path class="signal-line" d="M100 115 C190 115 170 224 280 224 S420 320 480 320" />
          <path class="signal-line signal-second" d="M420 80 C350 80 380 224 280 224 S150 360 110 360" />
        </svg>
        <div class="map-core">
          <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
          <strong>一个项目</strong>
          <span>共同的上下文</span>
        </div>
        <div class="map-node node-team">
          <v-icon class="landing-icon" icon="mdi-account-group-outline" size="20" />
          <div>
            <strong>团队</strong>
            <span>提出问题，作出判断</span>
          </div>
        </div>
        <div class="map-node node-ai">
          <v-icon class="landing-icon" icon="mdi-creation" size="21" />
          <div>
            <strong>AI 队友</strong>
            <span>参与讨论，推进任务</span>
          </div>
          <span class="live-dot" />
        </div>
        <div class="map-node node-knowledge">
          <v-icon class="landing-icon" icon="mdi-file-document-outline" size="20" />
          <div>
            <strong>项目知识</strong>
            <span>积累讨论与判断</span>
          </div>
        </div>
        <div class="map-node node-output">
          <v-icon class="landing-icon" icon="mdi-code-tags" size="20" />
          <div>
            <strong>共同的成果</strong>
            <span>让想法有迹可循</span>
          </div>
        </div>
        <div class="map-foot"><span>+</span> 人的判断 × AI 的执行 <span>+</span></div>
      </figure>
    </section>
    <div class="statement-strip">
      <div class="wrap">
        <span>AI FOR THE WHOLE JOURNEY</span>
        <p>你的团队，和一个持续参与的 AI 队友。</p>
        <v-icon class="landing-icon" icon="mdi-arrow-right" size="22" />
      </div>
    </div>
    <section id="experience" class="demo-section wrap">
      <div class="section-intro">
        <div>
          <p class="eyebrow">协作体验</p>
          <h2>
            一起推进，
            <br />
            交付真正的成果。
          </h2>
        </div>
        <p>
          需求、分工、运行与成果，在同一个项目里衔接。
          <br />
          <span class="muted">交互演示 · 内容为示例</span>
        </p>
      </div>
      <div class="demo-tabs">
        <div class="stage-list" role="tablist" aria-label="项目全过程" @keydown="moveTab($event, 'stage')">
          <button
            v-for="(item, index) in stages"
            :id="`stage-${item.id}`"
            :key="item.id"
            type="button"
            role="tab"
            :aria-selected="stage === item.id"
            :tabindex="stage === item.id ? 0 : -1"
            :aria-controls="`panel-${item.id}`"
            :data-active="stage === item.id || undefined"
            @click="selectStage(item.id)"
          >
            <span>0{{ index + 1 }}</span
            >{{ item.label }}
          </button>
        </div>
        <div class="workspace">
          <aside class="workspace-sidebar">
            <div class="workspace-name">
              <v-icon class="landing-icon" icon="mdi-layers-outline" size="19" /> 知识检索项目
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-compass-outline" size="16" /> 项目总览
            </div>
            <div class="sidebar-link active">
              <v-icon class="landing-icon" icon="mdi-message-outline" size="16" /> 需求与原型
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-file-document-outline" size="16" /> 项目文档
            </div>
            <div class="sidebar-caption">项目成员</div>
            <div class="member"><span class="avatar">企</span> 企业伙伴</div>
            <div class="member"><span class="avatar">研</span> 研究同学</div>
            <div class="member">
              <span class="avatar ai-avatar">
                <v-icon class="landing-icon" icon="mdi-creation" size="16" />
              </span>
              知是 <small>AI 队友</small>
            </div>
            <div class="sidebar-bottom">
              <v-icon class="landing-icon" icon="mdi-source-branch" size="15" /> 讨论与成果，一起留下
            </div>
          </aside>
          <div class="workspace-main">
            <div class="workspace-bar">
              <span>
                <span class="hash">#</span> 需求与原型
                <v-icon class="landing-icon" icon="mdi-chevron-right" size="14" />
              </span>
              <span class="member-dots">
                <span>企</span>
                <span>研</span>
                <span>✳</span>
              </span>
            </div>
            <div
              v-if="stage === 'discuss'"
              id="panel-discuss"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-discuss"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage who="企业伙伴">
                  <p>我们想验证：知识库能不能让新人更快找到答案？</p>
                </HomepageMessage>
                <HomepageMessage who="研究同学">
                  <p>先做一个小范围原型，回答里需要能看到资料来源。</p>
                </HomepageMessage>
                <HomepageMessage who="知是" ai>
                  <p>我会先梳理评估方法，再搭建检索原型。待确认的问题和产出会整理在项目文档里。</p>
                  <div class="inline-note">
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" /> 项目文档随工作更新
                  </div>
                </HomepageMessage>
                <div class="demo-composer">
                  <span>在项目里，继续这段讨论</span>
                  <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" /> 项目文档
                </div>
                <h3>知识检索原型</h3>
                <p class="doc-label">当前目标</p>
                <p>验证有来源引用的知识检索原型</p>
                <p class="doc-label">已达成共识</p>
                <p><v-icon class="landing-icon" icon="mdi-check" size="14" /> 先验证效果，再扩大范围</p>
                <p class="doc-label">下一步</p>
                <p>确认评估样例</p>
                <div class="doc-bottom">讨论中的共识，在这里接着积累</div>
              </aside>
            </div>
            <div
              v-if="stage === 'build'"
              id="panel-build"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-build"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage who="知是" ai>
                  <p>检索原型与评估方案正在分别推进。团队可以在各自的任务里继续讨论。</p>
                </HomepageMessage>
                <div class="task-rows">
                  <div>
                    <v-icon class="landing-icon" icon="mdi-code-tags" size="19" />
                    <span>检索原型</span>
                    <small>进行中</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-message-outline" size="19" />
                    <span>评估方案</span>
                    <small>待讨论</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="19" />
                    <span>资料整理</span>
                    <small> <v-icon class="landing-icon" icon="mdi-check" size="13" /> 已完成 </small>
                  </div>
                </div>
                <p class="demo-note">以上为示例任务，不会启动真实 AI 或访问企业资料。</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-source-branch" size="15" /> 任务进展
                </div>
                <h3>各自推进，共同协作</h3>
                <p>原型和评估可以分别讨论，形成的结论汇入同一个项目。</p>
                <p class="doc-label">团队下一步</p>
                <p>确认原型的评估范围</p>
              </aside>
            </div>
            <div
              v-if="stage === 'verify'"
              id="panel-verify"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-verify"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage who="知是" ai
                  ><p>原型已经准备好。团队可以查看运行结果，继续反馈需要调整的地方。</p></HomepageMessage
                >
                <div class="prototype-preview">
                  <span class="eyebrow">知识检索原型 · 交互示例</span>
                  <h3>新人从哪里了解项目？</h3>
                  <button
                    type="button"
                    class="button button-dark"
                    :aria-expanded="sampleAnswer"
                    @click="sampleAnswer = !sampleAnswer"
                  >
                    {{ sampleAnswer ? '收起回答示例' : '查看回答示例'
                    }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>
                  <p v-if="sampleAnswer">
                    先阅读项目概览，再查看最近一次讨论中的决策与下一步计划。<span class="sample-citation"
                      >来源示例：项目入门指南 §1</span
                    >
                  </p>
                </div>
                <p class="demo-note">这是演示页面中的示例原型，未连接真实资料或运行 AI。</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">云端运行与预览</div>
                <h3>一起看，同一个结果</h3>
                <p>团队查看原型效果，AI 根据反馈继续修改。</p>
                <p class="doc-label">下一步</p>
                <p>确认结果后，提交成果审阅</p>
              </aside>
            </div>
            <div
              v-if="stage === 'review'"
              id="panel-review"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-review"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage who="知是" ai>
                  <p>原型已准备好供团队审阅。评估范围和局限已写入说明，请确认下一步要验证的问题。</p>
                </HomepageMessage>
                <div class="review-card">
                  <span class="eyebrow">待团队审阅</span>
                  <h3>检索原型与评估说明</h3>
                  <p>在协作模式下，由人审阅与采纳成果。</p>
                  <button class="button button-dark" :aria-expanded="expanded" @click="expanded = !expanded">
                    {{ expanded ? '收起成果示例' : '查看成果示例' }}
                    <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>

                  <p v-if="expanded" class="expanded-result">
                    原型展示资料引用位置；评估说明列出待验证的问题。本演示不连接真实资料或运行 AI。
                  </p>
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" /> 成果说明
                </div>
                <h3>让判断有依据</h3>
                <p>看得到产出，也看得到产生这份成果的讨论与决策。</p>
                <p class="doc-label">下一步</p>
                <p>团队审阅后，继续下一轮验证</p>
              </aside>
            </div>
          </div>
        </div>
      </div>
      <div class="capabilities">
        <article>
          <v-icon class="landing-icon" icon="mdi-account-group-outline" />
          <h3>少搬运上下文</h3>
          <p>讨论、文档与成果放在一起，新加入的成员有据可查，AI 也能继续推进已有工作。</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" />
          <h3>让讨论接上执行</h3>
          <p>梳理资料、编写代码、整理报告，使用云端环境运行验证。AI 在项目里推进任务，团队继续讨论和调整方向。</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-source-branch" />
          <h3>看到成果，再做判断</h3>
          <p>协作模式下，AI 提交成果，由团队审阅与采纳。过程中的判断与产出一同留下。</p>
        </article>
      </div>
    </section>
    <section id="resources" class="resources-section wrap">
      <div>
        <p class="eyebrow">模型与云端环境</p>
        <h2>把资源准备好，<br />让团队开始协作。</h2>
        <p>项目里的 AI 可以调用平台接入的模型，并使用云端环境推进任务、运行代码、展示结果。</p>
      </div>
      <div class="resource-options">
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" size="24" />
          <h3>模型支持</h3>
          <p>在项目中使用团队可用的模型，围绕共同的目标持续协作。</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-server-outline" size="24" />
          <h3>云端运行环境</h3>
          <p>为代码和原型提供运行空间，团队围绕同一份结果继续讨论。</p>
        </article>
        <p class="resource-terms">
          提供部分免费体验资源；企业与高校可按需采购。可用模型、运行环境与额度以开通方案为准。
        </p>
      </div>
    </section>
    <section id="teams" class="teams-section">
      <div class="wrap">
        <div class="section-intro">
          <div>
            <p class="eyebrow">企业与高校解决方案</p>
            <h2>
              从一个项目开始，
              <br />
              支持整个团队。
            </h2>
          </div>
          <p>
            按需采购平台服务与资源，
            <br />
            支持研发、内容生产与项目实践。
          </p>
        </div>
        <div class="audience-tabs">
          <div class="audience-list" role="tablist" aria-label="使用场景" @keydown="moveTab($event, 'audience')">
            <button
              v-for="item in audiences"
              :id="`audience-${item.id}`"
              :key="item.id"
              type="button"
              role="tab"
              :aria-selected="audience.id === item.id"
              :tabindex="audience.id === item.id ? 0 : -1"
              aria-controls="audience-panel"
              :data-active="audience.id === item.id || undefined"
              @click="audience = item"
            >
              {{ item.label }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
            </button>
          </div>
          <div id="audience-panel" role="tabpanel" :aria-labelledby="`audience-${audience.id}`" tabindex="0">
            <div class="audience-content">
              <div>
                <h3>{{ audience.title }}</h3>
                <p>{{ audience.description }}</p>
                <div class="audience-tags">
                  <span v-for="tag in audience.tags" :key="tag">{{ tag }}</span>
                </div>
              </div>
              <div class="collaboration-flow">
                <div v-for="(step, index) in audience.steps" :key="step">
                  <span>{{ ['起点', '协作', '交付'][index] }}</span
                  ><strong>{{ step }}</strong
                  ><v-icon v-if="index < 2" class="landing-icon" icon="mdi-arrow-right" />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="organization-direction">
          <span class="eyebrow">整体方案 · 建设方向</span>
          <p>平台、AI 队友、场景内容与过程数据。</p>
          <span> 面向高校与机构，将项目实践、内容共创和企业合作组织在一起，建设支持师生持续探索的未来学习中心。 </span>
        </div>
      </div>
    </section>

    <section id="content" class="content-section wrap">
      <div class="content-layout">
        <div>
          <p class="eyebrow">内容生产与共创</p>
          <h2>
            把共同的思考，
            <br />
            做成值得分享的内容。
          </h2>
          <p>
            团队与 AI 一起梳理材料、组织表达、打磨成果。研究报告、项目案例和学习材料，都可以围绕同一个主题持续创作。
          </p>
          <p>
            将项目经验整理成学习材料，
            <br />
            在团队讨论与审阅中持续修改。
          </p>
        </div>
        <div class="content-artifacts" aria-label="内容共创场景示意">
          <div class="content-sheet">
            <small>内容共创场景 · 示例</small>
            <h3>一个项目，多种表达</h3>
            <div>
              <v-icon class="landing-icon" icon="mdi-file-document-outline" /> 研究报告 <span>梳理问题与发现</span>
            </div>
            <div><v-icon class="landing-icon" icon="mdi-layers-outline" /> 项目案例 <span>呈现过程与方法</span></div>
            <div><v-icon class="landing-icon" icon="mdi-compass-outline" /> 学习材料 <span>分享可复用的经验</span></div>
          </div>
          <div class="content-caption">
            <v-icon class="landing-icon" icon="mdi-creation" size="15" /> 人与 AI 共创 · 团队审阅
          </div>
        </div>
      </div>
    </section>
    <section id="vision" class="vision-section wrap">
      <p class="eyebrow">我们的愿景 · 未来学习中心</p>
      <div class="vision-layout">
        <h2>
          每一次合作，
          <br />
          都成为下一次
          <br />
          <span>探索的起点。</span>
        </h2>
        <div>
          <span class="vision-symbol" aria-hidden="true"> ✳ </span>
          <p>当 AI 成为持续参与的队友，团队积累着成果，也积累着形成成果的思考。</p>
          <p>知是希望把这些思考变成可继续学习、创作与实践的资源，让后来的人接得上。</p>
          <a
            class="policy-link"
            href="https://hudong.moe.gov.cn/srcsite/A16/s3342/202604/t20260410_1433240.html"
            target="_blank"
            rel="noreferrer"
          >
            了解未来学习中心的政策方向 <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="12" />
          </a>
          <span class="vision-english">
            THINK TOGETHER.
            <br />
            BUILD TOGETHER.
            <br />
            GROW TOGETHER.
          </span>
        </div>
      </div>
    </section>
    <section class="closing">
      <div class="wrap">
        <p>
          带上你的问题，
          <br />
          和团队一起开始。
        </p>
        <a class="button button-light" href="/account/signin">
          进入知是
          <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="19" />
        </a>
      </div>
    </section>
    <footer class="wrap">
      <a class="brand" href="#top" aria-label="知是首页">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span> cheese<span class="brand-cn">知是</span> </span>
      </a>
      <p>人与 AI，一起把事做成</p>
      <span> © 2026 知是 </span>
    </footer>
  </main>
</template>
<style scoped src="./landing.css"></style>
<style>
/* The workspace locks document scrolling; the public homepage scrolls as a document. */
html:has(.landing-page) {
  overflow-y: auto !important;
}
body:has(.landing-page),
#app:has(.landing-page) {
  height: auto;
  overflow: visible;
}
</style>
