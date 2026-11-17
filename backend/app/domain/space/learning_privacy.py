"""老师看全班时，摘录怎么去掉「是谁」：本空间固定代号，和对话里的身份信息遮蔽。

学情分析给老师的只有全班整体（§ learning analysis, step 3）。整体里仍有要引用成员原话
的地方 —— 高频问题的例句、常见误解的摘录、导出报告里的匿名对话 —— 这两样东西让那
些原话不指向任何一个人：

- **代号**（``member_code``）：成员 id 与空间 id 一起算出的 4 位代号，同一个人在同一
  块板上永远是同一个，换一块板就不同。用带密钥的 HMAC 算，不存任何映射：板上 id 都
  是小整数，不带密钥的哈希逐个试一遍就能倒推回去，带了密钥就不能。界面上写作
  「成员 A7F3」。
- **遮蔽**（``Masker``）：原话里出现的姓名、学号、手机号、邮箱、证件号在给人看之前
  换成 ``[姓名]`` ``[学号]`` 这类记号。规则，不调模型：这块板上每个人的昵称、
  handle、实名与学号逐字遮；「我叫…」「学号…」这类说法、手机号、邮箱、长串数字按
  正则遮。宁可多遮：一串 SQL 里的长数字被遮成 ``[学号]`` 不碍理解，漏一个学号就是
  泄露。

实名与学号在这里只在内存里用来遮蔽，不写进任何返回值、不落库。
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.config import settings

#: 代号几位。4 位 base32 是 2^20 种，几百人的班里撞上的机会很小；撞上了也只是两个
#: 人的例句标了同一个代号，人数统计按 id 数，不受影响。
CODE_LENGTH = 4

#: 不像字母也不像数字的那些字母去掉了（I L O U），读出来不会认错。
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _code_key() -> bytes:
    # 由 jwt_secret 派生，不另加一个部署密钥（同 ``preview_connection_auth_secret``）。
    return hashlib.sha256(
        b"cheesex:learning-member-code:v1:" + settings.jwt_secret.encode()
    ).digest()


def member_code(space_id: int, user_id: int) -> str:
    """这个人在这块板上的固定代号，例如 ``A7F3``。不可逆，不存映射。"""
    digest = hmac.new(
        _code_key(), f"{space_id}:{user_id}".encode(), hashlib.sha256
    ).digest()
    value = int.from_bytes(digest[:4], "big")
    out = []
    for _ in range(CODE_LENGTH):
        out.append(_ALPHABET[value % 32])
        value //= 32
    return "".join(out)


# --- 遮蔽 -------------------------------------------------------------------

NAME_MARK = "[姓名]"
STUDENT_ID_MARK = "[学号]"
PHONE_MARK = "[手机号]"
EMAIL_MARK = "[邮箱]"
ID_CARD_MARK = "[证件号]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_ID_CARD = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d{4}){2}(?!\d)")
#: 「学号 2021xxxx」「学号是 B21…」：说出来的学号，不管多长。
_STUDENT_ID_SAID = re.compile(
    r"(学号|工号|学籍号)(\s*(?:是|为|[:：])?\s*)[A-Za-z]?\d{3,}"
)
#: 没说是学号的长串数字（8 位以上）也遮：学号、准考证号多半就是这个样子。
_LONG_DIGITS = re.compile(r"(?<![\d.])\d{8,}(?![\d.])")
#: 「我叫张三」「我的名字是…」「姓名：…」—— 后面跟着的两到四个汉字是名字。
_NAME_SAID = re.compile(
    r"(我叫|我的名字是|我的名字叫|名字叫|姓名\s*[:：]?|本人)\s*([一-鿿·]{2,4})"
)
#: 「我是张三，……」：只在后面紧跟标点、空白或「学号」时才算名字，「我是不是」不算。
_NAME_IAM = re.compile(r"(我是)\s*([一-鿿·]{2,3})(?=[，,。.！!？?\s；;]|学号|$)")
_ASCII_TERM = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class Masker:
    """一块板的遮蔽规则：逐字遮的那几样（名字、学号）加上通用的正则。"""

    names: list[str] = field(default_factory=list)
    student_ids: list[str] = field(default_factory=list)
    _pattern: re.Pattern[str] | None = None

    @classmethod
    def of(cls, names: Iterable[str], student_ids: Iterable[str] = ()) -> Masker:
        clean_names = sorted(
            {n.strip() for n in names if n and len(n.strip()) >= 2},
            key=len,
            reverse=True,
        )
        clean_ids = sorted(
            {s.strip() for s in student_ids if s and len(s.strip()) >= 4},
            key=len,
            reverse=True,
        )
        return cls(names=clean_names, student_ids=clean_ids)

    def _terms(self) -> re.Pattern[str] | None:
        if self._pattern is None and (self.names or self.student_ids):
            parts = []
            for term in [*self.student_ids, *self.names]:
                escaped = re.escape(term)
                if _ASCII_TERM.match(term):
                    # handle、英文名：只遮整词，不遮进别的标识符里。
                    escaped = rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
                parts.append(escaped)
            self._pattern = re.compile("|".join(parts), re.IGNORECASE)
        return self._pattern

    def mask(self, text: str) -> str:
        """``text`` 里的身份信息换成记号。"""
        if not text:
            return text
        out = _EMAIL.sub(EMAIL_MARK, text)
        out = _ID_CARD.sub(ID_CARD_MARK, out)
        out = _PHONE.sub(PHONE_MARK, out)
        out = _STUDENT_ID_SAID.sub(
            lambda m: m.group(1) + m.group(2) + STUDENT_ID_MARK, out
        )
        terms = self._terms()
        if terms is not None:
            student_ids = {s.casefold() for s in self.student_ids}
            out = terms.sub(
                lambda m: (
                    STUDENT_ID_MARK
                    if m.group(0).casefold() in student_ids
                    else NAME_MARK
                ),
                out,
            )
        out = _LONG_DIGITS.sub(STUDENT_ID_MARK, out)
        out = _NAME_SAID.sub(lambda m: m.group(1) + NAME_MARK, out)
        out = _NAME_IAM.sub(lambda m: m.group(1) + NAME_MARK, out)
        return out
