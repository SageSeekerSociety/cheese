"""老师看到的摘录里没有「是谁」：固定代号与身份信息遮蔽（学情分析 step 3）。

规则：

- 代号由成员与空间一起决定：同一个人同一块板永远一样，换一块板就不同；它不是成员
  id 本身，也看不出 id；
- 姓名、学号、手机号、邮箱、证件号在给人看之前换成记号；这块板上的人的昵称、handle、
  实名与学号逐字遮；「我叫…」「我是…，」这类说法后面的名字也遮；
- 不该遮的不遮：「我是不是写错了」不是名字，SQL 关键字不是名字。
"""

from app.domain.space.learning_privacy import Masker, member_code


def test_a_code_is_fixed_for_one_person_in_one_space_and_differs_elsewhere():
    assert member_code(3, 101) == member_code(3, 101)
    assert member_code(3, 101) != member_code(4, 101)
    assert member_code(3, 101) != member_code(3, 102)
    assert "101" not in member_code(3, 101)
    assert len(member_code(3, 101)) == 4


def test_identity_details_are_replaced_with_marks():
    masker = Masker.of(["张三", "zhangsan", "小明同学"], ["2021012345"])
    text = masker.mask(
        "老师好，我是李四，学号 B21001234。我的手机 138-1234-5678，"
        "邮箱 lisi@pku.edu.cn，身份证 11010519491231002X。"
        "张三说 zhangsan 的写法不对，学籍号 2021012345 是小明同学的。"
    )
    assert "李四" not in text and "B21001234" not in text
    assert "138-1234-5678" not in text and "lisi@pku.edu.cn" not in text
    assert "11010519491231002X" not in text
    assert "张三" not in text and "zhangsan" not in text and "小明同学" not in text
    assert "2021012345" not in text
    assert "[学号]" in text and "[手机号]" in text and "[邮箱]" in text
    assert "[姓名]" in text and "[证件号]" in text


def test_ordinary_words_are_left_alone():
    masker = Masker.of(["sel"])
    said = "我是不是写错了？SELECT name FROM t WHERE id = 3"
    assert masker.mask(said) == said
