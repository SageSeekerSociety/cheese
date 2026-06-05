// Generate docs/slides.pptx from the narrative in docs/slides.md.
// Theme: Midnight Executive (navy + ice blue + white) + coral accent.
// Run: node docs/scripts/make-pptx.mjs

import pptxgen from "pptxgenjs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const OUTPUT = path.resolve(__dirname, "../slides.pptx");

// -----------------------------------------------------------------------------
// Design tokens
// -----------------------------------------------------------------------------
const C = {
  navy: "1E2761",        // primary dark
  navyDeep: "141A4A",    // darker accent
  ice: "CADCFC",         // secondary light
  white: "FFFFFF",
  ink: "1F2330",         // body text on light
  mute: "6B7280",        // captions
  rule: "D7DEEC",        // divider on light
  accent: "F96167",      // coral accent for ⭐ / highlights
  gold: "E8B84E",        // gold accent (alt)
  cardBg: "F4F6FC",      // very light navy tint for content cards
  chipBg: "E6ECFB",
};

const FONT = {
  head: "PingFang SC",   // macOS system CJK
  body: "PingFang SC",
  mono: "Menlo",
};

// -----------------------------------------------------------------------------
// Presentation setup
// -----------------------------------------------------------------------------
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.3" x 7.5"
pres.author = "andylizf";
pres.title = "知是项目汇报 · 2026-04-22";
pres.company = "Cheese / 知是";

const W = 13.333;
const H = 7.5;

// Master: dark "section divider"
pres.defineSlideMaster({
  title: "DARK",
  background: { color: C.navy },
  objects: [
    { rect: { x: 0, y: 0, w: 0.18, h: H, fill: { color: C.ice } } },
  ],
});

// Master: light content slide
pres.defineSlideMaster({
  title: "LIGHT",
  background: { color: C.white },
  objects: [
    // left rail — visual motif we repeat everywhere
    { rect: { x: 0, y: 0, w: 0.18, h: H, fill: { color: C.navy } } },
    // footer
    {
      text: {
        text: "知是项目汇报 · 2026-04-22",
        options: {
          x: 0.5, y: H - 0.35, w: 6, h: 0.25,
          fontFace: FONT.body, fontSize: 9, color: C.mute, margin: 0,
        },
      },
    },
    {
      text: {
        text: "andylizf",
        options: {
          x: W - 3.2, y: H - 0.35, w: 2.7, h: 0.25,
          fontFace: FONT.body, fontSize: 9, color: C.mute, align: "right", margin: 0,
        },
      },
    },
  ],
  slideNumber: {
    x: W - 0.5, y: H - 0.35, w: 0.3, h: 0.25,
    fontFace: FONT.body, fontSize: 9, color: C.mute, align: "right",
  },
});

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------
function titleBlock(slide, title, { kicker } = {}) {
  if (kicker) {
    slide.addText(kicker, {
      x: 0.7, y: 0.45, w: W - 1.4, h: 0.35,
      fontFace: FONT.body, fontSize: 12, color: C.accent, bold: true,
      charSpacing: 4, margin: 0,
    });
  }
  slide.addText(title, {
    x: 0.7, y: kicker ? 0.85 : 0.6, w: W - 1.4, h: 0.9,
    fontFace: FONT.head, fontSize: 30, bold: true, color: C.navy, margin: 0,
  });
}

function card(slide, { x, y, w, h, fill = C.cardBg, radius = 0.08 }) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h,
    fill: { color: fill }, line: { color: C.rule, width: 0.5 },
    rectRadius: radius,
  });
}

function numberBadge(slide, { x, y, n, color = C.navy, bg = C.ice, size = 0.7 }) {
  slide.addShape(pres.shapes.OVAL, {
    x, y, w: size, h: size, fill: { color: bg }, line: { color: bg, width: 0 },
  });
  slide.addText(String(n), {
    x, y, w: size, h: size, align: "center", valign: "middle",
    fontFace: FONT.head, fontSize: 22, bold: true, color, margin: 0,
  });
}

function chip(slide, { x, y, w, text, fill = C.chipBg, color = C.navy }) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h: 0.45, fill: { color: fill }, line: { color: fill, width: 0 },
    rectRadius: 0.22,
  });
  slide.addText(text, {
    x, y, w, h: 0.45, align: "center", valign: "middle",
    fontFace: FONT.body, fontSize: 11, bold: true, color, margin: 0,
  });
}

// -----------------------------------------------------------------------------
// Slide 1 · Title cover (DARK)
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "DARK" });

  // accent dot motif
  s.addShape(pres.shapes.OVAL, { x: 11.7, y: 0.9, w: 0.35, h: 0.35, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  s.addShape(pres.shapes.OVAL, { x: 12.2, y: 0.9, w: 0.35, h: 0.35, fill: { color: C.gold }, line: { color: C.gold, width: 0 } });
  s.addShape(pres.shapes.OVAL, { x: 12.7, y: 0.9, w: 0.35, h: 0.35, fill: { color: C.ice }, line: { color: C.ice, width: 0 } });

  s.addText("CHEESE · 知是", {
    x: 0.9, y: 2.1, w: 10, h: 0.4,
    fontFace: FONT.body, fontSize: 14, bold: true, color: C.ice,
    charSpacing: 12, margin: 0,
  });
  s.addText("知是项目汇报", {
    x: 0.9, y: 2.5, w: 12, h: 1.4,
    fontFace: FONT.head, fontSize: 72, bold: true, color: C.white, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.9, y: 4.1, w: 1.0, h: 0.08, fill: { color: C.accent }, line: { color: C.accent, width: 0 },
  });
  s.addText("过去几个月  ·  产品方向重定位  ·  接下来怎么走", {
    x: 0.9, y: 4.3, w: 12, h: 0.55,
    fontFace: FONT.head, fontSize: 22, color: C.ice, margin: 0,
  });

  s.addText("andylizf", {
    x: 0.9, y: 6.3, w: 6, h: 0.4,
    fontFace: FONT.body, fontSize: 14, color: C.white, bold: true, margin: 0,
  });
  s.addText("2026-04-22", {
    x: 0.9, y: 6.65, w: 6, h: 0.35,
    fontFace: FONT.body, fontSize: 12, color: C.ice, margin: 0,
  });

  s.addNotes([
    "开场 30 秒：",
    "今天想和两位老师汇报三件事：过去几个月做了什么、把产品方向重新想了一遍、接下来打算怎么走。",
    "这几个月我在海外，没亲自写多少代码 —— 但是完成了一条结构性的基建链 + 一次系统性的方向重定位。",
    "讲的过程里会有一些地方特别想请两位从各自的视角帮我判断，我会在对应的地方停一下。",
  ].join("\n"));
}

// -----------------------------------------------------------------------------
// Slide 2 · 一条因果链 (LIGHT)
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "过去几个月 · 一条因果链", { kicker: "PART Ⅰ · 过去几个月的硬交付" });

  s.addText("技术底座重构 → 人力机制搭建 → 用户端落地", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 18, color: C.accent, bold: true, italic: true, margin: 0,
  });

  // Three chain boxes
  const boxY = 3.0, boxH = 3.0, boxW = 3.65, gap = 0.35;
  const startX = (W - (3 * boxW + 2 * gap)) / 2;
  const items = [
    {
      n: 1,
      head: "技术底座",
      body: "后端多仓收敛为 cheese-backend-py",
      sub: "AI-friendly 单仓 · 636 个集成测试全通过",
    },
    {
      n: 2,
      head: "人力机制",
      body: "AI-first 大一大二梯队启动",
      sub: "和信院柴老师联合推动 · 4 人 / 1.5 月 / 7 改动",
    },
    {
      n: 3,
      head: "用户端",
      body: "eTrip 公网版上线",
      sub: "ruc-etrip.cn · 承载阿里 / 华为赛题",
    },
  ];
  items.forEach((it, i) => {
    const x = startX + i * (boxW + gap);
    card(s, { x, y: boxY, w: boxW, h: boxH });
    numberBadge(s, { x: x + 0.3, y: boxY + 0.3, n: it.n });
    s.addText(it.head, {
      x: x + 1.15, y: boxY + 0.35, w: boxW - 1.3, h: 0.5,
      fontFace: FONT.body, fontSize: 12, color: C.mute, charSpacing: 4, bold: true, margin: 0,
    });
    s.addText(it.body, {
      x: x + 0.3, y: boxY + 1.15, w: boxW - 0.6, h: 1.0,
      fontFace: FONT.head, fontSize: 18, color: C.navy, bold: true, margin: 0,
    });
    s.addText(it.sub, {
      x: x + 0.3, y: boxY + 2.15, w: boxW - 0.6, h: 0.7,
      fontFace: FONT.body, fontSize: 12, color: C.ink, margin: 0,
    });

    if (i < 2) {
      s.addText("→", {
        x: x + boxW + 0.02, y: boxY + boxH / 2 - 0.25, w: gap - 0.04, h: 0.5,
        fontFace: FONT.head, fontSize: 24, bold: true, color: C.accent, align: "center", valign: "middle", margin: 0,
      });
    }
  });

  s.addText("没重构就没有梯队能跑的土壤；没梯队就没有规模化执行；没规模化就撑不住 eTrip 这种真实场景。", {
    x: 0.7, y: 6.4, w: W - 1.4, h: 0.5,
    fontFace: FONT.body, fontSize: 12, color: C.mute, italic: true, align: "center", margin: 0,
  });

  s.addNotes("先用一张总览页定调。这三件事有因果关系 —— 没有重构就没有梯队能跑的土壤，没有梯队就没有规模化执行，没有规模化就撑不住 eTrip 这种真实场景。所以它们不是三件孤立的事，是一条链。下面每件简单展开一页。");
}

// -----------------------------------------------------------------------------
// Slide 3 · ① 技术底座 · cheese-backend-py 重构 ⭐
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "技术底座 · cheese-backend-py 重构", { kicker: "① 过去几个月的硬交付" });

  s.addText("从多仓（NestJS + Kotlin + Vue）→ 单一 Python / FastAPI 仓", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 16, color: C.accent, italic: true, margin: 0,
  });

  // Left: why column
  const lx = 0.7, ly = 2.6, lw = 7.5;
  s.addText("为什么改", {
    x: lx, y: ly, w: lw, h: 0.4,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  s.addText("跨仓对新人不友好，对 AI 也不友好。", {
    x: lx, y: ly + 0.4, w: lw, h: 0.5,
    fontFace: FONT.head, fontSize: 18, bold: true, color: C.navy, margin: 0,
  });
  s.addText([
    { text: "Codex / Claude Code 在多仓里上下文切换会丢大量信息", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "梯队大一大二要同时学 TypeScript 和 Kotlin 几乎不现实", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "AI coding 要真内置到产品里，代码库本身必须 AI-friendly", options: { bullet: { code: "25A0" } } },
  ], {
    x: lx, y: ly + 1.1, w: lw, h: 2.8,
    fontFace: FONT.body, fontSize: 14, color: C.ink, paraSpaceAfter: 8,
  });

  // Right: stat callout card
  const rx = 8.7, ry = 2.6, rw = 3.9;
  card(s, { x: rx, y: ry, w: rw, h: 4.0, fill: C.navy, radius: 0.12 });
  s.addText("现状", {
    x: rx, y: ry + 0.3, w: rw, h: 0.35,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.ice, align: "center", charSpacing: 6, margin: 0,
  });
  s.addText("636", {
    x: rx, y: ry + 0.7, w: rw, h: 1.4,
    fontFace: FONT.head, fontSize: 96, bold: true, color: C.white, align: "center", margin: 0,
  });
  s.addText("个集成测试全通过", {
    x: rx, y: ry + 2.05, w: rw, h: 0.4,
    fontFace: FONT.body, fontSize: 14, color: C.ice, align: "center", margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, { x: rx + 1.2, y: ry + 2.55, w: 1.5, h: 0.02, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  s.addText([
    { text: "3 月初建成 · 功能与 Kotlin 仓对齐", options: { breakLine: true } },
    { text: "线上暂保留 Kotlin 仓运行 eTrip" },
  ], {
    x: rx + 0.2, y: ry + 2.75, w: rw - 0.4, h: 1.1,
    fontFace: FONT.body, fontSize: 12, color: C.white, align: "center", paraSpaceAfter: 6, margin: 0,
  });

  s.addNotes("这是最早动手的一件事。听起来是纯工程迁移，但我想强调为什么这是面向未来的基建。窦老师您做 FlashRAG、带学生做 Agent 研究，应该深有体会 —— AI coding 工具在多仓里几乎没法工作，上下文切换一次就掉一大半信息。所以如果我们希望知是将来把 AI coding 真的内置到产品里（后面 §动手 会讲），前提是代码库本身 AI-friendly。顺带的好处是大一大二梯队上手成本降一大半 —— 只学一套 Python。这一层讲完后自然停一下，看窦老师有没有想接话。");
}

// -----------------------------------------------------------------------------
// Slide 4 · ② AI-first 大一大二梯队
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "人力机制 · AI-first 大一大二梯队", { kicker: "② 过去几个月的硬交付" });

  s.addText("和信院柴老师联合推动 · 已落地运行", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.4,
    fontFace: FONT.head, fontSize: 16, color: C.accent, italic: true, margin: 0,
  });

  // 3 stat cards
  const stats = [
    { num: "4", label: "位大一大二同学" },
    { num: "7", label: "个真实改动\n3 merged PR + 4 direct push" },
    { num: "1.5", label: "个月 (仓建成至今)" },
  ];
  const sy = 2.6, sh = 1.9, sw = 3.9, sgap = 0.2;
  const startSX = 0.7;
  stats.forEach((st, i) => {
    const x = startSX + i * (sw + sgap);
    card(s, { x, y: sy, w: sw, h: sh, fill: C.cardBg });
    s.addText(st.num, {
      x, y: sy + 0.1, w: sw, h: 1.1,
      fontFace: FONT.head, fontSize: 64, bold: true, color: C.navy, align: "center", margin: 0,
    });
    s.addText(st.label, {
      x: x + 0.2, y: sy + 1.2, w: sw - 0.4, h: 0.7,
      fontFace: FONT.body, fontSize: 12, color: C.ink, align: "center", margin: 0,
    });
  });

  // Bottom block: the real point
  const by = 4.8, bw = W - 1.4;
  card(s, { x: 0.7, y: by, w: bw, h: 2.2, fill: C.navy });
  s.addText("重点不是数量，是机制已跑通", {
    x: 1.0, y: by + 0.3, w: bw - 0.6, h: 0.5,
    fontFace: FONT.head, fontSize: 20, bold: true, color: C.white, margin: 0,
  });
  s.addText("筛选 → 培养 → 独立用 AI coding 完成 feature / fix，整条流水线已经走通。", {
    x: 1.0, y: by + 0.85, w: bw - 0.6, h: 0.5,
    fontFace: FONT.body, fontSize: 14, color: C.ice, margin: 0,
  });
  s.addText("判断：知是缺的不是方向，是技术人手。AI 让工程门槛压到大一大二也能干活。这是我回国后能\"主抓\"而不是\"单打独斗\"的前提。", {
    x: 1.0, y: by + 1.4, w: bw - 0.6, h: 0.7,
    fontFace: FONT.body, fontSize: 12, color: C.ice, italic: true, margin: 0,
  });

  s.addNotes("这件事是我在海外期间做的另一件实的事。柴老师两位老师可能都认识 —— 信院院长，他和我一起从今年初开始推这件事。逻辑很简单：我们发现最卡的不是产品方向，是手上人手不够；而 AI 让工程门槛压到大一大二也能干活。于是我们搞了一个小型的'筛选-培养-上手'流程。数字要老实讲 —— 目前是 4 位新同学、1.5 个月、7 个真实改动。数字不大，但机制走通了：他们真的能独立用 Codex / Claude Code 在 Python 单仓上提 PR、改功能。这是我回国后能'主抓'而不是'单打独斗'的前提。");
}

// -----------------------------------------------------------------------------
// Slide 5 · ③ eTrip 公网版上线
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "用户端 · eTrip 公网版上线", { kicker: "③ 过去几个月的硬交付" });

  // URL hero
  const ux = 0.7, uy = 2.0, uw = W - 1.4;
  card(s, { x: ux, y: uy, w: uw, h: 1.4, fill: C.navy, radius: 0.12 });
  s.addText("LIVE", {
    x: ux + 0.4, y: uy + 0.3, w: 1.0, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.navy, align: "center", valign: "middle",
    fill: { color: C.accent }, charSpacing: 6, margin: 0,
  });
  s.addText("http://ruc-etrip.cn/spaces", {
    x: ux + 0.4, y: uy + 0.75, w: uw - 0.8, h: 0.55,
    fontFace: FONT.mono, fontSize: 28, bold: true, color: C.white, margin: 0,
  });

  // Left: Partners
  const py = 3.8, plx = 0.7, plw = 6.0;
  s.addText("承载", {
    x: plx, y: py, w: plw, h: 0.35,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  s.addText("阿里 / 华为赛题", {
    x: plx, y: py + 0.35, w: plw, h: 0.5,
    fontFace: FONT.head, fontSize: 24, bold: true, color: C.navy, margin: 0,
  });
  s.addText("信院已部署。从\"我们做了什么\"到\"谁在用我们\"的过渡 —— 前两件是内功，这件是外显。", {
    x: plx, y: py + 0.9, w: plw, h: 1.2,
    fontFace: FONT.body, fontSize: 13, color: C.ink, margin: 0,
  });

  // Right: solved bottlenecks
  const prx = 7.0, prw = W - prx - 0.7;
  s.addText("解了几个底层卡点", {
    x: prx, y: py, w: prw, h: 0.35,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  const blockers = [
    ["内容保护", "require_auth"],
    ["非 .edu.cn 邮箱", "invite code"],
    ["SRP 登录", "bug 修复"],
  ];
  blockers.forEach((b, i) => {
    const y = py + 0.45 + i * 0.7;
    s.addShape(pres.shapes.OVAL, { x: prx, y: y + 0.07, w: 0.3, h: 0.3, fill: { color: C.ice }, line: { color: C.ice, width: 0 } });
    s.addText(b[0], {
      x: prx + 0.45, y, w: prw - 0.5, h: 0.32,
      fontFace: FONT.body, fontSize: 14, bold: true, color: C.navy, margin: 0,
    });
    s.addText(b[1], {
      x: prx + 0.45, y: y + 0.32, w: prw - 0.5, h: 0.28,
      fontFace: FONT.body, fontSize: 11, color: C.mute, italic: true, margin: 0,
    });
  });

  s.addNotes("最后一件是用户端的。这件事张超老师那边在主推，我们配合打通了几个技术卡点。目前线上已经有阿里和华为的赛题在跑。这是从\"我们做了什么\"到\"谁在用我们\"的过渡 —— 前两件是内功，这件是外显。");
}

// -----------------------------------------------------------------------------
// Slide 6 · Part Ⅱ divider
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("PART Ⅱ", {
    x: 0.9, y: 2.3, w: 10, h: 0.6,
    fontFace: FONT.body, fontSize: 18, bold: true, color: C.accent, charSpacing: 14, margin: 0,
  });
  s.addText("产品方向系统性重定位", {
    x: 0.9, y: 2.9, w: 12, h: 1.4,
    fontFace: FONT.head, fontSize: 60, bold: true, color: C.white, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.9, y: 4.5, w: 1.0, h: 0.08, fill: { color: C.ice }, line: { color: C.ice, width: 0 },
  });
  s.addText("这是这几个月我花最多心思的部分", {
    x: 0.9, y: 4.7, w: 12, h: 0.6,
    fontFace: FONT.head, fontSize: 20, color: C.ice, italic: true, margin: 0,
  });
  s.addNotes("过渡到第二部分。前面讲的是过去的交付，接下来我想讲这几个月思考的成果 —— 对知是整体方向的重新定位。这一部分我希望两位老师主动打断、主动质疑，因为方向对不对比做得快不快更重要。");
}

// -----------------------------------------------------------------------------
// Slide 7 · 定位升级
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "定位升级", { kicker: "PART Ⅱ · 产品方向重定位" });

  s.addText("从 \"进度管理\" → \"广义科研 / 开发的全过程\"", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 18, color: C.accent, bold: true, italic: true, margin: 0,
  });

  // Before card
  const cy = 2.8, cw = 5.7, ch = 3.2;
  const bx = 0.7, ax = W - 0.7 - cw;

  card(s, { x: bx, y: cy, w: cw, h: ch, fill: C.cardBg });
  s.addText("ORIGINAL", {
    x: bx + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  s.addText("赛题 + 协作 + 进度管理", {
    x: bx + 0.3, y: cy + 0.7, w: cw - 0.6, h: 0.6,
    fontFace: FONT.head, fontSize: 22, bold: true, color: C.ink, margin: 0,
  });
  s.addText("围绕\"怎么把管理 / 汇报做好\"——但这只是子模块。", {
    x: bx + 0.3, y: cy + 1.4, w: cw - 0.6, h: 1.6,
    fontFace: FONT.body, fontSize: 13, color: C.mute, margin: 0,
  });

  // Arrow
  s.addText("→", {
    x: bx + cw - 0.1, y: cy + ch / 2 - 0.3, w: (ax - bx - cw) + 0.2, h: 0.6,
    fontFace: FONT.head, fontSize: 36, bold: true, color: C.accent, align: "center", valign: "middle", margin: 0,
  });

  // After card
  card(s, { x: ax, y: cy, w: cw, h: ch, fill: C.navy });
  s.addText("NOW", {
    x: ax + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.accent, charSpacing: 6, margin: 0,
  });
  s.addText("学生的研究 / 项目工作从头到尾", {
    x: ax + 0.3, y: cy + 0.7, w: cw - 0.6, h: 0.6,
    fontFace: FONT.head, fontSize: 22, bold: true, color: C.white, margin: 0,
  });
  s.addText("工作本身在知是上发生，不是在外部做完再来填周报。", {
    x: ax + 0.3, y: cy + 1.4, w: cw - 0.6, h: 1.6,
    fontFace: FONT.body, fontSize: 13, color: C.ice, margin: 0,
  });

  // Footnote
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 6.3, w: 0.08, h: 0.5, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  s.addText("参考：北邮有个类似平台叫 \"智链\"（不是我们）—— 要做差异化。", {
    x: 0.9, y: 6.3, w: W - 1.6, h: 0.5,
    fontFace: FONT.body, fontSize: 12, color: C.mute, italic: true, valign: "middle", margin: 0,
  });

  s.addNotes("一句话的定位升级。原来我们写 plan 写得很细 —— Space 成员 / Progress Stream / 治理模式这些，全是围绕'怎么把管理/汇报做好'。但我想清楚了：这只是子模块。知是真正应该是什么？是一个学生从选题到结项一条龙都在上面发生的地方。插一句竞品：北邮有个类似平台叫智链。我们不是做它的复制品，要走一条更宽的路。");
}

// -----------------------------------------------------------------------------
// Slide 8 · 科研范围表格
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "但 \"科研\" 这个词范围要先说清楚", { kicker: "PART Ⅱ · 广义科研的边界" });

  // Table
  const tx = 0.7, ty = 2.0, tw = W - 1.4;
  const rows = [
    ["场景", "paper-driven 科研？", "项目 / 问题解决？"],
    ["高瓴博士生做 Agent", "✅", "✅"],
    ["信院创研课本科生", "❌", "✅"],
    ["明理书院创新项目", "❌", "✅"],
    ["eTrip 校企合作", "❌", "✅"],
    ["大一大二用 AI 写功能", "❌", "✅"],
  ];

  const table = rows.map((row, i) => {
    const isHead = i === 0;
    return row.map((cell, j) => ({
      text: cell,
      options: {
        fontFace: FONT.body,
        fontSize: isHead ? 12 : 13,
        bold: isHead,
        color: isHead ? C.white : (j === 0 ? C.navy : (cell === "✅" ? "2E7D32" : "B71C1C")),
        fill: { color: isHead ? C.navy : (i % 2 === 0 ? C.cardBg : C.white) },
        align: j === 0 ? "left" : "center",
        valign: "middle",
        margin: [4, 10, 4, 10],
      },
    }));
  });

  s.addTable(table, {
    x: tx, y: ty, w: tw,
    colW: [5.4, 3.25, 3.25],
    rowH: 0.42,
    border: { type: "solid", color: C.rule, pt: 0.5 },
  });

  // Takeaway banner
  const by = 5.15, bh = 1.5;
  card(s, { x: tx, y: by, w: tw, h: bh, fill: C.navy });
  s.addText("\"广义科研\" = 项目 / 问题解决的全过程", {
    x: tx + 0.3, y: by + 0.25, w: tw - 0.6, h: 0.55,
    fontFace: FONT.head, fontSize: 20, bold: true, color: C.white, margin: 0,
  });
  s.addText("狭义 paper-driven 科研只是光谱的一个高端子集 —— 但是这条路上最高价值的用户。", {
    x: tx + 0.3, y: by + 0.85, w: tw - 0.6, h: 0.55,
    fontFace: FONT.body, fontSize: 14, color: C.ice, margin: 0,
  });

  s.addNotes("这里有个 gap 我必须先讲清楚，不然后面容易误会。两位老师做的是 paper-driven 学术研究。知是的'研究'比这更宽 —— 我叫它广义科研 = 项目 / 问题解决。你看这张表，知是的真实用户大部分在右列 —— 本科生、创研课、校企合作、梯队这种。但这不是绕开科研讲应用。恰恰相反 —— 狭义科研是这条路最高阶的一等公民。如果我们把'项目/问题解决全过程'做扎实，高瓴博士生的科研工作流自然是其中最高价值的场景。窦老师，这里我特别想听您的看法：这个范围定义，您觉得我们扩得对不对？");
}

// -----------------------------------------------------------------------------
// Slide 9 · 6 环节 pipeline
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "核心叙事 · 学生在一个项目里的 6 个环节", { kicker: "PART Ⅱ · 核心产品设计" });

  // Pipeline
  const py = 2.9, ph = 1.5;
  const steps = ["选题", "文献", "讨论", "动手", "沉淀", "汇报"];
  const starIdx = 3;
  const pxStart = 0.7, pxEnd = W - 0.7;
  const stepW = 1.5;
  const totalSpan = pxEnd - pxStart;
  const stepGap = (totalSpan - 6 * stepW) / 5;

  steps.forEach((st, i) => {
    const x = pxStart + i * (stepW + stepGap);
    const isStar = i === starIdx;
    const fill = isStar ? C.accent : C.navy;
    const textColor = isStar ? C.white : C.white;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: py, w: stepW, h: ph,
      fill: { color: fill }, line: { color: fill, width: 0 }, rectRadius: 0.12,
    });
    s.addText(st, {
      x, y: py + 0.2, w: stepW, h: ph - 0.4,
      fontFace: FONT.head, fontSize: 24, bold: true, color: textColor, align: "center", valign: "middle", margin: 0,
    });
    if (isStar) {
      s.addText("⭐ 新核心", {
        x, y: py + ph + 0.1, w: stepW, h: 0.3,
        fontFace: FONT.body, fontSize: 11, bold: true, color: C.accent, align: "center", margin: 0,
      });
    }
    if (i < steps.length - 1) {
      const ax = x + stepW, aw = stepGap;
      s.addText("→", {
        x: ax, y: py, w: aw, h: ph, align: "center", valign: "middle",
        fontFace: FONT.head, fontSize: 22, color: C.mute, bold: true, margin: 0,
      });
    }
  });

  // Hook
  const hy = 5.3, hh = 1.6;
  card(s, { x: 0.7, y: hy, w: W - 1.4, h: hh, fill: C.cardBg });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: hy, w: 0.1, h: hh, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  s.addText("这一节是我最想请窦老师从带学生经验判断 \"有没有漏 / 有没有多\" 的。", {
    x: 1.0, y: hy + 0.25, w: W - 2.0, h: 0.5,
    fontFace: FONT.head, fontSize: 16, bold: true, color: C.navy, margin: 0,
  });
  s.addText("下面两页把 6 个环节展开（前三环 / 后三环），每一环是什么、我们怎么做。", {
    x: 1.0, y: hy + 0.85, w: W - 2.0, h: 0.55,
    fontFace: FONT.body, fontSize: 13, color: C.ink, margin: 0,
  });

  s.addNotes("这是全场最关键的产品设计 slide。下面两页我会把 6 个环节展开讲，每一环是什么、我们怎么做。讲完之后，窦老师，我最想听您的判断 —— 从您带博士生、做研究、自己跑 Agent 项目的经验看，这 6 个环节够不够 cover 真实的科研和项目协作？哪一环我们可能漏了，哪一环可能多了，您觉得？");
}

// -----------------------------------------------------------------------------
// Slide 10 · 前三环
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "6 环节展开 · 前三环", { kicker: "PART Ⅱ · 想清楚做什么" });

  const items = [
    {
      n: 1,
      head: "选题",
      sub: "Onboarding",
      body: "老师课题 / 企业需求 / 书院项目 / 学生自发 / 订阅推荐 —— 同一界面，多数据源；偏匹配的角色切极简视图。",
    },
    {
      n: 2,
      head: "文献 / 调研",
      sub: "Before you start",
      body: "Paper 中文 TLDR 订阅 + 老师自动学术主页（高瓴最重）+ 项目内 References + AI 综述骨架。",
    },
    {
      n: 3,
      head: "讨论",
      sub: "Making sense",
      body: "Discussion（@mention / reactions / 嵌套）+ 行内 @AI（项目级记忆）+ Project Assistant 抽屉 + 三层 AI 记忆（Thread / Project ⭐ / Personal）。",
    },
  ];

  const cy = 2.1, ch = 4.7;
  const cw = (W - 1.4 - 0.4) / 3;
  items.forEach((it, i) => {
    const x = 0.7 + i * (cw + 0.2);
    card(s, { x, y: cy, w: cw, h: ch });
    numberBadge(s, { x: x + 0.3, y: cy + 0.3, n: it.n, size: 0.8 });
    s.addText(it.sub.toUpperCase(), {
      x: x + 1.3, y: cy + 0.4, w: cw - 1.5, h: 0.35,
      fontFace: FONT.body, fontSize: 10, bold: true, color: C.mute, charSpacing: 5, margin: 0,
    });
    s.addText(it.head, {
      x: x + 1.3, y: cy + 0.7, w: cw - 1.5, h: 0.5,
      fontFace: FONT.head, fontSize: 22, bold: true, color: C.navy, margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, { x: x + 0.3, y: cy + 1.5, w: cw - 0.6, h: 0.02, fill: { color: C.rule }, line: { color: C.rule, width: 0 } });
    s.addText(it.body, {
      x: x + 0.3, y: cy + 1.65, w: cw - 0.6, h: ch - 1.9,
      fontFace: FONT.body, fontSize: 13, color: C.ink, paraSpaceAfter: 4, margin: 0,
    });
  });

  s.addNotes("前三环讲'想清楚做什么'这一段。选题不用多说 —— 我们不做三个独立工具去满足'找作业/找科研/找实习'，合成一个。文献这里有个点想特别强调：老师自动学术主页对高瓴是最重的。学生选导师 / 导师招生 / 对外展示都能用上。窦老师您自己的学术主页如果能自动聚合近况，招生应该也会更顺。讨论这里的关键词是项目级记忆 —— 每个项目 AI 记得之前所有讨论和决策，新人进来直接问 AI 能快速 catch up。这是和 Notion / 飞书的根本差别。");
}

// -----------------------------------------------------------------------------
// Slide 11 · 后三环
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "6 环节展开 · 后三环", { kicker: "PART Ⅱ · 做 + 交付" });

  const items = [
    {
      n: 4,
      head: "动手 ⭐",
      sub: "Where the real work happens · 新核心",
      body: "AI coding 内置（coder + Claude API，阿里 coding plan 接入）+ GitHub 双向同步 + 实验记录 —— 工作真的在平台上发生。",
      highlight: true,
    },
    {
      n: 5,
      head: "沉淀",
      sub: "What stays after",
      body: "Knowledge 模块 + Project 成果页（\"项目即简历\"的载体）+ 跨项目弱链接（paper / 老师 / 话题汇聚，轻量研究图谱）。",
    },
    {
      n: 6,
      head: "汇报 / 评审",
      sub: "External evaluation",
      body: "Progress Stream + Milestone + AI 周报（给导师 / 企业 / 学院）+ 老师 / 企业 Dashboard。",
    },
  ];

  const cy = 2.1, ch = 4.7;
  const cw = (W - 1.4 - 0.4) / 3;
  items.forEach((it, i) => {
    const x = 0.7 + i * (cw + 0.2);
    const isStar = it.highlight;
    card(s, { x, y: cy, w: cw, h: ch, fill: isStar ? C.navy : C.cardBg });
    numberBadge(s, {
      x: x + 0.3, y: cy + 0.3, n: it.n, size: 0.8,
      color: isStar ? C.navy : C.navy,
      bg: isStar ? C.accent : C.ice,
    });
    s.addText(it.sub.toUpperCase(), {
      x: x + 1.3, y: cy + 0.4, w: cw - 1.5, h: 0.35,
      fontFace: FONT.body, fontSize: 10, bold: true,
      color: isStar ? C.accent : C.mute, charSpacing: 5, margin: 0,
    });
    s.addText(it.head, {
      x: x + 1.3, y: cy + 0.7, w: cw - 1.5, h: 0.5,
      fontFace: FONT.head, fontSize: 22, bold: true,
      color: isStar ? C.white : C.navy, margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: x + 0.3, y: cy + 1.5, w: cw - 0.6, h: 0.02,
      fill: { color: isStar ? "3A4488" : C.rule }, line: { width: 0 },
    });
    s.addText(it.body, {
      x: x + 0.3, y: cy + 1.65, w: cw - 0.6, h: ch - 1.9,
      fontFace: FONT.body, fontSize: 13,
      color: isStar ? C.ice : C.ink, paraSpaceAfter: 4, margin: 0,
    });
  });

  s.addNotes("后三环讲'做和交付'。动手这一环 ⭐ 是我们的新核心 —— AI coding 内置。这也是和阿里的主要合作点（他们的 coding plan）。一旦学生每人有一个 coder + Claude 实例在项目里，GitHub + Notion 组合的替代性就出现了。沉淀这里的\"项目即简历\"是对外叙事的关键 —— 学生找实习、读研、投奖，直接展示他在知是上的项目页。汇报这一环就是原来 plan.md 的子模块，进度管理、AI 周报、Dashboard —— 这些不变，只是在新主轴里它们是第 6 环，不是全部。");
}

// -----------------------------------------------------------------------------
// Slide 12 · Part Ⅲ divider
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("PART Ⅲ", {
    x: 0.9, y: 2.0, w: 10, h: 0.6,
    fontFace: FONT.body, fontSize: 18, bold: true, color: C.accent, charSpacing: 14, margin: 0,
  });
  s.addText("战略 insight", {
    x: 0.9, y: 2.6, w: 12, h: 1.4,
    fontFace: FONT.head, fontSize: 60, bold: true, color: C.white, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.9, y: 4.2, w: 1.0, h: 0.08, fill: { color: C.ice }, line: { color: C.ice, width: 0 },
  });
  s.addText("知是在做一件更大的事 ——", {
    x: 0.9, y: 4.4, w: 12, h: 0.5,
    fontFace: FONT.head, fontSize: 22, color: C.ice, margin: 0,
  });
  s.addText("广义科研协作过程即训练数据", {
    x: 0.9, y: 4.9, w: 12, h: 0.6,
    fontFace: FONT.head, fontSize: 28, bold: true, color: C.white, margin: 0,
  });
  s.addNotes("上面讲的是产品方向 —— '知是做什么'。接下来这部分讲 '知是为什么值得做'，是更高层的战略判断。这是我这几个月最想和两位讨论的一个点。");
}

// -----------------------------------------------------------------------------
// Slide 13 · 三个数据缺口
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "现有 AI 训练语料有三个结构性缺口", { kicker: "PART Ⅲ · 战略 insight" });

  s.addText("现在 AI 往 Agent / 研究能力方向打的瓶颈不是模型，是数据。", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 16, color: C.accent, italic: true, margin: 0,
  });

  const gaps = [
    {
      title: "社交信号",
      en: "Social Signals",
      body: "人协作里的微观信号 —— 让步 / 追问 / 推辞 / 把模糊想法推向行动。几乎不在代码和论文里。",
    },
    {
      title: "任务拆解轨迹",
      en: "Task Decomposition",
      body: "从模糊目标到可执行子任务的中间推演，通常只在脑子里，不落到文档。",
    },
    {
      title: "长周期研究 / 项目",
      en: "Long-Horizon Trajectories",
      body: "现有 benchmark 都是几分钟到几小时的短任务。几周到几个月的 trajectory 公开数据集几乎空白 —— 这是 AI 从\"会写代码\"到\"能做科研\"的鸿沟。",
    },
  ];

  const cy = 2.7, ch = 4.2;
  const cw = (W - 1.4 - 0.4) / 3;
  gaps.forEach((g, i) => {
    const x = 0.7 + i * (cw + 0.2);
    card(s, { x, y: cy, w: cw, h: ch });
    // Big number
    s.addText(String(i + 1).padStart(2, "0"), {
      x: x + 0.3, y: cy + 0.2, w: cw - 0.6, h: 1.0,
      fontFace: FONT.head, fontSize: 60, bold: true, color: C.ice, margin: 0,
    });
    s.addText(g.en.toUpperCase(), {
      x: x + 0.3, y: cy + 1.3, w: cw - 0.6, h: 0.35,
      fontFace: FONT.body, fontSize: 10, bold: true, color: C.mute, charSpacing: 5, margin: 0,
    });
    s.addText(g.title, {
      x: x + 0.3, y: cy + 1.6, w: cw - 0.6, h: 0.55,
      fontFace: FONT.head, fontSize: 20, bold: true, color: C.navy, margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, { x: x + 0.3, y: cy + 2.3, w: 0.6, h: 0.04, fill: { color: C.accent }, line: { width: 0 } });
    s.addText(g.body, {
      x: x + 0.3, y: cy + 2.45, w: cw - 0.6, h: ch - 2.6,
      fontFace: FONT.body, fontSize: 12, color: C.ink, margin: 0,
    });
  });

  s.addNotes("这三个数据缺口是我这几个月想得最多的一层。窦老师您做 FlashRAG、CoRAG、Agent，训练数据的瓶颈您应该是最清楚的。现在所有人都在往 long-horizon research agent 方向打，但可公开获得的 trajectory 数据几乎都是短任务。这三层是我拍的，不一定对。一会儿特别想听您判断这个 claim 站不站得住。");
}

// -----------------------------------------------------------------------------
// Slide 14 · 知是承载三类数据
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "知是天然承载这三类数据", { kicker: "PART Ⅲ · 战略 insight" });

  // table
  const tx = 0.7, ty = 2.0, tw = W - 1.4;
  const rows = [
    ["缺口", "知是上的自然产生处"],
    ["社交信号", "Discussion + 评审评论（@mention / reactions / 嵌套）"],
    ["任务拆解", "Milestone + Progress Stream + commit + AI coding session 指令轨迹"],
    ["长周期研究 / 项目", "Project 6 环节全生命周期 + 跨学期延续"],
  ];
  const table = rows.map((row, i) => {
    const isHead = i === 0;
    return row.map((cell, j) => ({
      text: cell,
      options: {
        fontFace: FONT.body,
        fontSize: isHead ? 12 : 13,
        bold: isHead || j === 0,
        color: isHead ? C.white : (j === 0 ? C.navy : C.ink),
        fill: { color: isHead ? C.navy : (i % 2 === 0 ? C.cardBg : C.white) },
        align: "left",
        valign: "middle",
        margin: [6, 12, 6, 12],
      },
    }));
  });
  s.addTable(table, {
    x: tx, y: ty, w: tw,
    colW: [4.0, tw - 4.0],
    rowH: 0.55,
    border: { type: "solid", color: C.rule, pt: 0.5 },
  });

  // Key insight box
  const ky = 4.55, kh = 0.8;
  card(s, { x: tx, y: ky, w: tw, h: kh, fill: C.navy });
  s.addShape(pres.shapes.RECTANGLE, { x: tx, y: ky, w: 0.1, h: kh, fill: { color: C.accent }, line: { width: 0 } });
  s.addText("关键：这些是用户正常使用的副产品 —— 不需要打标、不需要改工作流。", {
    x: tx + 0.35, y: ky + 0.15, w: tw - 0.5, h: kh - 0.3,
    fontFace: FONT.head, fontSize: 16, bold: true, color: C.white, valign: "middle", margin: 0,
  });

  // 3 live samples row
  const ly = 5.6;
  s.addText("活体样本已经在产生", {
    x: tx, y: ly, w: tw, h: 0.4,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  const samples = [
    { tag: "梯队", body: "大一大二在知是上做知是" },
    { tag: "eTrip", body: "企业赛题跨周 trajectory" },
    { tag: "高瓴 pilot", body: "paper-driven trajectory（争取中）" },
  ];
  const sy2 = 6.0, sw = (tw - 0.4) / 3;
  samples.forEach((sp, i) => {
    const x = tx + i * (sw + 0.2);
    card(s, { x, y: sy2, w: sw, h: 0.95, fill: C.cardBg });
    s.addText(sp.tag, {
      x: x + 0.2, y: sy2 + 0.1, w: sw - 0.4, h: 0.35,
      fontFace: FONT.head, fontSize: 13, bold: true, color: C.accent, margin: 0,
    });
    s.addText(sp.body, {
      x: x + 0.2, y: sy2 + 0.45, w: sw - 0.4, h: 0.45,
      fontFace: FONT.body, fontSize: 11, color: C.ink, margin: 0,
    });
  });

  s.addNotes("你看，这三类稀缺数据在知是上都是副产品 —— 学生正常做项目，数据就在。不需要打标、不需要改工作流。活体样本三块：梯队、eTrip、以及未来高瓴博士生 pilot。最后这条我特别想和窦老师讨论可行性。讲到这里也是跟王老师的一个自然衔接 —— 下一页讲对阿里合作的定位升级。");
}

// -----------------------------------------------------------------------------
// Slide 15 · 对阿里合作定位升级
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "对阿里合作的定位升级", { kicker: "PART Ⅲ · 战略 insight" });

  s.addText("从 \"用他们的 coding plan\" → \"共同构建数据飞轮\"", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 18, color: C.accent, bold: true, italic: true, margin: 0,
  });

  // Before / After
  const cy = 2.8, cw = 5.7, ch = 3.2;
  const bx = 0.7, ax = W - 0.7 - cw;

  card(s, { x: bx, y: cy, w: cw, h: ch, fill: C.cardBg });
  s.addText("ORIGINAL FRAMING", {
    x: bx + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  s.addText("我们用阿里的 coder + Claude", {
    x: bx + 0.3, y: cy + 0.75, w: cw - 0.6, h: 0.7,
    fontFace: FONT.head, fontSize: 22, bold: true, color: C.ink, margin: 0,
  });
  s.addText("关系：客户 ↔ 供应商。", {
    x: bx + 0.3, y: cy + 1.8, w: cw - 0.6, h: 1.0,
    fontFace: FONT.body, fontSize: 13, color: C.mute, margin: 0,
  });

  s.addText("→", {
    x: bx + cw - 0.1, y: cy + ch / 2 - 0.3, w: (ax - bx - cw) + 0.2, h: 0.6,
    fontFace: FONT.head, fontSize: 36, bold: true, color: C.accent, align: "center", valign: "middle", margin: 0,
  });

  card(s, { x: ax, y: cy, w: cw, h: ch, fill: C.navy });
  s.addText("NEW FRAMING", {
    x: ax + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.accent, charSpacing: 6, margin: 0,
  });
  s.addText("为下一代 AI 的 research / Agent 能力生产稀缺训练数据", {
    x: ax + 0.3, y: cy + 0.75, w: cw - 0.6, h: 1.3,
    fontFace: FONT.head, fontSize: 18, bold: true, color: C.white, margin: 0,
  });
  s.addText("关系：合作方 ↔ 合作方。阿里 coding plan 是数据飞轮的一部分。", {
    x: ax + 0.3, y: cy + 2.2, w: cw - 0.6, h: 0.9,
    fontFace: FONT.body, fontSize: 13, color: C.ice, margin: 0,
  });

  // Bottom
  const fy = 6.3;
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: fy, w: 0.08, h: 0.5, fill: { color: C.accent }, line: { width: 0 } });
  s.addText("更平视、更可持续 —— 想请王老师从孵化器视角评估这个商业 framing 能不能立住。", {
    x: 0.9, y: fy, w: W - 1.6, h: 0.5,
    fontFace: FONT.body, fontSize: 12, color: C.mute, italic: true, valign: "middle", margin: 0,
  });

  s.addNotes("这是我想请王老师评估的一个商业 framing。目前和阿里的合作是我们用他们的 coding plan。这是个'客户-供应商'关系。但如果 §数据缺口 这个判断成立，我们其实是在为他们（以及国内 AI 基础设施）生产稀缺训练数据。这就不是客户关系，是合作方关系。这个 reframe 在商业上能不能立住，王老师您从孵化器的视角看 —— 值得在对阿里的陈述里推这个叙事吗？");
}

// -----------------------------------------------------------------------------
// Slide 16 · Part Ⅳ divider
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("PART Ⅳ", {
    x: 0.9, y: 2.6, w: 10, h: 0.6,
    fontFace: FONT.body, fontSize: 18, bold: true, color: C.accent, charSpacing: 14, margin: 0,
  });
  s.addText("接下来的 P0", {
    x: 0.9, y: 3.2, w: 12, h: 1.4,
    fontFace: FONT.head, fontSize: 60, bold: true, color: C.white, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.9, y: 4.8, w: 1.0, h: 0.08, fill: { color: C.ice }, line: { color: C.ice, width: 0 },
  });
  s.addText("近期执行 · 长期雄心 · 个人位置", {
    x: 0.9, y: 5.0, w: 12, h: 0.6,
    fontFace: FONT.head, fontSize: 20, color: C.ice, italic: true, margin: 0,
  });
  s.addNotes("最后一部分讲接下来我打算做什么。两个方向：近期执行的 P0，以及更长时期的雄心 + 我个人的位置。");
}

// -----------------------------------------------------------------------------
// Slide 17 · 回国后 P0 表格
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "回国后的 P0（前两个月）", { kicker: "PART Ⅳ · 接下来的 P0" });

  const tx = 0.7, ty = 2.1, tw = W - 1.4;
  const rows = [
    ["事项", "梯队承担", "我主抓"],
    ["Project 归属 Space 的架构补丁", "技术实现", "设计决策"],
    ["成员管理 + 项目进度记录（原 plan 子模块）", "实现", "架构把关"],
    ["微人大登录", "技术对接 + 审批", "产品对接"],
    ["AI coding 内置（阿里 coding plan 接入后）", "集成", "架构 + 合作对接"],
    ["高瓴 pilot 争取", "—", "请窦老师推动"],
  ];
  const table = rows.map((row, i) => {
    const isHead = i === 0;
    return row.map((cell, j) => ({
      text: cell,
      options: {
        fontFace: FONT.body,
        fontSize: isHead ? 12 : 13,
        bold: isHead || j === 0,
        color: isHead ? C.white : (j === 0 ? C.navy : C.ink),
        fill: { color: isHead ? C.navy : (i % 2 === 0 ? C.cardBg : C.white) },
        align: j === 0 ? "left" : "center",
        valign: "middle",
        margin: [6, 12, 6, 12],
      },
    }));
  });
  s.addTable(table, {
    x: tx, y: ty, w: tw,
    colW: [6.5, 3.0, 2.4],
    rowH: 0.6,
    border: { type: "solid", color: C.rule, pt: 0.5 },
  });

  const fy = 6.15;
  card(s, { x: tx, y: fy, w: tw, h: 0.8, fill: C.cardBg });
  s.addShape(pres.shapes.RECTANGLE, { x: tx, y: fy, w: 0.1, h: 0.8, fill: { color: C.accent }, line: { width: 0 } });
  s.addText("每条都带 \"梯队做 X / 我主抓 Y\"—— 人手不是问题，关键是方向与合作对接。", {
    x: tx + 0.35, y: fy + 0.15, w: tw - 0.5, h: 0.55,
    fontFace: FONT.body, fontSize: 13, color: C.ink, valign: "middle", margin: 0,
  });

  s.addNotes("每条我都带了'梯队做 X、我主抓 Y'—— 人手这层不是问题。其中一件需要窦老师帮忙：高瓴 pilot 我希望和您一起推。");
}

// -----------------------------------------------------------------------------
// Slide 18 · 雄心升级
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "雄心 · 从 \"信院支撑\" → \"校级官方创研课系统\"", { kicker: "PART Ⅳ · 长期方向" });

  // Before → after
  const cy = 2.6, cw = 5.7, ch = 2.8;
  const bx = 0.7, ax = W - 0.7 - cw;
  card(s, { x: bx, y: cy, w: cw, h: ch, fill: C.cardBg });
  s.addText("NOW", {
    x: bx + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  s.addText("信院内部创研课支撑工具", {
    x: bx + 0.3, y: cy + 0.75, w: cw - 0.6, h: 0.7,
    fontFace: FONT.head, fontSize: 22, bold: true, color: C.ink, margin: 0,
  });
  s.addText("已有公司实体 · 和阿里非合同式合作（coding plan）", {
    x: bx + 0.3, y: cy + 1.7, w: cw - 0.6, h: 0.9,
    fontFace: FONT.body, fontSize: 13, color: C.mute, margin: 0,
  });

  s.addText("→", {
    x: bx + cw - 0.1, y: cy + ch / 2 - 0.3, w: (ax - bx - cw) + 0.2, h: 0.6,
    fontFace: FONT.head, fontSize: 36, bold: true, color: C.accent, align: "center", valign: "middle", margin: 0,
  });

  card(s, { x: ax, y: cy, w: cw, h: ch, fill: C.navy });
  s.addText("NEXT", {
    x: ax + 0.3, y: cy + 0.3, w: cw - 0.6, h: 0.35,
    fontFace: FONT.body, fontSize: 10, bold: true, color: C.accent, charSpacing: 6, margin: 0,
  });
  s.addText("跨学院 · 校级 · 官方创研课系统", {
    x: ax + 0.3, y: cy + 0.75, w: cw - 0.6, h: 0.7,
    fontFace: FONT.head, fontSize: 22, bold: true, color: C.white, margin: 0,
  });
  s.addText("创研课是校级的事情 —— 理论上应该全校一套系统。", {
    x: ax + 0.3, y: cy + 1.7, w: cw - 0.6, h: 0.9,
    fontFace: FONT.body, fontSize: 13, color: C.ice, margin: 0,
  });

  // Required bridges
  const ry = 5.7;
  s.addText("需要打通的关节", {
    x: 0.7, y: ry, w: W - 1.4, h: 0.35,
    fontFace: FONT.body, fontSize: 11, bold: true, color: C.mute, charSpacing: 6, margin: 0,
  });
  const bridges = ["跨学院协调", "教务层认定", "校级资源 / 审批"];
  const chipW = 3.6, chipGap = 0.3;
  const totalChipsW = 3 * chipW + 2 * chipGap;
  const chipStartX = (W - totalChipsW) / 2;
  bridges.forEach((b, i) => {
    const x = chipStartX + i * (chipW + chipGap);
    chip(s, { x, y: ry + 0.45, w: chipW, text: b });
  });

  s.addNotes("这是我想和王老师重点聊的。知是现在定位还是信院内的工具。但创研课是一个校级的事情，理论上应该全校一套系统，而不是每个学院搞一套。王老师您在孵化器，对这种'从学院内 → 校级'的升级路径应该见过不少。哪些关节要先打通？跨学院协调 / 教务层认定 / 校级审批，您觉得最该先搞哪个？");
}

// -----------------------------------------------------------------------------
// Slide 19 · 个人 / PhD
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "我个人 · incoming PhD", { kicker: "PART Ⅳ · 个人位置" });

  s.addText("怎么让知是在我非日常在场时继续做大？", {
    x: 0.7, y: 1.85, w: W - 1.4, h: 0.5,
    fontFace: FONT.head, fontSize: 18, color: C.accent, bold: true, italic: true, margin: 0,
  });

  // Background card
  const fy = 2.6, fw = W - 1.4, fh = 1.3;
  card(s, { x: 0.7, y: fy, w: fw, h: fh, fill: C.navy });
  s.addText([
    { text: "秋季去 Princeton 读 PhD", options: { bold: true, color: C.white } },
    { text: "，advisor 是 ", options: { color: C.ice } },
    { text: "Tri Dao", options: { bold: true, color: C.accent } },
    { text: "（FlashAttention / Mamba 方向）。", options: { color: C.ice } },
  ], {
    x: 0.9, y: fy + 0.2, w: fw - 0.4, h: 0.5,
    fontFace: FONT.head, fontSize: 18, margin: 0, valign: "middle",
  });
  s.addText("PhD research 本身就是 long-horizon 工作 —— 全职投知是不现实。这是既定约束。", {
    x: 0.9, y: fy + 0.7, w: fw - 0.4, h: 0.5,
    fontFace: FONT.body, fontSize: 13, color: C.ice, italic: true, margin: 0,
  });

  // Three real questions
  const qy = 4.2, qh = 2.5;
  card(s, { x: 0.7, y: qy, w: W - 1.4, h: qh, fill: C.cardBg });
  s.addText("所以真问题不是 \"投多少时间\"，而是 ——", {
    x: 1.0, y: qy + 0.2, w: W - 2.0, h: 0.4,
    fontFace: FONT.body, fontSize: 12, color: C.mute, italic: true, margin: 0,
  });

  const questions = [
    "知是如何从 \"我 + 梯队 + 顾问\" 的形态扩到不依赖我日常在场？",
    "什么时候该加专职产品经理 / BD / 运营？接力的人从哪来？",
    "公司 / 学院 / 阿里合作这几条关系如何在入学前稳定？",
  ];
  questions.forEach((q, i) => {
    const qx = 1.0, qtop = qy + 0.65 + i * 0.55;
    s.addShape(pres.shapes.OVAL, {
      x: qx, y: qtop + 0.05, w: 0.35, h: 0.35,
      fill: { color: C.navy }, line: { color: C.navy, width: 0 },
    });
    s.addText(String(i + 1), {
      x: qx, y: qtop + 0.05, w: 0.35, h: 0.35,
      fontFace: FONT.head, fontSize: 12, bold: true, color: C.white,
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(q, {
      x: qx + 0.5, y: qtop, w: W - 2.0 - 0.5, h: 0.45,
      fontFace: FONT.body, fontSize: 14, color: C.ink, valign: "middle", margin: 0,
    });
  });

  // Footer
  const ff = 6.9;
  s.addText("梯队 + 重构这两件机制性基建本来就是为这场景设计的 —— 但到下一阶段 (校级系统、产品化团队) 谁接、怎么接，是今天最想听两位判断的。", {
    x: 0.7, y: ff, w: W - 1.4, h: 0.45,
    fontFace: FONT.body, fontSize: 11, color: C.mute, italic: true, margin: 0,
  });

  s.addNotes("最后一张是我个人的事。我马上毕业，秋季去 Princeton 读 PhD，advisor 是 Tri Dao —— 做 FlashAttention、Mamba 那位。研究方向其实和 §6 讲的 long-horizon / efficient LLM 很近，两边能互相借力。但这意味着我不可能全职投知是 —— PhD 负荷摆在那里。所以我今天想和两位讨论的真问题是：知是在我不能日常 in the trenches 的情况下怎么继续生长。梯队和重构这两件事其实就是为这场景准备的 —— 有人手、有 AI-friendly 基建、有可授权的结构。但到下一阶段（校级系统、产品化团队、商业关系），谁来接、怎么接，是王老师最能帮我的。窦老师也想请教一句 —— 您带过的学生里，有一边做 PhD research 一边带产品/创业做得不错的吗？节奏上您有什么建议？讲到这里我就讲完了，请两位指教。");
}

// -----------------------------------------------------------------------------
// Slide 20 · 备查
// -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "LIGHT" });
  titleBlock(s, "备查", { kicker: "APPENDIX" });

  const items = [
    { k: "详细产品方向", v: "docs/vision.md (v0.7)" },
    { k: "讲稿底稿", v: "docs/talk.md" },
    { k: "eTrip 公网版", v: "http://ruc-etrip.cn/spaces" },
    { k: "GitHub", v: "github.com/SageSeekerSociety/cheese-backend-py" },
    { k: "窦老师邮箱", v: "dou@ruc.edu.cn" },
  ];

  const iy = 2.4, ih = 0.75, iw = W - 1.4;
  items.forEach((it, i) => {
    const y = iy + i * (ih + 0.12);
    card(s, { x: 0.7, y, w: iw, h: ih, fill: C.cardBg });
    s.addText(it.k, {
      x: 0.95, y, w: 3.8, h: ih,
      fontFace: FONT.body, fontSize: 12, bold: true, color: C.navy, valign: "middle", margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, { x: 4.8, y: y + 0.15, w: 0.02, h: ih - 0.3, fill: { color: C.rule }, line: { width: 0 } });
    s.addText(it.v, {
      x: 5.0, y, w: iw - 4.3, h: ih,
      fontFace: FONT.mono, fontSize: 13, color: C.ink, valign: "middle", margin: 0,
    });
  });

  s.addNotes("这一页不讲，作为手上的提示条 / 如果老师问起可以递 URL。");
}

// -----------------------------------------------------------------------------
// Write
// -----------------------------------------------------------------------------
await pres.writeFile({ fileName: OUTPUT });
console.log(`wrote ${OUTPUT}`);
