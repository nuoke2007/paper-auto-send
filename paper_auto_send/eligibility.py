from __future__ import annotations

import re

from .regions import is_jiangsu_title
from .security import paper_subject


def eligibility_basis(title: str) -> str | None:
    """Require evidence in this resource's title, never a query or nearby card."""
    compact = re.sub(r"\s+", "", title)
    if is_jiangsu_title(compact):
        return "江苏地区（标题明确标注）"
    subject = paper_subject(compact)
    versions = {
        "语文": ("统编版", "统编", "部编版", "部编"),
        "数学": ("苏教版", "苏教"),
        "英语": ("译林一起点", "译林版一起点", "译林版一年级起点", "译林版"),
        "道德与法治": ("统编版", "统编", "部编版", "部编"),
        "科学": ("苏教版", "苏教"),
        "音乐": ("苏少版", "苏少"),
        "美术": ("苏少版", "苏少"),
    }
    if subject == "英语":
        if any(term in compact for term in ("三起点", "三年级起点", "3起点")):
            return None
        if not re.search(r"(?:[一二]|(?<!\d)[12])年级", compact):
            versions["英语"] = versions["英语"][:-1]
    allowed = versions.get(subject, ())
    # Conflicting explicit versions are not proof that the requested version applies.
    explicit_versions = re.findall(r"人教(?:PEP)?版|北师大版|冀教版|青岛版|教科版|粤教版|沪教版|外研版|苏教版|苏少版|译林版", compact)
    if any(version not in allowed for version in explicit_versions):
        # 人教版统编语文 is an established naming combination.
        if not (
            subject in ("语文", "道德与法治")
            and all(version == "人教版" for version in explicit_versions)
            and any(version in compact for version in allowed)
        ):
            return None
    version = next((version for version in allowed if version in compact), None)
    return f"教材版本：{subject} · {version}（标题明确标注）" if version else None
