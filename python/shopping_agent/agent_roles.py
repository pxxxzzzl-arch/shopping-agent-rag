"""Explicit contracts for task planning, knowledge lookup and final validation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Callable, Literal

from .retrieval import EvidenceIndex, RetrievalHit, SearchResult, _terms
from .product_requirements import claim_excerpt, missing_claims, required_claims
from .schemas import EvidenceRef, Recommendation, ShopRequest
from .storage import CatalogStore, Product, verbatim_source_text


Route = Literal["product", "faq", "mixed"]
_FAQ_TERMS = (
    "退货", "退款", "退换", "运费", "配送", "快递", "物流", "发票",
    "保修", "售后", "签收", "客服", "支付", "换货", "订单",
    "真实", "演示", "库存", "实时", "余量", "兼容", "包装",
    "附赠", "优惠券", "折扣", "一定",
)
_INVENTORY_TERMS = ("库存", "余量", "缺货")
_INVENTORY_AVAILABILITY = re.compile(
    r"有库存|库存(?:还)?(?:有|剩|多少|几)|(?:剩余|可用)库存|"
    r"余量(?:还有|多少)|有多少余量|"
    r"(?:库存|余量)\s*(?:大于|高于|超过|[>＞]|不为|非)\s*(?:0|零)"
)
_INVENTORY_POLICY = re.compile(
    r"(?:库存|余量).{0,10}(?:实时|归零|快照|规则|政策|更新|同步|保证|准确)"
    r"|(?:实时|归零|快照|规则|政策|更新|同步|保证|准确).{0,10}(?:库存|余量)"
    r"|库存为\s*[0零]|缺货.{0,8}(?:规则|政策|处理|怎么办)"
)
_META_RECOMMENDATION = re.compile(
    r"(?:能否|是否|可以|还能|还会|会不会).{0,3}推荐|推荐购买"
)
_FOLLOWUP = re.compile(r"这款|那款|它|刚才|上一款|前面")
_DELIVERY_TERMS = ("配送", "送达", "发货", "快递", "物流")
_LOCATION_BEFORE_DELIVERY = re.compile(
    r"([\u4e00-\u9fff]{2,16})(?=" + "|".join(_DELIVERY_TERMS) + r")"
)
_LOCATION_PREFIXES = (
    "我想问", "我想了解", "请问", "想问", "麻烦", "请帮我查", "帮我查",
    "请查", "想了解", "商品", "产品", "订单", "演示", "到", "往", "在", "给",
)
_GENERIC_DELIVERY_QUALIFIERS = {
    "商品", "产品", "订单", "演示商品", "普通", "标准", "一般", "全国", "本地",
    "同城", "免费", "默认", "正常", "退货", "退款", "售后", "这个商品",
}


@dataclass(frozen=True)
class PlanningInput:
    request: ShopRequest
    catalog_categories: tuple[str, ...]
    previous_product_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningDecision:
    route: Route
    rationale: str
    tools: tuple[str, ...]
    context_product_ids: tuple[str, ...] = ()


class PlanningAgent:
    def plan(self, task: PlanningInput) -> PlanningDecision:
        query = task.request.query
        product_mention = bool(task.request.category) or any(
            category in query for category in task.catalog_categories
        ) or bool(re.search(r"商品|产品|东西", query))
        context_ids = task.previous_product_ids if _FOLLOWUP.search(query) else ()
        product_mention = product_mention or bool(context_ids)
        inventory_policy = bool(_INVENTORY_POLICY.search(query)) or bool(
            "缺货" in query and _META_RECOMMENDATION.search(query)
        )
        inventory_lookup = bool(
            product_mention and _INVENTORY_AVAILABILITY.search(query)
            and not inventory_policy
        )
        # Availability belongs to the current catalog. Questions about how
        # inventory is maintained or how sold-out items are handled need FAQ.
        faq = any(
            term in query for term in _FAQ_TERMS if term not in _INVENTORY_TERMS
        ) or bool(
            any(term in query for term in _INVENTORY_TERMS)
            and not inventory_lookup
        )
        shopping_intent = inventory_lookup or bool(
            re.search(r"推荐|想买|选购|挑|选哪|找|哪款|买哪", query)
        )
        if inventory_policy and _META_RECOMMENDATION.search(query) and not re.search(
            r"(?:给我|帮我|请)推荐|推荐.*(?:并|同时|顺便)", query
        ):
            shopping_intent = False
        if faq and product_mention and shopping_intent:
            return PlanningDecision(
                "mixed", "同时包含商品与服务问题",
                ("profile", "catalog", "product_retrieval", "faq_retrieval", "stock_verify"),
                context_ids,
            )
        if faq:
            return PlanningDecision(
                "faq", "服务政策问答", ("faq_retrieval", "source_verify"),
            )
        return PlanningDecision(
            "product", "商品选购", ("profile", "catalog", "product_retrieval", "stock_verify"),
            context_ids,
        )


@dataclass(frozen=True)
class KnowledgeInput:
    query: str
    retrieval_mode: str
    limit: int = 5


@dataclass(frozen=True)
class KnowledgeResult:
    answer: str
    evidence: tuple[EvidenceRef, ...]
    hits: tuple[RetrievalHit, ...]
    warnings: tuple[str, ...]
    actual_mode: str


def _delivery_location(query: str) -> str | None:
    """Extract an explicit destination adjacent to a delivery request, if any.

    This is a conservative guard, not a general Chinese address parser. An
    unmatched destination is never answered with a different city's SLA.
    """
    if not any(term in query for term in _DELIVERY_TERMS):
        return None
    for match in _LOCATION_BEFORE_DELIVERY.finditer(query):
        candidate = match.group(1)
        for separator in ("到", "往", "在", "给"):
            if separator in candidate:
                candidate = candidate.rsplit(separator, 1)[-1]
        while True:
            prefix = next(
                (prefix for prefix in _LOCATION_PREFIXES if candidate.startswith(prefix)),
                None,
            )
            if prefix is None:
                break
            candidate = candidate[len(prefix):]
        for suffix in ("商品", "产品", "订单", "地区", "市内", "的", "市"):
            if candidate.endswith(suffix):
                candidate = candidate[:-len(suffix)]
        if 2 <= len(candidate) <= 6 and candidate not in _GENERIC_DELIVERY_QUALIFIERS:
            return candidate
    return None


def _faq_matches(query: str, text: str) -> bool:
    """Require a shared service topic before a source is allowed to answer."""
    location = _delivery_location(query)
    if location is not None and location not in text:
        return False
    if "实时" in query or "余量" in query:
        return any(term in text for term in ("实时", "快照", "更新"))
    if any(term in query for term in ("归零", "缺货", "库存为零", "库存为 0")):
        return any(term in text for term in ("缺货", "库存为 0", "库存为零"))
    if "折扣" in query or "优惠券" in query:
        return "折扣" in text or "优惠券" in text
    if "包装" in query or "标配" in query:
        return any(term in text for term in ("附赠", "另购", "单独购买"))
    query_topics = {term for term in _FAQ_TERMS if term in query}
    source_topics = {term for term in _FAQ_TERMS if term in text}
    if query_topics & source_topics:
        return True
    equivalences = (
        (("一定", "保证"), ("保证", "兼容")),
        (("包装", "标配", "附赠"), ("附赠", "另购", "单独购买")),
        (("实时", "余量"), ("库存", "快照")),
    )
    return any(
        any(term in query for term in question_terms)
        and any(term in text for term in source_terms)
        for question_terms, source_terms in equivalences
    )


def _faq_topic_key(question: str) -> str:
    normalized = question.replace("退款", "退货")
    if "运费" in normalized and "退货" in normalized:
        return "退货运费"
    return " ".join(normalized.split())


def _question_score(query: str, question: str) -> int:
    query_terms = _terms(query)
    question_terms = _terms(question)
    return sum(
        (3 if term.startswith("zh2:") else 1)
        for term in query_terms.keys() & question_terms.keys()
    )


class KnowledgeAgent:
    def __init__(self, store: CatalogStore, index: EvidenceIndex, timeout: float):
        self.store = store
        self.index = index
        self.timeout = timeout

    async def run(self, task: KnowledgeInput) -> KnowledgeResult:
        try:
            found: SearchResult = await asyncio.wait_for(
                asyncio.to_thread(
                    self.index.search, task.query, None, task.limit, task.retrieval_mode
                ),
                timeout=self.timeout,
            )
        except Exception as exc:
            return KnowledgeResult(
                "暂时无法核实这项服务信息。", (), (),
                (f"FAQ 检索失败：{type(exc).__name__}",), "bm25",
            )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._resolve, task, found),
                timeout=self.timeout,
            )
        except Exception as exc:
            return KnowledgeResult(
                "暂时无法核实这项服务信息。", (), (),
                (f"FAQ 来源核验失败：{type(exc).__name__}",), found.actual_mode,
            )

    def _resolve(self, task: KnowledgeInput, found: SearchResult) -> KnowledgeResult:
        warnings = (found.fallback_reason,) if found.fallback_reason else ()
        valid: list[tuple[RetrievalHit, object, object]] = []
        for hit in found.hits:
            current = self.store.get_document(hit.document.source_id)
            if current is None or current.source_type != "answer":
                continue
            if current.version != hit.document.version or not _faq_matches(task.query, current.text):
                continue
            faq = self.store.get_faq(current.faq_id) if current.faq_id else None
            if faq is not None:
                valid.append((hit, current, faq))
        if valid:
            question_answers: dict[str, set[str]] = {}
            for _, _, faq in valid:
                question_answers.setdefault(_faq_topic_key(faq.question), set()).add(
                    " ".join(faq.answer.split())
                )
            if any(len(answers) > 1 for answers in question_answers.values()):
                return KnowledgeResult(
                    "当前 FAQ 对同一问题有冲突，无法核实统一答案。",
                    (), (), warnings + ("FAQ 来源冲突。",), found.actual_mode,
                )
            hit, current, faq = max(
                valid, key=lambda entry: _question_score(task.query, entry[2].question)
            )
            excerpt = verbatim_source_text(current)
            if not excerpt:
                return KnowledgeResult(
                    "当前 FAQ 原文无法核实，无法回答该问题。",
                    (), (), warnings + ("FAQ 原文缺失。",), found.actual_mode,
                )
            ref = EvidenceRef(
                source_id=current.source_id,
                source_type="answer",
                excerpt=excerpt[:200],
            )
            # Quote the current source verbatim; no LLM can invent policy terms.
            prefix = "合成演示 FAQ" if current.license == "synthetic_demo" else "FAQ 来源"
            return KnowledgeResult(
                f"{prefix}：{excerpt} [{current.source_id}]",
                (ref,), (hit,), warnings, found.actual_mode,
            )
        return KnowledgeResult(
            "未找到能支持该问题的当前 FAQ；请核对正式服务政策。",
            (), (), warnings + ("FAQ 缺少可核验依据。",), found.actual_mode,
        )


@dataclass(frozen=True)
class VerificationInput:
    request: ShopRequest
    recommendations: tuple[Recommendation, ...]
    category: str | None
    max_price: float | None


@dataclass(frozen=True)
class VerificationResult:
    recommendations: tuple[Recommendation, ...]
    removed_count: int
    conflicted_count: int = 0


class VerificationAgent:
    """Re-read authoritative catalog and current source revisions before reply."""

    def __init__(
        self, store: CatalogStore,
        feature_guard: Callable[[str, object], bool] | None = None,
        conflict_guard: Callable[[Product], bool] | None = None,
    ):
        self.store = store
        self.feature_guard = feature_guard
        self.conflict_guard = conflict_guard

    def run(self, task: VerificationInput) -> VerificationResult:
        from .constraints import judge_conditions, judgments_are_current, parse_conditions

        verified: list[Recommendation] = []
        removed = 0
        conflicted = 0
        has_explicit_claims = bool(required_claims(task.request.query))
        conditions = parse_conditions(
            task.request.query, category=task.category, max_price=task.max_price,
        )
        for item in task.recommendations:
            current = self.store.get_product(item.product_id)
            if current is not None and self.conflict_guard is not None and self.conflict_guard(current):
                removed += 1
                conflicted += 1
                continue
            if (
                current is None or current.stock <= 0
                or (task.category is not None and current.category != task.category)
                or (task.max_price is not None and current.price > task.max_price)
                or (
                    self.feature_guard is not None
                    and not self.feature_guard(task.request.query, current)
                )
            ):
                removed += 1
                continue
            judgments = judge_conditions(conditions, current, self.store)
            if not judgments_are_current(judgments, self.store):
                removed += 1
                conflicted += int(any(judgment.conflict for judgment in judgments))
                continue
            evidence: list[EvidenceRef] = []
            claim_source_verified = False
            for ref in item.evidence:
                source = self.store.get_document(ref.source_id)
                if (
                    source is not None and source.product_id == current.product_id
                    and source.source_type == ref.source_type
                    and not re.search(
                        r"价格|售价|库存|折扣|优惠券|打折|[¥￥]|\d+(?:\.\d+)?元",
                        source.text,
                    )
                ):
                    if source.source_type == "description" and not missing_claims(
                        task.request.query, source.text
                    ):
                        claim_source_verified = True
                    quote = verbatim_source_text(source)
                    if quote:
                        evidence.append(EvidenceRef(
                            source_id=source.source_id,
                            source_type=source.source_type,
                            excerpt=claim_excerpt(task.request.query, quote)
                            if source.source_type == "description" else
                            " ".join(quote.split())[:100],
                        ))
            if has_explicit_claims and not claim_source_verified:
                # A source containing unverified price or discount language is
                # deliberately not cited. The catalog item may still be shown
                # with basic fields if its *current* description proves the
                # requested attribute; an invalidated source cannot pass.
                current_description = self.store.get_product_description_document(
                    current.product_id
                )
                if current_description is None or missing_claims(
                    task.request.query, current_description.text
                ):
                    removed += 1
                    continue
            reason = (
                f"相关资料提到：{evidence[0].excerpt} [{evidence[0].source_id}]"
                if evidence else
                f"属于{current.category}类目，价格¥{current.price:g}；暂无匹配的详细资料。"
            )
            verified.append(item.model_copy(update={
                "name": current.name, "category": current.category,
                "price": current.price, "stock": current.stock,
                "evidence": evidence, "reason": reason,
                "condition_judgments": list(judgments),
            }))
        return VerificationResult(tuple(verified), removed, conflicted)
