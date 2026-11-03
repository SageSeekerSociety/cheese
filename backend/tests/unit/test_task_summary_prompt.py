"""收尾那一轮的平台指令是给平台上每一个项目的。

某个项目自己的收尾流程（比如把每次上线抄进自己的登记表）写在那个项目仓库的
AGENTS.md 里，不写进这段指令：写进来，所有不相干的项目收尾时都会照着发
（docs/agent-principles.md 第八条）。
"""

from app.domain.review.task_landing import task_summary_prompt


def test_the_write_up_every_project_gets_asks_for_no_project_record():
    text = task_summary_prompt(title="改动页默认折叠")

    assert "留痕" not in text
