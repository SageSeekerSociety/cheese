#set document(title: "高等代数Ⅰ（计算机类）期中模拟卷")
#set page(
  paper: "a4",
  margin: (x: 20mm, top: 17mm, bottom: 15mm),
  numbering: (i, n) => [第 #i 页 / 共 #n 页],
)
#set text(font: ("Noto Serif CJK SC",), size: 10.5pt, lang: "zh")
#set par(justify: true, leading: 0.68em, spacing: 1.0em)
#let dim = math.op("dim")
#let span = math.op("span")
#let gap = v(2.2em)

#align(center)[
  #text(size: 15pt, weight: "bold")[高等代数Ⅰ（计算机类）] \
  #v(0.2em)
  #text(size: 12.5pt)[期中模拟卷] \
  #v(0.15em)
  #text(size: 9.5pt)[考试时间 90 分钟　　满分 100 分　　无附加题]
]

#v(0.45em)
#grid(
  columns: (1fr, 1fr, 1fr),
  [班级 #box(width: 3.2em, stroke: (bottom: 0.6pt))[]],
  [姓名 #box(width: 3.2em, stroke: (bottom: 0.6pt))[]],
  [学号 #box(width: 4.0em, stroke: (bottom: 0.6pt))[]],
)

#v(0.45em)
#block(inset: (x: 0.75em, y: 0.6em), stroke: 0.4pt + luma(150), radius: 2pt, width: 100%)[
  #text(size: 9pt)[
    所有矩阵与线性空间均在实数域上。记 $C(A)$、$N(A)$ 分别为矩阵 $A$ 的列空间与零空间，
    $r(A)$ 为其秩，$I_k$ 为 $k$ 阶单位矩阵。请写出必要过程，使用本课程范围内的方法作答，
    引用结论须说明依据。
  ]
]

#v(0.5em)

*1.*［10 分］设线性方程组
$
  cases(
    x_1 + x_2 + x_3 = 1,
    x_1 + 2x_2 + 3x_3 = 2,
    x_1 + 4x_2 + a x_3 = b,
  )
$
其中 $a, b in RR$ 为参数。

（1）［5 分］就 $a, b$ 的取值讨论方程组的解的情况，并在有唯一解时求出解。

（2）［5 分］求使方程组有唯一解且满足 $x_1 = x_2 = x_3$ 的全部 $(a, b)$。

#gap

*2.*［10 分］设 $A = mat(2, 1, 0; 0, 2, 1; 0, 0, 2)$。

（1）［4 分］计算 $A^2$ 与 $A^3$。

（2）［6 分］求 $A^k$（$k$ 为正整数）与 $A^(-1)$，并证明所得的 $A^k$ 公式。

#gap

*3.*［10 分］设
$
  A = mat(1, 2, 0, 0, 1; 0, 1, 1, 0, -1; 0, 0, 0, 1, 2; 1, 3, 1, 1, 2).
$

（1）［3 分］求 $A$ 的简化阶梯型 $R$ 及 $r(A)$。

（2）［3 分］求 $C(A)$ 与 $C(A^T)$ 的一组基。

（3）［2 分］求 $N(A)$ 的一组基及 $dim N(A)$。

（4）［2 分］取 $b = (1, 0, 1, 2)^T$，判断 $A x = b$ 是否有解；有解时写出全部解。

#gap

*4.*［15 分］设 $A$ 为 $m times n$ 矩阵，$B$ 为 $n times p$ 矩阵。

（1）［7 分］证明
$
  r(A B) = r(B) - dim(C(B) inter N(A)).
$

（2）［8 分］由此证明 $r(A B) >= r(A) + r(B) - n$，并证明等号成立当且仅当 $N(A) subset.eq C(B)$。

#pagebreak()

*5.*［15 分］设
$
  A = mat(I_2, B; C, D), quad
  B = mat(1, 2; 3, 1), quad
  C = mat(2, 1; 1, 1), quad
  D = mat(7, 6; 5, 4),
$
其中 $I_2$ 为二阶单位矩阵。

（1）［5 分］求 $S = D - C B$ 及其逆矩阵 $S^(-1)$。

（2）［10 分］用分块矩阵的乘法恒等式求 $A^(-1)$，须写出所用的恒等式，并说明每一步成立的理由。

#gap

*6.*［20 分］设
$
  A = mat(0, 1, 2; -1, 0, 3; -1, 0, 4).
$

（1）［9 分］求置换矩阵 $P$、单位下三角矩阵 $L$ 与上三角矩阵 $U$，使 $P A = L U$；须写出换行与消元过程。

（2）［5 分］利用上述分解解 $A x = (2, 1, 3)^T$。

（3）［6 分］利用上述分解求 $A^(-1)$。

#gap

*7.*［20 分］在 $RR^4$ 中，设 $U = span{a_1, a_2, a_3}$，其中
$
  a_1 = (1, 2, 0, 1)^T, quad
  a_2 = (2, 0, 1, 3)^T, quad
  a_3 = (1, 1, 1, 1)^T ,
$
又设
$
  W = {(x_1, x_2, x_3, x_4)^T in RR^4 : x_1 + x_2 - x_3 - x_4 = 0}.
$

（1）［6 分］求 $dim U$ 与 $dim W$，并分别给出 $U$ 与 $W$ 的一组基。

（2）［6 分］求 $dim(U inter W)$ 与 $U inter W$ 的一组基，并判断 $U + W = RR^4$ 是否成立。

（3）［8 分］求 $W$ 的子空间 $W'$ 的维数的最大可能值，使得 $U inter W' = {0}$；并对此最大值给出一例，说明理由。
