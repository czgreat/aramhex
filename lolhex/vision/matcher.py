"""把 OCR 文本模糊匹配到已知名称（海克斯 / 英雄）。"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

_PUNCT = re.compile(r"[\s·•・:：,，.。!！?？'\"“”‘’()（）\[\]【】<>《》\-—_~～|/\\]+")


# 繁简写法几乎一样的字，OCR 常认成另一个（台服「斗內」切出标题单独认时读成「斗内」）。两字名差一个字只有 50 分，所以先统一
_VARIANTS = str.maketrans("內別戶吳眞", "内别户吴真")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_VARIANTS)
    return _PUNCT.sub("", s).lower()


# 短的英文名（如 Stats!）和别的词只差一两个字母就能过线（Start 对 Stats 有 80 分），要更像才算
SHORT_ASCII = 6
SHORT_ASCII_MIN = 90.0


class NameMatcher:
    def __init__(self, names: dict[str, str | list[str]], threshold: float = 72.0,
                 min_margin: float = 0.0):
        """names: {id: 显示名} 或 {id: [几种语言的名字]}。"""
        self.threshold = threshold
        self.min_margin = min_margin
        self._ids: list[str] = []
        self._keys: list[str] = []
        self._bangs: list[bool] = []
        for i, ns in names.items():
            for n in ([ns] if isinstance(ns, str) else ns):
                k = normalize(n)
                if k:
                    self._ids.append(i)
                    self._keys.append(k)
                    self._bangs.append(_has_bang(n))

    def __len__(self) -> int:
        return len(self._keys)

    def match(self, text: str) -> tuple[str | None, float]:
        q = normalize(text)
        if not q or not self._keys:
            return None, 0.0
        if len(q) < 2:  # 只剩一个字（「邦！」、英雄「慎」）：只认完全一样的，模糊匹配太容易错
            pos = next((i for i, k in enumerate(self._keys) if k == q), None)
            return (None, 0.0) if pos is None else (self._pick(pos, text), 100.0)
        exact = next((i for i, k in enumerate(self._keys) if k == q), None)
        if exact is not None:
            return self._pick(exact, text), 100.0
        scorer = fuzz.ratio
        hit = process.extractOne(q, self._keys, scorer=fuzz.ratio)
        if not hit:
            return None, 0.0
        key, score, pos = hit
        # 名字较长时允许 OCR 多识别出前后缀（如"棱彩阶"标记），用 partial 再试一次。
        if score < self.threshold and len(q) > 3:
            hit2 = process.extractOne(q, self._keys, scorer=fuzz.partial_ratio)
            if hit2 and hit2[1] >= 90 and len(hit2[0]) >= 3 and abs(len(hit2[0]) - len(q)) <= 3:
                key, score, pos = hit2
                scorer = fuzz.partial_ratio
        if score < self.threshold or (key.isascii() and len(key) <= SHORT_ASCII and score < SHORT_ASCII_MIN):
            return None, float(score)
        if self.min_margin:
            for other_key, other_score, other_pos in process.extract(q, self._keys, scorer=scorer, limit=8):
                if self._ids[other_pos] == self._ids[pos]:
                    continue  # 同一个海克斯的繁中等别名不构成另一个候选。
                if scorer is fuzz.partial_ratio and (len(other_key) < 3 or abs(len(other_key)-len(q)) > 3):
                    continue
                if score - other_score < self.min_margin:
                    return None, float(score)
                break
        return self._pick(pos, text), float(score)

    def exact(self, text: str) -> str | None:
        """规范化后整个名字（含别名）一字不差时返回 ID，否则 None。match 的 100 分也可能来自
        partial_ratio（「棱彩阶恶趣味」对「恶趣味」），不能当作完全对上。"""
        q = normalize(text)
        pos = next((i for i, k in enumerate(self._keys) if k == q), None) if q else None
        return None if pos is None else self._pick(pos, text)

    def _pick(self, pos: int, text: str) -> str:
        """去掉标点后同名的几个海克斯（4.0「罪恶快感！」和老的「罪恶快感」）：按有没有感叹号分。"""
        same = [i for i, k in enumerate(self._keys) if k == self._keys[pos] and self._ids[i] != self._ids[pos]]
        if not same:
            return self._ids[pos]
        bang = _has_bang(text)
        return next((self._ids[i] for i in [pos] + same if self._bangs[i] == bang), self._ids[pos])


def _has_bang(s: str) -> bool:
    return "!" in unicodedata.normalize("NFKC", s or "")
