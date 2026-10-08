"""Small, source-backed product requirements for explicit shopping claims.

Retrieval ranks relevant documents; it does not prove that every recommended
product meets every requested attribute. These rules cover catalog facts that
can be checked directly in the current product description and tags. Unknown
claims remain subject to the separate conservative guards in ``workflow``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RequiredClaim:
    name: str
    query: re.Pattern[str]
    source: re.Pattern[str]
    requires_absence: bool = False


def _rule(name: str, query: str, source: str, *, requires_absence: bool = False) -> RequiredClaim:
    return RequiredClaim(
        name, re.compile(query, re.IGNORECASE), re.compile(source, re.IGNORECASE),
        requires_absence,
    )


_RULES = (
    _rule("包耳佩戴", r"包耳", r"包耳"),
    _rule("头戴式", r"头戴式|头戴耳机", r"头戴式|头戴|包耳"),
    _rule("入耳式", r"入耳式", r"(?<!半)入耳式"),
    _rule("有线连接", r"能接线|有线(?:模式|连接)", r"有线(?:模式|连接)|支持有线"),
    _rule("双设备", r"两台设备|双设备", r"双设备|两台设备"),
    _rule("AAC", r"(?<![a-z0-9])aac(?![a-z0-9])", r"(?<![a-z0-9])aac(?![a-z0-9])"),
    _rule("IPX 防护等级", r"(?<![a-z0-9])ipx(?![a-z0-9])(?:\s*防护等级)?", r"(?<![a-z0-9])ipx[1-9](?![a-z0-9])"),
    _rule("防泼溅", r"防泼溅", r"防泼溅|(?<![a-z0-9])ipx[4-9](?![a-z0-9])"),
    _rule("防抖主摄", r"防抖主摄|主摄.{0,4}防抖", r"防抖主摄|主摄.{0,6}防抖|光学防抖"),
    _rule("超广角", r"超广角", r"超广角"),
    _rule("压感笔", r"压感(?:手写)?笔", r"压感(?:手写)?笔"),
    _rule("蜂窝网络", r"蜂窝网络", r"蜂窝网络"),
    _rule("折叠屏", r"折叠(?:屏|手机)|展开成大屏", r"折叠屏|折叠手机"),
    _rule("出差文档或移动办公", r"出差.{0,8}文档|移动办公", r"出差|移动办公"),
    _rule("轻薄电脑供电", r"轻薄电脑.{0,4}供电", r"轻薄电脑"),
    _rule("单 USB-C 口", r"(?:仅有|只有|仅|单)\s*(?:一|1|一个)?\s*(?:个)?\s*usb-c\s*(?:口|接口)?", r"单\s*usb-c\s*(?:口|接口)|单口[^，。；]{0,8}usb-c|(?:仅有|只有)\s*(?:一个|1个)\s*usb-c\s*(?:口|接口)(?=[，。；]|$)"),
    _rule("至少两个接口", r"至少.{0,4}(?:两个|2个)(?:接口|插口|口)", r"双口|三口|四口|多口|(?:两个|2个)[^，。；]{0,8}(?:接口|插口)|(?:两个|2个)\s*usb-[ca]"),
    _rule("USB-C 接口", r"(?<![不别])(?:必须有|要有|带|包含|配备)\s*usb-c", r"(?<![a-z0-9])usb-c(?![a-z0-9])"),
    _rule("折叠插脚", r"折叠插脚", r"折叠插脚"),
    _rule("不附充电线", r"(?:没有|不附|不带)\s*(?:充电)?线|无\s*充电线|无线(?:材|缆)", r"(?:没有|不附(?:送)?|不带|无)\s*(?:充电)?线", requires_absence=True),
    _rule("低噪键盘", r"(?:少打扰|安静|低噪|静音).{0,8}键盘|(?:键盘|按键).{0,8}(?:少打扰|安静|低噪|静音)|少打扰同事", r"低噪|静音|安静|低声音"),
    _rule("独立数字区", r"独立数字区", r"独立数字区"),
    _rule("68 键", r"68\s*键", r"68\s*键"),
    _rule("98 键", r"(?<!\d)98\s*键(?!\d)", r"(?<!\d)98\s*键(?!\d)"),
    _rule("机械键盘", r"机械键盘", r"机械键盘|机械轴"),
    _rule("分体弧形布局", r"分体弧形", r"分体弧形"),
    _rule("软质腕托", r"软质腕托", r"软质腕托"),
    _rule("蓝牙", r"蓝牙", r"蓝牙"),
    _rule("2.4 GHz", r"2\.4\s*g(?:hz)?", r"2\.4\s*g(?:hz)?"),
    _rule("无线连接", r"无线(?!充电|供电)", r"无线(?!充电|供电)|蓝牙|(?<!\d)2\.4\s*g(?:hz)?(?![a-z0-9])"),
    _rule("升降支架", r"支架.{0,8}升降|升降.{0,4}支架", r"支架.{0,8}升降|升降支架"),
    _rule("旋转支架", r"支架.{0,10}旋转|旋转.{0,4}支架", r"支架.{0,10}旋转|旋转支架"),
    _rule("4K 分辨率", r"(?<![a-z0-9])4\s*k(?![a-z0-9])", r"(?<![a-z0-9])4\s*k(?![a-z0-9])|3840\s*[×x]\s*2160"),
    _rule("出厂校色", r"出厂校色", r"出厂校色"),
    _rule("USB-C 视频输入", r"usb-c\s*视频输入", r"usb-c\s*视频输入"),
    _rule("HDMI", r"hdmi", r"hdmi"),
    _rule("VESA 安装", r"(?<![a-z0-9])vesa(?![a-z0-9])", r"(?<![a-z0-9])vesa(?![a-z0-9])"),
    _rule("eSIM", r"(?<![a-z0-9])e-?sim(?![a-z0-9])", r"(?<![a-z0-9])e-?sim(?![a-z0-9])"),
)


def required_claims(query: str) -> tuple[RequiredClaim, ...]:
    """Return explicit positive claims; a negated phrase is not a demand for it."""
    found = []
    for rule in _RULES:
        for match in rule.query.finditer(query):
            before = query[max(0, match.start() - 8):match.start()]
            if re.search(
                r"(?:不要|不需要|无需|排除|避免|避开|不是|并非|非|不想|不带|不含|"
                r"不支持|不能支持|无法支持|不提供|不具备|(?<!有)没有|"
                r"没写|未写|没标注|未标注|无)"
                r"\s*(?:支持|具备|带|含)?\s*$", before
            ):
                continue
            found.append(rule)
            break
    return tuple(found)


def missing_claims(query: str, source_text: str) -> tuple[str, ...]:
    """Report explicit claims absent from the product's current catalog source."""
    return tuple(
        rule.name for rule in required_claims(query)
        if not _source_asserts(rule, source_text)
    )


def _source_asserts(rule: RequiredClaim, source_text: str) -> bool:
    matches = list(rule.source.finditer(source_text))
    if not matches:
        return False
    if rule.name == "IPX 防护等级" and re.search(
        r"(?:没有|无|未标注|未注明|不提供)[^，。；]{0,8}"
        r"(?:ipx\s*\d*|防水|防护)\s*(?:防护)?等级",
        source_text, re.IGNORECASE,
    ):
        # A stale positive tag cannot override an explicit catalog denial.
        return False
    if rule.name == "单 USB-C 口":
        for other in re.finditer(
            r"usb-a|usb-b|micro-?usb|双口|三口|四口|多口|"
            r"(?:两个|2个)\s*(?:usb-c|接口)", source_text, re.IGNORECASE
        ):
            before = source_text[max(0, other.start() - 8):other.start()]
            if not re.search(r"(?:没有|不含|不带|无)\s*$", before):
                return False
    if rule.requires_absence:
        return True
    # A keyword in "不支持蓝牙" is not evidence of Bluetooth support. If a
    # source says both yes and no, leave the claim unverified.
    negated = []
    for match in matches:
        before = source_text[max(0, match.start() - 12):match.start()]
        before = re.split(r"[，。；、,.!?！？]", before)[-1]
        negated.append(bool(re.search(
            r"(?:没有|不带|不含|不支持|不能支持|无法支持|不提供|"
            r"不具备|无法|无)\s*$", before
        )))
    return any(not value for value in negated) and not any(negated)


def claim_excerpt(query: str, source_text: str, limit: int = 500) -> str:
    """Keep the supporting phrases visible even when a source is long."""
    clean = " ".join(source_text.split())
    claims = required_claims(query)
    if not claims:
        return clean[:100]
    if len(clean) <= limit:
        return clean
    windows: list[tuple[int, int]] = []
    for rule in claims:
        matches = list(rule.source.finditer(clean))
        if matches:
            match = matches[-1]
            windows.append((max(0, match.start() - 24), min(len(clean), match.end() + 36)))
    if not windows:
        return clean[:limit]
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    excerpt = " … ".join(clean[start:end] for start, end in merged)
    return excerpt[:limit]
