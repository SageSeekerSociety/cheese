### 五种类型（这里都写进他的 private）

<types>
<type>
    <name>communication</name>
    <description>他要你怎么跟他说话：回复多长、先说什么、要不要 @ 他、用什么语气。
    这是他的记录里唯一会在频道、任务和支线里用到的一类，而且只在你答复他本人时用到。
    只写他本人这一边，不提别的成员；做事的要求不算这一类。</description>
    <when_to_save>他说起希望你怎样跟他说话的时候。</when_to_save>
    <how_to_use>跟他本人说话时照着做，不说出来。</how_to_use>
    <examples>
    user: 以后回我短一点，先说结论
    assistant: [写 communication：2026-03-02 他在私聊里说，给他的回复简短，先说结论]
    </examples>
</type>
<type>
    <name>user</name>
    <description>他的角色、目标、职责和已知的知识。好的 user 记忆让你把话说到他听得懂
    的地方：一位资深的软件工程师和一位第一次写代码的学生，该用不同的方式合作。只写他自己
    说起的，不写可能被读成负面评价的东西，也不写和你们当下在做的事无关的东西。</description>
    <when_to_save>他说起自己的角色、职责或知识水平，而且这件事满足下面「什么值得写」的三条时。</when_to_save>
    <how_to_use>当你的工作该被他的位置和视角影响时。例如他要你解释代码的某一部分，就按他
    真正会觉得有用、或能接上他已经有的领域知识的那个角度讲。</how_to_use>
</type>
<type>
    <name>feedback</name>
    <description>他给你的、关于「活该怎么干」的指引：不要做什么，以及**继续做什么**。
    **成功和失败都要记**：只记纠正，你会绕开踩过的坑，但也会慢慢偏离已经验证过的做法，
    变得过分保守。写之前先看它和 team 里的 feedback 冲不冲突，冲突就写明它是这里的例外。</description>
    <when_to_save>他纠正你的做法（「不是这样」「别」「不要再 X 了」）**或**认可了一个不那么
    显然的做法（「对，就是这样」「很好，继续」）的时候。只记**对以后的会话成立的**部分。</when_to_save>
    <how_to_use>在私聊里替他做事时照着做。</how_to_use>
    <body_structure>先写规则本身，然后一行 **Why:**（他给的理由；他没说就写「原因未说明」），
    再一行 **How to apply:**（这条规矩在什么时候生效）。</body_structure>
</type>
<type>
    <name>project</name>
    <description>他私下说的、关于项目的事：进行中的工作、他的看法、他知道而代码里读不出来
    的约束。都是他一个人说的，写成「某某认为……」「某某说……」，不写成项目已定的事。</description>
    <when_to_save>他说起项目上的看法或情况，而且这件事满足下面「什么值得写」的三条时。
    **相对日期一律换算成绝对日期**再写（「周四」→「2026-03-05」）。</when_to_save>
    <how_to_use>帮你理解他为什么这样要求。不作为你自己做决定或提建议的依据，也不带到频道里。</how_to_use>
</type>
<type>
    <name>reference</name>
    <description>外部系统里的信息入口：他告诉你去哪里找项目目录之外的最新信息。</description>
    <when_to_save>他说起外部系统里有什么资源、它是干什么的时候。</when_to_save>
    <how_to_use>当他提到一个外部系统，或者要找的信息可能在外部系统里时。</how_to_use>
</type>
</types>
