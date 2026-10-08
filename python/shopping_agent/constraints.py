"""Typed hard conditions and current-source judgments for shopping requests.

Parsing is deliberately finite and auditable. Retrieval may rank candidates,
but only a supported judgment for every recognized hard condition can admit a
product. Unrecognized explicit capability demands become unknown conditions.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from .schemas import ConditionJudgment, EvidenceRef
from .storage import CatalogStore, Product, SourceDocument


Operator = Literal["=", "!=", ">", ">=", "<", "<=", "present", "absent"]


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: Operator
    expected: str | float
    unit: str | None = None
    phrase: str = ""


_NUMERIC = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mAh|kg|g|克|英寸|Hz|W|GB|小时)"
    r"(?![A-Za-z])", re.IGNORECASE,
)
_RESOLUTION = re.compile(r"(?<!\d)(\d{3,4})\s*[×xX]\s*(\d{3,4})(?!\d)")
_IP_RATING = re.compile(r"(?<![A-Za-z0-9])IP([0-6X])([0-9X])(?![A-Za-z0-9])", re.I)
_RESOLUTION_CLASS = re.compile(r"(?<![A-Za-z0-9])(?:2K|4K|8K)(?![A-Za-z0-9])", re.I)
_BLUETOOTH_VERSION = re.compile(
    r"(?:蓝牙|bluetooth)\s*(?:版本\s*|v\s*)?(\d+\.\d+(?:\.\d+)?)",
    re.I,
)
_FEATURES = (
    ("esim", re.compile(r"(?<![A-Za-z0-9])e-?sim(?![A-Za-z0-9])", re.I)),
    ("nfc", re.compile(r"(?<![A-Za-z0-9])nfc(?![A-Za-z0-9])", re.I)),
    ("vesa", re.compile(r"(?<![A-Za-z0-9])vesa(?![A-Za-z0-9])", re.I)),
    ("anc", re.compile(r"主动降噪|(?<![A-Za-z0-9])anc(?![A-Za-z0-9])", re.I)),
    ("pen", re.compile(r"手写笔")),
    ("hotswap", re.compile(r"热插拔")),
    ("camera", re.compile(r"摄像头")),
    ("wireless_connection", re.compile(r"无线连接|蓝牙|2\.4\s*g(?:hz)?", re.I)),
    ("wireless_charging", re.compile(r"无线充电")),
    ("satellite_communication", re.compile(r"卫星通信")),
    ("mechanical", re.compile(r"机械键盘|机械轴")),
    ("numpad", re.compile(r"独立数字区")),
)
_SOURCE_FEATURES = {
    "esim": re.compile(r"(?<![A-Za-z0-9])e-?sim(?![A-Za-z0-9])", re.I),
    "nfc": re.compile(r"(?<![A-Za-z0-9])nfc(?![A-Za-z0-9])", re.I),
    "vesa": re.compile(r"(?<![A-Za-z0-9])vesa(?![A-Za-z0-9])", re.I),
    "anc": re.compile(r"主动降噪|(?<![A-Za-z0-9])anc(?![A-Za-z0-9])", re.I),
    "pen": re.compile(r"手写笔"),
    "hotswap": re.compile(r"热插拔"),
    "camera": re.compile(r"摄像头|主摄|镜头"),
    "wireless_connection": re.compile(r"无线连接|蓝牙|2\.4\s*g(?:hz)?", re.I),
    "wireless_charging": re.compile(r"无线充电"),
    "satellite_communication": re.compile(r"卫星通信"),
    "mechanical": re.compile(r"机械键盘|机械轴"),
    "numpad": re.compile(r"独立数字区"),
}
_CATALOG_FIELDS = {"price", "stock", "category"}


def _clause_before(query: str, start: int) -> str:
    return re.split(r"[，。；、,.!?！？]", query[:start])[-1]


def _operator(query: str, start: int, end: int, default: Operator = "=") -> Operator:
    before = _clause_before(query, start)[-20:]
    after = query[end:end + 6]
    if re.search(r"至少|不少于|不低于|不能低于|不小于|起码|大于等于", before) or "以上" in after:
        return ">="
    if re.search(r"不超过|不能超过|不高于|至多|最多|不大于|小于等于", before) or re.search(r"以内|以下|至多|最多", after):
        return "<="
    if re.search(r"大于|高于|超过|多于", before):
        return ">"
    if re.search(r"低于|不到|少于|小于", before):
        return "<"
    if re.search(r"(?:不要|不想要|排除|不是|并非|非|避开)\s*$", before):
        return "!="
    return default


def _query_negated(query: str, start: int) -> bool:
    before = _clause_before(query, start)[-18:]
    return bool(re.search(
        r"(?:(?<!有)没有|不要|不想要|不需要|不支持|不能支持|无法支持|"
        r"不具备|不带|不含|不提供|无|排除|非)\s*"
        r"(?:支持|具备|带有|有)?\s*$", before,
    ))


def _query_not_required(query: str, start: int, end: int) -> bool:
    """An optional attribute is neither required present nor required absent."""

    before = _clause_before(query, start)[-18:]
    after = query[end:end + 24]
    optional = r"(?:不要求|不需要|无需|不用|可有可无)"
    return bool(
        re.search(optional + r"\s*(?:有|支持|具备|带|配备)?\s*$", before)
        or re.match(
            r"\s*(?:(?:充电|快充|功率|输出功率|瓦数|供电|能力|功能)\s*){0,2}"
            r"(?:不需要|无需|不要求|可有可无|无所谓)", after,
        )
    )


def parse_conditions(
    query: str, *, category: str | None = None, max_price: float | None = None,
) -> tuple[Condition, ...]:
    """Extract explicit typed requirements without consulting any answer labels."""

    found: list[Condition] = []
    for match in _NUMERIC.finditer(query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        amount = float(match.group(1))
        unit = match.group(2).lower()
        before = _clause_before(query, match.start())[-18:]
        after = query[match.end():match.end() + 10]
        field: str | None = None
        normalized_unit: str | None = None
        if unit == "mah":
            field, normalized_unit = "battery_mah", "mAh"
        elif unit in {"kg", "g", "克"} and (unit == "kg" or re.search(r"重|机身|质量", before + after)):
            field, normalized_unit = "weight_g", "g"
            amount *= 1000 if unit == "kg" else 1
        elif unit == "英寸":
            field, normalized_unit = "screen_inches", "英寸"
        elif unit == "hz":
            field, normalized_unit = "refresh_hz", "Hz"
        elif unit == "w":
            field, normalized_unit = "power_w", "W"
        elif unit == "gb":
            nearby = (before[-8:] + after).lower()
            field = "ram_gb" if re.search(r"内存|ram", nearby) else "storage_gb"
            normalized_unit = "GB"
        elif unit == "小时":
            clause = _clause_before(query, match.start())
            field = (
                "single_use_hours" if re.search(r"单次|连续", clause)
                else ("case_hours" if "充电盒" in clause else "battery_life_hours")
            )
            normalized_unit = "小时"
        if field is not None:
            found.append(Condition(
                field, _operator(query, match.start(), match.end()),
                amount, normalized_unit, match.group(),
            ))

    for match in _RESOLUTION.finditer(query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "resolution", _operator(query, match.start(), match.end()),
            f"{int(match.group(1))}×{int(match.group(2))}",
            "pixels", match.group(),
        ))
    for match in _RESOLUTION_CLASS.finditer(query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "resolution_class", _operator(query, match.start(), match.end()),
            match.group().upper(), None, match.group(),
        ))
    for match in re.finditer(r"(?<!\d)(\d{2,3})\s*键(?!\d)", query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "key_count", _operator(query, match.start(), match.end()),
            float(match.group(1)), "键", match.group(),
        ))
    for match in _IP_RATING.finditer(query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "ip_rating", _operator(query, match.start(), match.end()),
            match.group().upper().replace(" ", ""), None, match.group(),
        ))

    for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:元|块)(?!\d)", query):
        context = _clause_before(query, match.start())[-18:]
        tail = query[match.end():match.end() + 6]
        if not re.search(r"预算|价格|售价|花费|价位|不超过|不高于|最多|以内|低于|不到|少于", context + tail):
            continue
        op = _operator(query, match.start(), match.end(), "<=" if "预算" in context else "=")
        found.append(Condition("price", op, float(match.group(1)), "元", match.group()))
    for match in re.finditer(r"(?<!\d)(\d+)\s*件(?!\d)", query):
        if "库存" not in _clause_before(query, match.start())[-12:]:
            continue
        found.append(Condition(
            "stock", _operator(query, match.start(), match.end(), ">="),
            float(match.group(1)), "件", match.group(),
        ))
    if re.search(r"现货|有货|可购买|能买到|库存大于零|库存>\s*0", query) and not any(
        item.field == "stock" for item in found
    ):
        found.append(Condition("stock", ">", 0.0, "件", "现货/有货"))

    for field, pattern in _FEATURES:
        for match in pattern.finditer(query):
            if _query_not_required(query, match.start(), match.end()):
                continue
            operator: Operator = "absent" if _query_negated(query, match.start()) else "present"
            found.append(Condition(field, operator, field, None, match.group()))
            break
    for match in _BLUETOOTH_VERSION.finditer(query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "bluetooth_version", _operator(query, match.start(), match.end()),
            match.group(1), None, match.group(),
        ))
    for match in re.finditer(r"(?<![A-Za-z])(?:IPS|OLED|LCD|VA)(?![A-Za-z])", query, re.I):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "panel_type", _operator(query, match.start(), match.end()),
            match.group().upper(), None, match.group(),
        ))
    for match in re.finditer(r"入耳式|开放式|骨传导", query):
        if _query_not_required(query, match.start(), match.end()):
            continue
        found.append(Condition(
            "wearing_style", _operator(query, match.start(), match.end()),
            match.group(), None, match.group(),
        ))

    # Strong unfamiliar abilities are not silently downgraded to soft ranking.
    for match in re.finditer(
        r"(?:必须|需要|要求)\s*(?:支持|具备|带有|内置|能)\s*"
        r"([\u3400-\u9fffA-Za-z0-9-]{2,15}?)"
        r"(?:功能|的(?:手机|耳机|平板|显示器|键盘))", query,
    ):
        phrase = match.group(1)
        if not any(
            item.phrase and item.phrase in match.group()
            for item in found
        ):
            found.append(Condition("unparsed_capability", "present", phrase, None, match.group()))

    if category:
        found.append(Condition("category", "=", category, None, "请求类目"))
    if max_price is not None and not any(
        item.field == "price" and item.operator == "<=" and item.expected == float(max_price)
        for item in found
    ):
        found.append(Condition("price", "<=", float(max_price), "元", "请求价格上限"))

    unique: list[Condition] = []
    seen: set[tuple[str, Operator, str | float]] = set()
    for item in found:
        key = (item.field, item.operator, item.expected)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _excerpt(text: str, start: int, end: int, radius: int = 26) -> str:
    return " ".join(text[max(0, start - radius):min(len(text), end + radius)].split())


def _ref(document: SourceDocument, text: str, start: int, end: int) -> EvidenceRef:
    return EvidenceRef(
        source_id=document.source_id,
        source_type=document.source_type,
        excerpt=_excerpt(text, start, end),
    )


def _source_texts(
    product: Product, store: CatalogStore, *, include_tags: bool = False,
) -> list[tuple[SourceDocument, str]]:
    texts: list[tuple[SourceDocument, str]] = []
    for document in store.get_product_documents(product.product_id):
        if document.source_type == "description":
            try:
                row = json.loads(document.original_text)
            except (TypeError, ValueError):
                continue
            if row.get("description") != product.description:
                continue
            texts.append((document, product.description))
            texts.append((document, product.name))
            if include_tags and product.tags:
                texts.append((document, " ".join(product.tags)))
        elif document.source_type == "review" and document.text in product.reviews:
            texts.append((document, document.text))
    return texts


_SOURCE_NUMERIC = {
    "battery_mah": re.compile(r"(\d+(?:\.\d+)?)\s*mAh", re.I),
    "weight_g": re.compile(r"(?:重量|机身重量|约)\s*(\d+(?:\.\d+)?)\s*(kg|g|克)", re.I),
    "screen_inches": re.compile(r"(\d+(?:\.\d+)?)\s*英寸"),
    "refresh_hz": re.compile(r"(\d+(?:\.\d+)?)\s*Hz", re.I),
    "power_w": re.compile(r"(\d+(?:\.\d+)?)\s*W", re.I),
    "storage_gb": re.compile(r"(\d+(?:\.\d+)?)\s*GB\s*(?:存储|储存|容量)", re.I),
    "ram_gb": re.compile(r"(\d+(?:\.\d+)?)\s*GB\s*(?:运行内存|内存|RAM)", re.I),
    "single_use_hours": re.compile(r"(?:单次|连续)[^，。；、]{0,12}?(\d+(?:\.\d+)?)\s*小时"),
    "case_hours": re.compile(r"充电盒[^，。；、]{0,12}?(\d+(?:\.\d+)?)\s*小时"),
    "battery_life_hours": re.compile(r"(\d+(?:\.\d+)?)\s*小时"),
    "key_count": re.compile(r"(?<!\d)(\d{2,3})\s*键(?!\d)"),
}


def _compare(actual: float, expected: float, operator: Operator) -> bool:
    if operator == "=":
        return math.isclose(actual, expected, abs_tol=1e-6)
    if operator == "!=":
        return not math.isclose(actual, expected, abs_tol=1e-6)
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    return False


def _numeric_judgment(
    condition: Condition, texts: Sequence[tuple[SourceDocument, str]],
) -> ConditionJudgment:
    observations: list[tuple[float | str, bool | None, EvidenceRef]] = []
    for document, text in texts:
        if condition.field == "resolution":
            for match in _RESOLUTION.finditer(text):
                observations.append((
                    float(int(match.group(1)) * 10000 + int(match.group(2))),
                    _mention_polarity(text, match.start(), match.end()),
                    _ref(document, text, match.start(), match.end()),
                ))
            continue
        if condition.field == "ip_rating":
            for match in _IP_RATING.finditer(text):
                observations.append((
                    match.group().upper(), _mention_polarity(text, match.start(), match.end()),
                    _ref(document, text, match.start(), match.end()),
                ))
            continue
        pattern = _SOURCE_NUMERIC.get(condition.field)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            value = float(match.group(1))
            if condition.field == "weight_g" and match.group(2).lower() == "kg":
                value *= 1000
            observations.append((
                value, _mention_polarity(text, match.start(), match.end()),
                _ref(document, text, match.start(), match.end()),
            ))

    refs = list(dict.fromkeys(
        (ref.source_id, ref.source_type, ref.excerpt) for _, _, ref in observations
    ))
    evidence = [EvidenceRef(source_id=s, source_type=t, excerpt=e) for s, t, e in refs]
    positive_values = {value for value, polarity, _ in observations if polarity is True}
    negative_values = {value for value, polarity, _ in observations if polarity is False}
    if not observations:
        status, conflict, explanation = "unknown", False, "当前来源未写明此规格。"
    elif positive_values & negative_values:
        status, conflict, explanation = "refuted", True, "当前来源对同一规格给出直接冲突。"
    elif len(positive_values) > 1:
        status, conflict, explanation = "refuted", True, "当前来源对同一规格给出冲突数值。"
    elif not positive_values:
        if condition.field == "resolution":
            width, height = str(condition.expected).split("×")
            expected: float | str = float(int(width) * 10000 + int(height))
        elif condition.field == "ip_rating":
            expected = str(condition.expected).upper()
        else:
            expected = float(condition.expected)
        denied_exactly = expected in negative_values
        if denied_exactly and condition.operator in {"=", "!="}:
            status = "refuted" if condition.operator == "=" else "supported"
            conflict = False
            explanation = "当前来源明确否认该精确规格。"
        else:
            status, conflict, explanation = "unknown", False, "当前来源没有肯定此规格。"
    else:
        actual = next(iter(positive_values))
        if condition.field == "resolution":
            if condition.operator not in {"=", "!="}:
                return ConditionJudgment(
                    field=condition.field, operator=condition.operator,
                    expected=condition.expected, unit=condition.unit,
                    status="unknown", evidence=evidence,
                    explanation="像素对没有可安全比较的单一顺序。",
                )
            width, height = str(condition.expected).split("×")
            expected = float(int(width) * 10000 + int(height))
            satisfied = _compare(float(actual), expected, condition.operator)
        elif condition.field == "ip_rating":
            rating = str(condition.expected).upper()
            requested_dust = -1 if rating[2] == "X" else int(rating[2])
            requested_water = -1 if rating[3] == "X" else int(rating[3])
            actual_rating = str(actual).upper()
            actual_dust = -1 if actual_rating[2] == "X" else int(actual_rating[2])
            actual_water = -1 if actual_rating[3] == "X" else int(actual_rating[3])
            # A documented lower water/dust level refutes; an undocumented
            # dimension cannot support a request for that dimension.
            if (requested_dust >= 0 and actual_dust >= 0 and actual_dust < requested_dust) or (
                requested_water >= 0 and actual_water >= 0 and actual_water < requested_water
            ):
                status, conflict, explanation = "refuted", False, "来源标出的防护等级不足。"
                return ConditionJudgment(
                    field=condition.field, operator=condition.operator,
                    expected=condition.expected, unit=condition.unit,
                    status=status, evidence=evidence, conflict=conflict,
                    explanation=explanation,
                )
            if (requested_dust >= 0 and actual_dust < 0) or (
                requested_water >= 0 and actual_water < 0
            ):
                status, conflict, explanation = "unknown", False, "来源未标明要求的防护维度。"
                return ConditionJudgment(
                    field=condition.field, operator=condition.operator,
                    expected=condition.expected, unit=condition.unit,
                    status=status, evidence=evidence, conflict=conflict,
                    explanation=explanation,
                )
            if condition.operator == "!=":
                satisfied = (actual_dust, actual_water) != (requested_dust, requested_water)
            elif condition.operator in {">=", ">"}:
                satisfied = (
                    actual_dust >= requested_dust and actual_water >= requested_water
                    and (condition.operator == ">=" or
                         (actual_dust, actual_water) != (requested_dust, requested_water))
                )
            elif condition.operator == "=":
                satisfied = (actual_dust, actual_water) == (requested_dust, requested_water)
            else:
                return ConditionJudgment(
                    field=condition.field, operator=condition.operator,
                    expected=condition.expected, unit=condition.unit,
                    status="unknown", evidence=evidence,
                    explanation="防护等级不支持此比较方向。",
                )
        else:
            satisfied = _compare(float(actual), float(condition.expected), condition.operator)
        status, conflict, explanation = (
            ("supported", False, "当前来源规格满足条件。") if satisfied else
            ("refuted", False, "当前来源规格不满足条件。")
        )
    return ConditionJudgment(
        field=condition.field, operator=condition.operator,
        expected=condition.expected, unit=condition.unit,
        status=status, evidence=evidence, conflict=conflict,
        explanation=explanation,
    )


def _mention_polarity(text: str, start: int, end: int) -> bool | None:
    """Return positive, denied, or unasserted for a source mention."""

    separators = r"[，。；、,.!?！？]"
    before = re.split(separators, text[max(0, start - 24):start])[-1]
    after = re.split(separators, text[end:end + 24])[0]
    uncertain = (
        r"(?:(?:尚|暂)?(?:未说明|未提及|未标注|未注明|未写明)|"
        r"没有(?:明确)?说明|没有提及|没有标注|没写明|未知|待确认|无法确认)"
    )
    uncertain_bridge = (
        r"(?:是否|有无)\s*(?:支持|具备|配置|配备|提供|有)?"
        r"|支持|具备|配置|配备|提供"
    )
    if re.search(uncertain + r"\s*(?:" + uncertain_bridge + r")?\s*$", before):
        return None
    suffix = r"(?:能力|功能|规格|参数|情况|是否支持|支持情况|屏幕|充电|信息|技术|版本)"
    if re.match(r"\s*(?:" + suffix + r"\s*){0,3}" + uncertain, after):
        return None
    denied = (
        r"(?:没有|不支持|不能支持|无法支持|未支持|不具备|不带|不含|"
        r"不提供|未提供|未配备|未配置|未标配|不兼容|不是|并非|非|无)"
    )
    if re.search(denied + r"\s*$", before):
        return False
    if re.match(
        r"\s*(?:" + suffix + r"\s*){0,2}"
        r"(?:不支持|不具备|没有|未配备|未提供|不可用|无法使用)", after,
    ):
        return False
    return True


def _capability_judgment(
    condition: Condition, texts: Sequence[tuple[SourceDocument, str]],
) -> ConditionJudgment:
    pattern = _SOURCE_FEATURES[condition.field]
    mentions: list[tuple[bool | None, EvidenceRef]] = []
    for document, text in texts:
        for match in pattern.finditer(text):
            # "无线充电" does not establish wireless connectivity.
            if condition.field == "wireless_connection" and text[match.start():].startswith("无线充电"):
                continue
            # Package contents do not negate tablet stylus input support.
            if condition.field == "pen" and re.search(
                r"(?:包装内|包装里|随箱|随附|附赠)[^，。；]{0,8}(?:没有|不含|无)\s*$",
                text[max(0, match.start() - 24):match.start()],
            ):
                continue
            mentions.append((
                _mention_polarity(text, match.start(), match.end()),
                _ref(document, text, match.start(), match.end()),
            ))
    refs = list(dict.fromkeys(
        (ref.source_id, ref.source_type, ref.excerpt) for _, ref in mentions
    ))
    evidence = [EvidenceRef(source_id=s, source_type=t, excerpt=e) for s, t, e in refs]
    positive = any(value is True for value, _ in mentions)
    negative = any(value is False for value, _ in mentions)
    if positive and negative:
        status, conflict, explanation = "refuted", True, "当前来源对该能力有直接冲突。"
    elif not positive and not negative:
        status, conflict, explanation = "unknown", False, "当前来源没有明确说明该能力。"
    elif (condition.operator == "present" and positive) or (
        condition.operator == "absent" and negative
    ):
        status, conflict, explanation = "supported", False, "当前来源明确支持该条件。"
    else:
        status, conflict, explanation = "refuted", False, "当前来源明确反驳该条件。"
    return ConditionJudgment(
        field=condition.field, operator=condition.operator,
        expected=condition.expected, unit=condition.unit,
        status=status, evidence=evidence, conflict=conflict,
        explanation=explanation,
    )


def _categorical_judgment(
    condition: Condition, texts: Sequence[tuple[SourceDocument, str]],
) -> ConditionJudgment:
    pattern = (
        _BLUETOOTH_VERSION if condition.field == "bluetooth_version" else
        re.compile(
            r"(?<![A-Za-z])(?:IPS|OLED|LCD|VA)(?![A-Za-z])"
            if condition.field == "panel_type" else
            r"(?<![A-Za-z0-9])(?:2K|4K|8K)(?![A-Za-z0-9])"
            if condition.field == "resolution_class" else
            r"(?<!半)(?:入耳式|开放式|骨传导)", re.I,
        )
    )
    mentions = [
        (match.group(1) if condition.field == "bluetooth_version" else
         match.group().upper() if condition.field in {"panel_type", "resolution_class"}
         else match.group(),
         _mention_polarity(text, match.start(), match.end()),
         _ref(document, text, match.start(), match.end()))
        for document, text in texts for match in pattern.finditer(text)
    ]
    evidence = [ref for _, _, ref in mentions]
    positive_values = {value for value, polarity, _ in mentions if polarity is True}
    negative_values = {value for value, polarity, _ in mentions if polarity is False}
    expected = str(condition.expected)
    if not mentions:
        status, conflict = "unknown", False
    elif positive_values & negative_values or (
        len(positive_values) > 1 and not (
            condition.field == "wearing_style"
            and positive_values == {"骨传导", "开放式"}
        )
    ):
        status, conflict = "refuted", True
    elif condition.operator == "=":
        status = (
            "refuted" if expected in negative_values else
            "supported" if expected in positive_values else
            "refuted" if positive_values else "unknown"
        )
        conflict = False
    elif condition.operator == "!=":
        status = (
            "refuted" if expected in positive_values else
            "supported" if expected in negative_values or positive_values else "unknown"
        )
        conflict = False
    else:
        status, conflict = "unknown", False
    return ConditionJudgment(
        field=condition.field, operator=condition.operator,
        expected=expected, unit=condition.unit,
        status=status, evidence=evidence, conflict=conflict,
        explanation=("当前来源未写明该类型。" if status == "unknown" else
                     "当前来源类型冲突。" if conflict else "已核对当前来源类型。"),
    )


def judge_conditions(
    conditions: Sequence[Condition], product: Product | None, store: CatalogStore,
) -> tuple[ConditionJudgment, ...]:
    """Judge every requirement from current documents and current catalog row."""

    if product is None:
        return tuple(ConditionJudgment(
            field=item.field, operator=item.operator, expected=item.expected,
            unit=item.unit, status="unknown", explanation="商品记录已失效。",
        ) for item in conditions)
    texts = _source_texts(product, store)
    snapshot = store.catalog_snapshot_source(product.product_id)
    result: list[ConditionJudgment] = []
    for item in conditions:
        if item.field in _CATALOG_FIELDS:
            actual = (
                product.price if item.field == "price" else
                product.stock if item.field == "stock" else product.category
            )
            satisfied = (
                _compare(float(actual), float(item.expected), item.operator)
                if item.field != "category" else actual == item.expected
            )
            evidence = [EvidenceRef(
                source_id=snapshot[0], source_type="catalog_snapshot", excerpt=snapshot[1],
            )] if snapshot is not None else []
            result.append(ConditionJudgment(
                field=item.field, operator=item.operator, expected=item.expected,
                unit=item.unit, status=("supported" if satisfied and evidence else
                                        "refuted" if evidence else "unknown"),
                evidence=evidence, explanation="按当前商品库记录核验。",
            ))
        elif item.field in _SOURCE_FEATURES:
            result.append(_capability_judgment(item, texts))
        elif item.field in {
            "panel_type", "wearing_style", "resolution_class", "bluetooth_version",
        }:
            result.append(_categorical_judgment(
                item, _source_texts(product, store, include_tags=True)
                if item.field == "resolution_class" else texts,
            ))
        elif item.field == "unparsed_capability":
            result.append(ConditionJudgment(
                field=item.field, operator=item.operator, expected=item.expected,
                unit=item.unit, status="unknown",
                explanation="无法可靠解析或核实该开放式能力要求。",
            ))
        else:
            result.append(_numeric_judgment(item, texts))
    return tuple(result)


def judgments_are_current(judgments: Sequence[ConditionJudgment], store: CatalogStore) -> bool:
    """Verify that every supporting reference still resolves at response time."""

    return all(
        judgment.status == "supported" and bool(judgment.evidence) and all(
            store.is_current_catalog_snapshot(ref.source_id)
            if ref.source_type == "catalog_snapshot" else
            store.get_document(ref.source_id) is not None
            for ref in judgment.evidence
        )
        for judgment in judgments
    )
