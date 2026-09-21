"""pi 适配器。

包门口只放一样东西：行为声明。``capability/matrix.py`` 从三个骨架的门口各取一
份，三个门口长一个样；其余的东西继续从子模块直接取，因为这个包还没有一道
``test_harness_boundary.py`` 那样的门禁。
"""

from app.domain.agent.harness.pi.behaviour import declaration

__all__ = ["declaration"]
