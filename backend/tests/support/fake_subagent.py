"""一条子线程，做成可以跑的东西。

结论 43 的四条硬性要求（``harness.SubagentRequirement``）在契约上是四句话，而一个
新骨架没有办法被四句话拦住：得有一样东西说清楚「起了它并指定了模型、它说的话带着
标识、父线程改得了它的指令、父线程停得掉它」分别长什么样。这就是那样东西——
``ContractHarness`` 的会话里起得出它，四条各自是一个可以断言的动作。

它是一个替身，守的是真协议的那几条（结论 59）：

- 起它的是**父线程**，不是平台。平台这一侧没有「派活」的动作（结论 43），所以
  ``ContractHarness.spawn`` 演的是 agent 那次工具调用，不是契约上的第七个动词；
- 标识是起它的时候给的，之后它说的每一句都带着，谁也改不了——``label`` 没有写
  入口。一个报上来的绑定会让「归属是读出来的」变成「归属是报出来的」，而那正是
  这条要求要挡掉的东西；
- 停掉了就不能再说话，也接不了新指令。一个停不住、还在往房间里写的 worker，和一
  个没停的 worker 在时间线上是同一个样子，所以「停掉」这件事只有在它之后什么都做
  不了的时候才成立；
- **不指定模型就起不出来**，而且**指定了哪个是读得出来的**。那一条要求是「起得了
  子 agent **并指定模型**」，两半都要守得住：空模型起不出来，守的是前半；``model``
  和 ``instruction`` 一样有读者（``works()`` 把它带进那条流），守的是后半。少了后
  半，一个收下模型名再跑另一个模型的骨架照样绿——那正是这条要求要挡掉的东西。
- **指令有读者**，同一个坑不能在 ``instruction`` 上再踩一遍。它唯一的读者是
  ``works()``：干出来的那句活带着当前指令出去，所以父线程改没改得动它，是从这条
  流上读出来的，不是把 setter 写回来的那个值再读一遍。把 ``retask`` 的那次赋值拿
  掉，换了要求之后它干的还是原来那件事——测试红。
- **停掉一条不碰另一条**。「父线程能停掉它」停的是**一条子线程**，不是这条会话
  （会话那一层是 ``close``，是结论 43 的另一句「同生同死」）。所以 ``stop`` 是每条
  子线程各自的事，负向对照在同胞身上：停了一条，另一条照样说得出话。
"""

from collections.abc import Callable


class SubagentStopped(RuntimeError):
    """停掉的子线程又说话了。"""


class FakeSubagent:
    """父线程起的一条子线程：一个标识、一个模型、一份指令。"""

    def __init__(
        self,
        *,
        label: str,
        model: str,
        instruction: str,
        say: Callable[[str, str], None],
    ) -> None:
        if not model.strip():
            raise ValueError(
                "起一条子线程要说清楚跑哪个模型（结论 43 的 SPAWNS_WITH_A_MODEL）"
            )
        #: 这条活的线程标识，起它的时候父线程写给它的。只读：见模块说明。
        self.label = label
        #: 跑哪个模型。起子 agent 的那次调用指定，不是会话的属性。读者是
        #: ``works()``：它干出来的活上带着这个名字出去。
        self.model = model
        self.instruction = instruction
        self.running = True
        self._say = say

    def says(self, text: str) -> None:
        """它说了一句话，带着自己的标识进父会话那条流。"""
        if not self.running:
            raise SubagentStopped(f"{self.label} 已经停了")
        self._say(text, self.label)

    def works(self) -> None:
        """按当前指令、用起它时指定的模型干一句活，带着自己的标识进父会话那条流。

        ``instruction`` 和 ``model`` 唯一的读者。换了要求之后这条流上的话跟着换，
        旧那句再也出不来——「父线程改得动它的指令」是这么读出来的，而不是把刚写进
        去的那个值读回来（那只证明 ``retask`` 是个 setter）。``model`` 同理：干出
        来的活上带着它，所以「起它的那次调用指定了跑哪个模型」也是从这条流上读出
        来的，而不是把构造参数读回来。
        """
        self.says(f"在做（{self.model}）：{self.instruction}")

    def retask(self, instruction: str) -> None:
        """父线程改它的指令。

        改的是指令，不是标识：一条活换了要求还是那条活，卡不变。
        """
        if not self.running:
            raise SubagentStopped(f"{self.label} 已经停了")
        self.instruction = instruction

    def stop(self) -> None:
        """父线程停掉它。再说话就是 ``SubagentStopped``。"""
        self.running = False
