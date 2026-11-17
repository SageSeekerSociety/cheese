"""聚类那一问的回答怎么读（学情分析 step 3）：每条提问读一次就有归宿。

规则：模型归进已有类的照归；新类按它说的建；它没提到的提问各自成一类（用自己的概括
当代表性问题），于是下一批不会再读到同一条；编号对不上的忽略；读不出 JSON 就是失败。
"""

from types import SimpleNamespace

from app.domain.space.learning_class_analysis import parse_clustering

POINTS = [SimpleNamespace(id=1, name="相关子查询"), SimpleNamespace(id=2, name="索引")]
CLUSTERS = [(10, "怎么引用外层的列？", 1)]
ASKED = [(100, "怎么引用外层", 1), (101, "建了索引还是慢", 2), (102, "幻读是什么", None)]


def test_every_question_ends_up_in_exactly_one_group():
    answer = (
        '{"assign": [{"q": "q1", "c": "c1"}, {"q": "q9", "c": "c1"}],'
        ' "new": [{"label": "索引为什么没用上？", "point": "索引", "q": ["q2", "q1"]}]}'
    )
    grouping = parse_clustering(answer, points=POINTS, clusters=CLUSTERS, asked=ASKED)
    assert grouping is not None
    assert grouping.assigned == {10: [100]}
    placed = [qid for new in grouping.created for qid in new.question_ids]
    assert sorted(placed + [100]) == [100, 101, 102]
    by_label = {new.label: new for new in grouping.created}
    assert by_label["索引为什么没用上？"].point_id == 2
    # 模型没提到的那条自己成一类，代表性问题就是它的概括。
    assert by_label["幻读是什么"].question_ids == [102]


def test_an_answer_without_json_is_a_failure():
    assert (
        parse_clustering("我不知道", points=POINTS, clusters=CLUSTERS, asked=ASKED)
        is None
    )
