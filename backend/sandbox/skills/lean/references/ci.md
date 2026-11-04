# 给 Lean 项目配 CI

目标：目标项目的 PR 上出现一个名叫 `lean` 的 check run。它只在改动碰到 Lean 文件时才跑，平台读这个 check 决定「这条必跑检查过了没有」。

## 抄这份工作流

把 `references/lean.yml` 整个复制到**目标项目**的 `.github/workflows/lean.yml`。它是可直接用的完整工作流，别再手抄一份。它做三件事：`paths` 粗筛、跑 `lean-action` 构建、`axiom-audit` 让带 `sorry` 的证明红。

复制后按项目自己改这几处：

- **作业名 `name: lean` 就是 check run 名**，平台 `required_checks` 按这个名字找它；改名要同时改那边。
- `runs-on`：用平台自带 runner 就换成对应标签，GitHub 托管机留 `ubuntu-latest`。
- **mathlib 依赖**：`lean-action` 会自动认 mathlib 并跑 `lake exe cache get`；要显式控制就加 `use-mathlib-cache: true`。
- **有 sorry 就红**：`axiom-audit: true` 检查每条声明依赖的公理在不在白名单，默认白名单 `propext,Classical.choice,Quot.sound` 不含 `sorryAx`，所以留了 `sorry` 会报。也可以自己加一步 `grep -i sorry`；二选一，别一种都不做。
- 仓库根放一个 `lean-toolchain`（内容如 `leanprover/lean4:v4.35.0`），`lean-action` 按它装版本。

## required_checks 里的路径怎么写

在项目设置的分支保护里，必跑检查是一条 `{"name": "lean", "paths": [...]}`。`paths` 是「改动碰到这些才必须过」；**空数组＝每次都必需**。

**必须知道的坑**：平台的匹配器里 `**` 跨目录、`*` 不跨目录，所以 **`**/*.lean` 匹配不到仓库根目录下的 `Foo.lean`**（它要求至少一层目录）。项目在根目录放 `.lean` 文件时，根目录也要覆盖：

```json
{"name": "lean", "paths": ["**/*.lean", "*.lean", "lakefile.toml", "lean-toolchain"]}
```

（模板里的 `on.paths` 两条都写了，是同一个道理：GitHub 自己的路径过滤和这个坑的判断方式不一样，两边都盖住最稳。）

不放心就核实际效果：改一个**根目录**的 `.lean` 文件提 PR，看 `lean` 这个 check 有没有出现在 PR 上。**没出现＝路径没盖到，不是检查过了。**

另一头也要防：`paths` 写太宽（或不写）会让每次改动都要求这个检查过，而它跟改动无关。

## 怎么确认它真的在生效

1. PR 上能看到一个叫 `lean` 的 check，绿或红——而不是「没出现」。
2. 故意在某个 `.lean` 文件里插一句 `sorry` 推上去，它应该变红。这是唯一能证明「检查真的在看证明完不完整」的办法——因为带 `sorry` 的文件是编译得过的（本地实测：`lake build` 退出码 0），只做编译的检查拦不住它。配好之后做一次。
3. 改一个跟 Lean 无关的文件，它不该挡（不出现，或显示 skipped）。
