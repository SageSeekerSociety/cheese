# 第一版测量：离线复算说明

报告和输入快照已摆在任务 #1549 的文件区。代码固定在 PR #3785 的提交 `e13cbbb7cecc900e40a86b9fd88207341497f530`。这是测量切片，未完成优化、完整 CI 和最终审查，不用于验收。

## 输入文件

- `measurement/source/cohort.json`：原 90 条首提交至首卡窗口，含两条时序异常。
- `measurement/platform-v2/evidence-part-1.json` 至 `evidence-part-4.json`：43,980 条脱敏工具调用记录，按上传大小上限拆分；每片保留同一份来源与覆盖说明。
- `measurement/ci/evidence.json`：5,219 条 GitHub 检查观测。
- `measurement/report/result.json`：参考输出，用于核对复算结果。

JSON 文件保留原文件名，可下载。预览按文本展示，不影响 JSON 内容。未公开原始命令或输出。

## 复算步骤

在上述固定提交的仓库根目录，将输入文件放在同名路径下，再合并四片工具记录：

```python
import json
from pathlib import Path

folder = Path('measurement/platform-v2')
parts = [json.loads((folder / f'evidence-part-{i}.json').read_text())
         for i in range(1, 5)]
assert all(p['provenance'] == parts[0]['provenance'] for p in parts)
assert all(p['coverage'] == parts[0]['coverage'] for p in parts)
data = {**parts[0], 'observations': [x for p in parts for x in p['observations']]}
assert len(data['observations']) == 43980
(folder / 'evidence.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
```

生成独立输出，不覆盖参考结果：

```bash
python3 .claude/scripts/commit_card_report.py \
  measurement/source/cohort.json \
  measurement/platform-v2/evidence.json \
  measurement/ci/evidence.json \
  --out measurement/recomputed
```

输出应为 88 条有效窗口、9733.10 分钟；两条无效窗口另列。原 90 条的有符号合计仍为 9717.9495743 分钟，不能与有效窗口分母混用。独占分钟及跨阶段重叠分钟合计等于有效窗口总时长；含重叠分钟不能相加。

`combined-evidence.json` 和汇总文件可由以上输入重新生成，无需依赖沙箱里未备份的大文件。压缩归档不支持当前预览格式，因此发布的是可复算的 JSON 分片，没有发布压缩包。
