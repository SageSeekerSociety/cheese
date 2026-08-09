const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
  Header, Footer, PageNumber
} = require("docx");

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 300, after: 150 }, children: [new TextRun({ text, bold: true, size: 28, font: "Microsoft YaHei" })] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 }, children: [new TextRun({ text, bold: true, size: 24, font: "Microsoft YaHei" })] });
}
function p(text, opts = {}) {
  return new Paragraph({ spacing: { after: 80, line: 276 }, children: [new TextRun({ text, size: 21, font: "Microsoft YaHei", ...opts })] });
}
function bold(text) { return p(text, { bold: true }); }
function cell(text, width, shade) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    margins: cellMargins,
    shading: shade ? { fill: shade, type: ShadingType.CLEAR } : undefined,
    children: [new Paragraph({ children: [new TextRun({ text, size: 20, font: "Microsoft YaHei" })] })]
  });
}
function headerCell(text, width) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    margins: cellMargins,
    shading: { fill: "2B579A", type: ShadingType.CLEAR },
    children: [new Paragraph({ children: [new TextRun({ text, size: 20, font: "Microsoft YaHei", bold: true, color: "FFFFFF" })] })]
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Microsoft YaHei", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Microsoft YaHei" },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Microsoft YaHei" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 }
      }
    },
    headers: {
      default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "知是（CheeseX）| 科技成果转化项目", size: 16, font: "Microsoft YaHei", color: "999999" })] })] })
    },
    footers: {
      default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], size: 16, font: "Microsoft YaHei", color: "999999" })] })] })
    },
    children: [
      // Title
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "知是（CheeseX）", size: 36, bold: true, font: "Microsoft YaHei" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "AI 时代实践育人的基础设施", size: 26, font: "Microsoft YaHei", color: "2B579A" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: "中国人民大学信息学院 | 科技成果转化项目", size: 20, font: "Microsoft YaHei", color: "666666" })] }),

      // 一、愿景
      h1("一、愿景"),
      p("未来的学习不是听课，不是刷视频，是带着 AI 队友做真实项目。"),
      p("AI 正在重新定义「做事」的方式。写代码、做 PPT、搜文献这些个人执行技能正被 AI 快速替代，真正稀缺的能力是：判断 AI 做得对不对、把想法推进到结果、和团队一起把事做成。这些能力只有在真实项目中练出来——不是课堂教的，不是考试考的。"),
      p("2025 年达沃斯年会主题「智能时代的协作」（Collaboration for the Intelligent Age），WEF《未来就业报告》把协作列为第二核心技能。知是要做的就是让这件事发生的基础设施——让每个学生在真实项目中和 AI 一起成长。"),

      // 二、政策对齐
      h1("二、政策对齐"),
      p("国发〔2025〕11 号明确「育人从知识传授为重向能力提升为本转变」。教科信〔2026〕1 号（五部门联发）提出「推动项目式、探究式、场景式育人」「研发智能学伴」「建设未来学习中心」。复旦校长金力倡议「让学生借助 AI 新范式，真刀真枪探索科学前沿」。"),
      p("知是不是追政策热点——产品本身就长在「实践育人 + AI 赋能」这个交叉点上。知是就是「未来学习中心」的基础设施，AI 队友就是最先落地的「智能学伴」。"),

      // 三、问题与方案
      h1("三、问题与方案"),
      p("高校学生的工作单元是「项目」，但没有任何产品围绕它设计。协作散落在微信群和飞书文档里；AI 是临时工，每次从头交代背景；做了一学期项目只剩结题报告；老师对过程一无所知只能催周报。这是同一个结构性缺陷：缺少围绕「学生项目」设计的 AI 原生基础设施。"),
      bold("知是的三个核心词：AI、全过程、一同成长。"),
      p("AI 让全过程成为可能——工作在平台上发生，过程自然留痕，不用填表。全过程让 AI 真正有用——持续参与、持续记忆，不是临时工而是了解全局的队友。一同成长是意义——AI 在成长（记忆和理解越来越深）、人和 AI 一起成长（练判断力和执行力）、人和人一起成长（练协作和领导力）。"),

      // 角色价值表
      new Table({
        width: { size: 9506, type: WidthType.DXA },
        columnWidths: [1500, 8006],
        rows: [
          new TableRow({ children: [headerCell("角色", 1500), headerCell("价值", 8006)] }),
          new TableRow({ children: [cell("学生", 1500), cell("AI 队友全程陪伴；项目过程自动变成可验证的简历", 8006)] }),
          new TableRow({ children: [cell("老师", 1500), cell("不催周报不猜进度，平台自动汇总所有项目组状态", 8006)] }),
          new TableRow({ children: [cell("机构", 1500), cell("过程化管理零负担，数据看板提供真实的教学成效洞察", 8006)] }),
        ]
      }),
      new Paragraph({ spacing: { after: 100 }, children: [] }),

      // 四、市场
      h1("四、市场机会"),
      p("中国高校在校本科生约 3,700 万，每年毕业生约 800 万，每人在校期间参与数十个项目。围绕「学生项目」的 AI 基础设施是一个尚未被定义的新品类——不是教育信息化（管理为核心），不是在线教育（内容为核心），而是以实践过程为核心。"),
      p("切入路径：从人大校内已验证场景（信息学院创研课、明理书院创新项目、校企合作）出发，扩展到海淀区高校，再向全国复制。"),

      // 五、商业模式
      h1("五、商业模式"),
      h2("面向一：ToB 实践育人解决方案"),
      p("核心客户是普通高校——最缺导师资源、最缺项目指导能力。知是提供的不是工具，是「平台 + AI 队友 + 场景内容 + 数据看板」的整体方案。AI 队友能推荐有价值的题目、全程指导、自动评审，在很多场景下比现有导师指导更系统、更持续。根据学校专业特点，从积累的项目数据中筛选适配题目，打包输出。按机构和学生数订阅。"),

      h2("面向二：教育 AI 模型"),
      p("知是上产生的数据极其稀缺：真实的人机协作轨迹、项目拆解推演、长周期协作数据。知是不卖数据——用这些数据训练教育 AI、项目管理 AI、学术协作 AI 等领域模型。数据是护城河，模型是产品。数据让模型更好，模型让平台更好，平台产生更多数据——正向飞轮。"),

      // 六、竞争
      h1("六、竞争分析"),
      new Table({
        width: { size: 9506, type: WidthType.DXA },
        columnWidths: [2000, 2000, 5506],
        rows: [
          new TableRow({ children: [headerCell("类别", 2000), headerCell("代表", 2000), headerCell("为什么做不了", 5506)] }),
          new TableRow({ children: [cell("协作工具", 2000), cell("飞书/钉钉", 2000), cell("围绕「组织」设计，跨组织学生项目与其商业模式冲突", 5506)] }),
          new TableRow({ children: [cell("代码平台", 2000), cell("GitHub", 2000), cell("围绕「代码」设计，覆盖不到非代码类项目", 5506)] }),
          new TableRow({ children: [cell("通用 AI", 2000), cell("ChatGPT 等", 2000), cell("围绕「单次对话」设计，没有项目级持续记忆", 5506)] }),
          new TableRow({ children: [cell("教育信息化", 2000), cell("教务系统", 2000), cell("围绕「管理」设计，服务行政流程不是学生工作", 5506)] }),
        ]
      }),
      new Paragraph({ spacing: { after: 80 }, children: [] }),
      p("结构性护城河：产品架构围绕「学生项目」从底层设计，不可追赶；学生身份跨学期跨项目持续积累，时间壁垒；独有的长周期协作数据训练模型，数据壁垒。"),

      // 七、团队
      h1("七、团队"),
      p("12 位跨专业成员（计算机、工商管理、数据科学、数字媒体、金融），源自中国人民大学信息学院。三位互补导师：信息学院教授（技术）、高校孵化器副总（商业化）、学院院长（教育场景）。"),
      p("团队自身在知是上协作开发知是（dogfooding），AI 驱动开发月产 6-7 个高质量迭代。公司已完成工商注册。"),

      // 八、路线图
      h1("八、发展路线图"),
      new Table({
        width: { size: 9506, type: WidthType.DXA },
        columnWidths: [2400, 4506, 2600],
        rows: [
          new TableRow({ children: [headerCell("阶段", 2400), headerCell("目标", 4506), headerCell("关键指标", 2600)] }),
          new TableRow({ children: [cell("2026 下半年\n校内验证", 2400), cell("AI 队友核心能力升级；信息学院、明理书院、校企合作场景规模化部署", 4506), cell("覆盖率≥60%\n周活≥40%", 2600)] }),
          new TableRow({ children: [cell("2027 年\n海淀扩展", 2400), cell("向海淀区 2-3 所高校输出解决方案；建立标准化交付；启动模型训练", 4506), cell("合作高校≥3\n首个模型发布", 2600)] }),
          new TableRow({ children: [cell("2027-2028\n规模化", 2400), cell("成为高校实践育人默认基础设施；教育 AI 模型对外输出", 4506), cell("全国覆盖\n模型商业化", 2600)] }),
        ]
      }),
      new Paragraph({ spacing: { after: 100 }, children: [] }),

      // 九、社会价值
      h1("九、社会价值"),
      p("让实践育人真正落地——用 AI 解决「老师带不过来、过程管不住、成果留不下」三个问题，让「项目式育人」第一次有规模化落地的可能。"),
      p("缩小教育资源鸿沟——985 有顶尖导师，普通高校没有。知是的 AI 队友为每个学生提供高水平全程指导，优质项目指导不再是少数学校的特权。"),
      p("重新定义人才评价——从自述型简历进化到过程可验证的能力档案。"),

      // 结语
      new Paragraph({ spacing: { before: 200 }, children: [new TextRun({ text: "未来的学习是实践，知是是实践发生的地方。", size: 24, bold: true, font: "Microsoft YaHei", color: "2B579A" })] }),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("/Users/andyl/Projects/cheese-backend-py/tmp/cheesex/docs/bp.docx", buffer);
  console.log("BP saved to docs/bp.docx");
});
