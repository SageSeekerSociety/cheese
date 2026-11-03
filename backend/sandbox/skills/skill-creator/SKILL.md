---
name: skill-creator
description: 写或改一份技能时先加载：把反复交代的规矩、一套工作流变成一个 SKILL.md，调好它的 name 与 description。
---

# 写一份好技能

<!-- ponytail: 砍掉了原 skill-creator 的 eval-viewer/generate_review.py、定量 benchmark 和 description 优化脚本（独立 Python 工具链，本地跑不动）。第一版只留「生成 + 一条自检」。上限：没有定量评估，description 好不好只能靠真实使用发现。升级路径：把 evals/evals.json 加一个只跑 prompt 的脚本引进来。 -->

技能给一个已经很能干的 agent 补充它没有的、非显然的指导，而不是限制不相关的工作。

## 三条基本规矩

**假设 agent 本来就够用。** 只写会改变它判断或提高它结果的信息。删掉常识、重复的话、想出来的边界情况和不能真正澄清任务的例子。

**触发只靠 description。** frontmatter 里 `name` 和 `description` 是必填，`description` 是唯一的触发机制：技能被加载之前，模型只看得到这一句。说清它到底能做什么、什么时候该用；模型倾向**不触发**，所以描述可以稍微"推"一点、把该用的场合列出来。只有为了防明显的错触发才加排除项，别写成一串万能词去招来无关的请求。

**渐进披露三层。** 元数据（name + description）在选技能时可见 → `SKILL.md` 正文在技能被使用时才加载（正文 **< 500 行**）→ `references/` 等附带资源只在当前任务真需要时读。超过 500 行就再分一层，把某种模式专属的细节挪进 `references/`。一个简单的自足技能不需要路由和多余文件。

## 一个技能长什么样

技能是**一个目录**，里面至少有一个 `SKILL.md`，其余是它工作真正需要的可选资源：

```text
skill-name/
|-- SKILL.md                必填，技能说明
|   |-- YAML frontmatter    必填的 name 和 description
|   `-- Markdown 正文        技能被使用时加载
|-- scripts/                可选，可执行的小工具
|-- references/             可选，按需读的文档
`-- assets/                 可选，用在产出物里的文件
```

按实际任务挑结构。没有明确用途就不要建目录、占位文件、示例或附带的说明文档（**不要**加 README、安装指南、changelog 这类东西）。

### frontmatter 与正文

```yaml
---
name: skill-name
description: 一句说清做什么、什么时候用；相似请求不该触发时写出边界。
---
```

- `name` 必须等于目录名，只用小写字母、数字和连字符，不留开头/结尾/连续的连字符。
- `description` 写能力 + 适用场合。详细的工作流、工具选择、例子放进正文或 references，不要全塞进描述。

正文写成一个 agent 能把这件事做好所需的最少内容：想要的结果、非显然的背景、真正的约束、相关的文件或工具。保留用户明确的选择和已有的授权边界；任务不需要时不要规定死固定的结构、流程或步数。

### scripts / references / assets

- `scripts/`：同一段逻辑要反复重写，或确定性的执行明显更可靠时才放。新脚本或改过的脚本要真的跑一遍验证。
- `references/`：只在特定场合才需要的文档。每一份都要从 `SKILL.md` 里给出入口，并说明什么时候读它；信息只放一处，别在正文和 references 里各抄一份。
- `assets/`：属于产出物、拿来复制或改的文件（模板、图片、字体、脚手架）。不要把 assets 当说明读进去。

## 新建或修改一个技能

按请求调整工作量。新建一个复杂技能可能要看真实用法、挑附带资源、建文件、写说明、验证；对已有技能做个小改动，往往只需一处聚焦的编辑加验证。

只在缺失的信息确实重要、又没法合理推断时才追问。

### 命名

小写字母、数字、连字符；尽量短、面向动作；按工具或领域给命名空间能让它更好被发现；目录名跟技能名一致。

### 创建流程

1. **想清它要处理哪些真实请求**，以及哪些可复用的资源能让这些任务更好做（比如反复的 PDF 转换 → `scripts/`；要搭应用的流程 → 一份 `assets/` 脚手架；数据分析 → `references/schema.md`）。只有具体收益站得住脚才建这些资源。
2. **写 frontmatter**：`name` == 目录名；`description` 一句讲清做什么、何时用、何时不用。
3. **写正文**：先给最核心的流程和真正的约束，正文保持 < 500 行；条件性的细节挪进 `references/` 并从正文链过去。
4. **自检**（见下），再结束。

改已有技能时，除非用户要求，先保留它原来的触发方式和结构。

### 从地址取一份技能

用户给的是一个地址、要的是那份现成的技能时，别照着网址重写一遍。只认 GitHub 的仓库或子目录，以及 `raw.githubusercontent.com` 上 `SKILL.md` 的直链；别的地址就照实说读不了，不要凭网址猜里面的内容。

- 用 `cheese_web(action=fetch)` 去读：目录地址先读它下面的 `SKILL.md`，再逐份读它引用的 `scripts`、`references` 里的文件；直链读回来的就是 `SKILL.md` 本身。
- 按那份自己的 `name` 和 `description` 存（它已经调过触发），正文和配套文件照搬；只有名字对不上目录、`description` 缺失、或正文超过 500 行时才按前面几条改。
- 落法仍是 `cheese_skill(action=propose)`（配套文件走 `files`），由人确认；不要自己写进工作区。
- 读不到就说读不到，那个主机不是每个网络都通；不要拿记忆里的版本来凑。

## 自检（唯一一步）

技能是纯文本，出错的代价是静默的：名字对不上目录、描述太长、正文自己膨胀。做完在技能目录里跑一遍：

```bash
SKILL="<这个技能的目录>"; NAME=$(basename "$SKILL")
grep -qx "name: $NAME" "$SKILL/SKILL.md" && echo "name ok" || echo "name 和目录不一致"
awk -F': ' '/^description:/{print length($2)}' "$SKILL/SKILL.md"   # 要 < 1024
wc -l "$SKILL/SKILL.md"                                             # 正文要 < 500
grep -o 'references/[A-Za-z0-9._/-]*' "$SKILL/SKILL.md" | sort -u | while read r; do
  test -e "$SKILL/$r" || echo "正文引用了不存在的 $r"
done
```

这四条只证明结构和引用没坏，**不证明技能做得好**：description 是否够区分、正文是否守住了用户意图，还要靠真实使用的反馈来改。发现真失败时做一次窄的修正，不要为每个例子堆一条通用规矩。
