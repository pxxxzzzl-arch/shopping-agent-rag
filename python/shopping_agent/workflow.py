"""Catalog, retrieval, inventory, and answer nodes in a LangGraph workflow."""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .answer import AnswerComposer
from .agent_roles import (
    KnowledgeAgent, KnowledgeInput, PlanningAgent, PlanningDecision,
    PlanningInput, VerificationAgent, VerificationInput,
)
from .config import Settings
from .embeddings import create_embedding
from .constraints import Condition, judge_conditions, judgments_are_current, parse_conditions
from .conversation import ConversationStore
from .experiments import Assignment, ExperimentStore
from .product_requirements import claim_excerpt, missing_claims, required_claims
from .retrieval import (
    EvidenceDocument, EvidenceIndex, RetrievalHit, SearchResult,
)
from .schemas import EvidenceRef, Recommendation, RetrievalDiagnostic, ShopRequest, ShopResponse
from .seed import seed_demo_data, seed_demo_faq_data
from .storage import CatalogStore, Product, verbatim_source_text


# Context follows asyncio tasks and to_thread; no shared last-request state.
_retrieval_records: ContextVar[dict[str, RetrievalDiagnostic] | None] = ContextVar(
    "shopping_retrieval_records", default=None
)


class ShoppingState(TypedDict, total=False):
    request: ShopRequest
    profile_counts: dict[str, int]
    candidates: list[Product]
    max_price: float | None
    category: str | None
    hits: list[RetrievalHit]
    ranked: list[Product]
    recommendations: list[Recommendation]
    answer: str
    llm_used: bool
    warnings: list[str]
    retrieval_mode: str
    context_product_ids: tuple[str, ...]
    actual_mode: str
    source_conflict: bool
    conditions: tuple[Condition, ...]
    condition_conflict: bool
    condition_unknown: bool


class RoutingState(TypedDict, total=False):
    request: ShopRequest
    mode: str
    history_product_ids: tuple[str, ...]
    decision: PlanningDecision
    product_state: ShoppingState
    knowledge: Any
    response: ShopResponse
    assignment: Assignment | None
    request_id: str


@dataclass(frozen=True)
class ProductTask:
    request: ShopRequest
    retrieval_mode: str
    context_product_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductOutcome:
    state: ShoppingState
    failed: bool = False


class ProductAgent:
    """Runs catalog, profile, retrieval and stock tools under one task contract."""

    def __init__(self, graph: Any, timeout: float):
        self.graph = graph
        self.timeout = timeout

    async def run(self, task: ProductTask) -> ProductOutcome:
        try:
            result = await asyncio.wait_for(
                self.graph.ainvoke({
                    "request": task.request,
                    "retrieval_mode": task.retrieval_mode,
                    "context_product_ids": task.context_product_ids,
                }),
                timeout=self.timeout,
            )
            return ProductOutcome(result)
        except Exception as exc:
            return ProductOutcome({
                "request": task.request,
                "recommendations": [],
                "hits": [],
                "warnings": [f"商品工具失败：{type(exc).__name__}；未返回未经核验的商品。"],
            }, True)


def _chinese_amount(text: str) -> int:
    """Read common integer RMB amounts such as 一百 and 一千二百."""
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total = section = number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif char in units:
            section += (number or 1) * units[char]
            number = 0
        elif char == "万":
            total += (section + number) * 10000
            section = number = 0
    return total + section + number


def _budget_from_query(query: str) -> float | None:
    inclusive_patterns = (
        r"(?:预算|价格|售价)\s*[¥￥$]?\s*(\d+(?:\.\d+)?)",
        r"预算(?:控制在|上限(?:为)?)\s*[¥￥$]?\s*(\d+(?:\.\d+)?)",
        r"(?:不超过|不能超过|不高于|最多|以内)\s*[¥￥$]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)",
        r"(?:不超过|不能超过|不高于|最多|以内)\s*[¥￥$]\s*(\d+(?:\.\d+)?)",
        r"[¥￥$]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:以内|以下)",
        r"[¥￥$]\s*(\d+(?:\.\d+)?)\s*(?:以内|以下)",
    )
    limits = [
        float(match.group(1))
        for pattern in inclusive_patterns
        for match in re.finditer(pattern, query, flags=re.IGNORECASE)
    ]
    chinese_number = r"[零〇一二两三四五六七八九十百千万]+"
    for match in re.finditer(
        rf"({chinese_number})\s*(?:元|块)\s*(?:以内|以下)", query
    ):
        limits.append(float(_chinese_amount(match.group(1))))
    for match in re.finditer(rf"预算\s*({chinese_number})\s*(?:元|块)?", query):
        limits.append(float(_chinese_amount(match.group(1))))
    for match in re.finditer(
        rf"(?:不超过|不能超过|不高于|最多)\s*({chinese_number})\s*(?:元|块)", query
    ):
        limits.append(float(_chinese_amount(match.group(1))))
    # Prices are stored in cents. "低于/under" excludes the named amount.
    for match in re.finditer(
        r"(?:低于|不到|少于)\s*[¥￥$]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)"
        r"|under\s*[¥￥$]?\s*(\d+(?:\.\d+)?)",
        query, flags=re.IGNORECASE,
    ):
        amount = match.group(1) or match.group(2)
        limits.append((math.ceil(float(amount) * 100) - 1) / 100)
    for match in re.finditer(rf"(?:低于|不到|少于)\s*({chinese_number})\s*(?:元|块)", query):
        limits.append((_chinese_amount(match.group(1)) * 100 - 1) / 100)
    return min(limits) if limits else None


def _category_from_query(query: str, categories: list[str]) -> str | None:
    # "给手机用" describes compatibility, not the product being requested.
    target_phrase = re.sub(r"给[^，。?？]{1,12}?用(?:的)?", "", query)
    mentions: list[tuple[int, int, str]] = []
    for category in categories:
        for match in re.finditer(re.escape(category), target_phrase):
            before = target_phrase[max(0, match.start() - 8):match.start()]
            if re.search(r"(?:不要|不想要|不需要|别推荐|排除|不是|非)\s*$", before):
                continue
            mentions.append((match.start(), len(category), category))
    direct = max(mentions, default=(0, 0, None))[2]
    accessory = re.search(r"充电器|充电头|电源适配器|移动电源|数据线", query)
    if "配件" in categories and accessory and (
        direct is None
        or (
            direct in {"手机", "平板"}
            and accessory.start() > query.rfind(direct)
        )
    ):
        return "配件"
    if "耳机" in categories and re.search(r"耳朵|耳道", query) and re.search(
        r"佩戴|听|跑步|运动|塞住", query
    ):
        return "耳机"
    if direct is not None:
        return direct
    return None


def _matches_explicit_features(query: str, product: Product) -> bool:
    """Enforce a small, auditable feature rule before lexical retrieval.

    This is deliberately narrow. Other ambiguous attributes need a structured
    catalog field or a human-labeled parser before becoming hard constraints.
    """
    # These claims are explicit catalog capabilities, not BM25 relevance hints.
    # A capability needs an explicit description or tag assertion.
    wireless_connection_claim = any(
        rule.name == "无线连接" for rule in required_claims(query)
    )
    for feature in ("降噪", "防水", "水下拍照", "防汗", "无线", "静音", "手写笔"):
        if feature not in query:
            continue
        if re.search(
            rf"(?:不需要|无需|不要求)\s*{re.escape(feature)}"
            rf"|{re.escape(feature)}\s*可有可无",
            query,
        ):
            # An optional feature is neither required nor forbidden.
            continue
        if feature == "无线" and wireless_connection_claim:
            # The source-backed claim understands Bluetooth and 2.4 GHz as
            # wireless links. Keep wireless charging as a separate condition.
            if "无线充电" in query and not any(
                _feature_polarities(text, "无线充电")[0]
                for text in (product.description, *product.tags)
            ):
                return False
            continue
        denied = bool(
            re.search(
                rf"(?:不要|不想要|没有|不带|不含|不支持|不提供|不具备|无|非)"
                rf"(?:.{{0,2}}?){re.escape(feature)}",
                query,
            )
        )
        polarities = [
            _feature_polarities(text, feature)
            for text in (product.description, *product.tags)
        ]
        has_feature = any(positive for positive, _ in polarities)
        explicitly_absent = any(negative for _, negative in polarities)
        if (denied and (has_feature or not explicitly_absent)) or (
            not denied and not has_feature
        ):
            return False

    description = product.description.lower()
    tags = " ".join(product.tags).lower()
    normalized_query = re.sub(r"\s+", "", query.lower())
    searchable = re.sub(r"\s+", "", description + " " + tags)
    if missing_claims(query, description + " " + tags):
        return False
    # An explicit use scenario needs some source overlap beyond the category.
    # This is a conservative lexical gate, not open-domain semantic proof.
    scenario_source = re.sub(
        r"\s+", "", description + tags + "".join(product.reviews).lower()
    )
    for scenario in re.findall(
        r"(?:适合|用于)([^，。；？?]{2,18}?)的"
        r"(?:耳机|手机|平板|显示器|键盘|配件)",
        normalized_query,
    ):
        characters = {char for char in scenario if "\u3400" <= char <= "\u9fff"}
        pairs = {scenario[index:index + 2] for index in range(len(scenario) - 1)}
        if characters and (
            sum(char in scenario_source for char in characters) / len(characters) < 0.75
            or not any(pair in scenario_source for pair in pairs)
        ):
            return False
    # An absence claim needs explicit negative source text. Silence about an
    # attribute does not establish that a product lacks it.
    for match in re.finditer(
        r"(?:(?<!有)没有|不带|不含|不要|不支持|不提供|不具备|无(?!线|需|法))"
        r"((?:(?!的)[\u3400-\u9fffA-Za-z0-9-]){2,12}?)"
        r"(?:的)?(?:手机|耳机|平板|显示器|键盘)",
        normalized_query,
    ):
        feature = match.group(1)
        if any(
            condition.field == "wearing_style"
            and condition.operator == "!="
            and condition.expected == feature
            for condition in parse_conditions(query)
        ):
            # The typed style check recognizes mutually exclusive styles.
            continue
        explicitly_absent = any(
            _feature_polarities(text, feature)[1]
            for text in (product.description, *product.tags)
        )
        if feature == "主动降噪":
            # An explicit absence of all noise cancellation also rules out ANC.
            explicitly_absent = explicitly_absent or any(
                _feature_polarities(text, "降噪")[1]
                for text in (product.description, *product.tags)
            )
        if not explicitly_absent:
            return False
    # For a requested capability, only an explicit capability statement can
    # establish it. The category name alone cannot support “会飞的手机”.
    for match in re.finditer(
        r"会([\u3400-\u9fffA-Za-z0-9-])的"
        r"(?:手机|耳机|平板|显示器|键盘)",
        normalized_query,
    ):
        feature = match.group(1)
        if not re.search(
            rf"(?:会|能|可以|支持|具备){re.escape(feature)}"
            rf"|{re.escape(feature)}(?:功能|能力)",
            searchable,
        ):
            return False
    # Treat explicit capabilities as hard constraints. Mere lexical similarity
    # to another product does not prove an absent capability.
    for feature in ("折叠屏", "内置投影仪", "8k", "热插拔", "机械轴"):
        if feature.lower() not in normalized_query:
            continue
        if feature == "机械轴":
            supported = "机械" in searchable
        else:
            supported = feature.lower() in searchable
        if not supported:
            return False
    if re.search(r"(?:不能|不要|不想|不愿|不)\s*堵(?:住)?耳道", query):
        if not ("不堵耳道" in description or "开放式" in searchable):
            return False
    if re.search(r"不能被塞住|不塞耳朵|不入耳", query):
        if "开放式" not in searchable and "不堵耳道" not in description:
            return False
    # The legacy lexical gate only applies to a positive in-ear request.
    # A typed exclusion must be free to admit an open-ear source instead.
    excludes_in_ear = any(
        condition.field == "wearing_style"
        and condition.expected == "入耳式"
        and condition.operator == "!="
        for condition in parse_conditions(query)
    )
    if "入耳" in query and not excludes_in_ear and "入耳" not in searchable:
        return False
    if re.search(r"同时切换两台设备|切换两台设备|双设备切换", query):
        if not ("双设备" in searchable or "多设备" in searchable):
            return False
    if "独立数字区" in query and re.search(r"(?:不要|不需要|没有|无)独立数字区", normalized_query):
        if "数字区" in tags or (
            "独立数字区" in description and
            not re.search(r"(?:没有|无|不含|不带)独立数字区", description)
        ):
            return False
    requested_resolutions = {
        (int(width), int(height))
        for width, height in re.findall(r"(?<!\d)(\d{3,4})\s*[×xX]\s*(\d{3,4})(?!\d)", query)
    }
    if requested_resolutions:
        documented_resolutions = {
            (int(width), int(height))
            for width, height in re.findall(
                r"(?<!\d)(\d{3,4})\s*[×xX]\s*(\d{3,4})(?!\d)", product.description
            )
        }
        if not requested_resolutions <= documented_resolutions:
            return False
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(hz|w|英寸|gb|mah|克|小时)",
        query.lower(), re.IGNORECASE,
    ):
        optional_before = query[max(0, match.start() - 18):match.start()]
        optional_after = query[match.end():match.end() + 18]
        if re.search(r"(?:不需要|无需|不要求|不用)\s*$", optional_before) or re.match(
            r"\s*(?:充电|快充|功率|规格|功能)?\s*"
            r"(?:可有可无|不需要|无需|不要求)", optional_after
        ):
            continue
        wanted = float(match.group(1))
        unit = match.group(2).lower()
        before = query[max(0, match.start() - 8):match.start()]
        after = query[match.end():match.end() + 4]
        actual = [
            float(value) for value in re.findall(
                rf"(\d+(?:\.\d+)?)\s*{re.escape(unit)}",
                description.lower(), re.IGNORECASE,
            )
        ]
        if unit == "gb":
            after_amount = query[match.end():match.end() + 5].lower()
            before_amount = re.split(
                r"[，。；、,.]", query[max(0, match.start() - 12):match.start()].lower()
            )[-1]
            nearby = after_amount if re.search(
                r"内存|ram|存储|储存|容量", after_amount
            ) else before_amount
            field = (
                r"(?:内存|运行内存|ram)" if re.search(r"内存|ram", nearby)
                else (r"(?:存储|储存|容量)" if re.search(r"存储|储存|容量", nearby) else None)
            )
            if field:
                actual = [
                    float(value) for value in re.findall(
                        rf"(\d+(?:\.\d+)?)\s*gb\s*{field}",
                        description.lower(), re.IGNORECASE,
                    )
                ]
        if unit == "小时":
            clause = query[max(
                query.rfind(separator, 0, match.start()) for separator in "，。；、"
            ) + 1:match.start()]
            marker = (
                r"(?:单次|连续)" if re.search(r"单次|连续", clause)
                else (r"(?:充电盒|搭配充电盒)" if "充电盒" in clause else None)
            )
            if marker:
                actual = [
                    float(value) for value in re.findall(
                        rf"{marker}[^，。；、]{{0,8}}?(\d+(?:\.\d+)?)\s*小时",
                        description.lower(), re.IGNORECASE,
                    )
                ]
        if re.search(r"不要|不需要|排除|避免", before):
            if any(abs(value - wanted) < 0.001 for value in actual):
                return False
        elif re.search(r"至少|不少于|不低于|不能低于|不得低于|不能少于|别小于|不小于|起码", before) or re.search(
            r"以上", after
        ):
            if not any(value >= wanted for value in actual):
                return False
        elif re.search(r"不超过|最多|至多|不高于", before) or re.search(
            r"以内|以下", after
        ):
            if not any(value <= wanted for value in actual):
                return False
        elif re.search(r"不到|低于|少于|小于", before):
            if not any(value < wanted for value in actual):
                return False
        elif re.search(r"高于|超过|大于", before):
            if not any(value > wanted for value in actual):
                return False
        elif not any(abs(value - wanted) < 0.001 for value in actual):
            return False
    mobile_power_excluded = bool(re.search(
        r"(?:不要|不算|不考虑|排除)\s*移动电源|移动电源.{0,4}(?:不用算|不算|排除)",
        query,
    ))
    if mobile_power_excluded:
        if "移动电源" in product.name:
            return False
    elif "移动电源" in query and "移动电源" not in product.name:
        return False
    if re.search(r"充电器|充电头|电源适配器", query) and "充电器" not in product.name:
        return False
    if re.search(r"两口|双口", query) and "双口" not in searchable:
        return False
    if "usb-c" in query.lower() and "usb-a" in query.lower():
        if not ("usb-c" in description and "usb-a" in description):
            return False
    if re.search(r"(?<!不)附线|(?<!不)带线|(?<!不)附.{0,8}线", query):
        if not re.search(r"附.{0,8}线", description) or re.search(
            r"不附.{0,8}线", description
        ):
            return False
    if re.search(r"(?:全是|都是|全部是)\s*usb-c", query, re.IGNORECASE):
        # A mixed USB-A/USB-C layout cannot substantiate an all-USB-C request.
        if "usb-a" in searchable or "usb-c" not in searchable:
            return False
    for count in ("单口", "双口", "三口", "四口"):
        if count in query and count not in searchable:
            return False
    # A strong capability request is a hard constraint. Unknown capabilities
    # produce abstention instead of a category-only recommendation.
    for phrase in re.findall(
        r"(?:有没有|能|支持|具备|内置)\s*"
        r"([\u3400-\u9fffA-Za-z0-9-]{2,18}?)"
        r"(?:功能|的(?:手机|耳机|平板|显示器|键盘|充电器))",
        query,
    ):
        if re.match(r"推荐|选|买|找", phrase) or re.search(
            r"\d|至少|至多|最高|输出|单口|双口|预算|元", phrase
        ) or any(rule.query.search(phrase) for rule in required_claims(query)):
            continue
        capability = re.sub(r"^(?:带|有)", "", phrase)
        if capability.lower() not in searchable:
            return False
    return True


_CONFLICT_FEATURES = (
    "主动降噪", "折叠屏", "内置投影仪", "热插拔", "机械轴",
    "摄像头", "蜂窝网络", "双设备", "AAC",
)


def _feature_polarities(text: str, feature: str) -> tuple[bool, bool]:
    """Find explicit positive/negative mentions of one controlled capability."""
    positive = negative = False
    for match in re.finditer(re.escape(feature), text, re.IGNORECASE):
        before = text[max(0, match.start() - 12):match.start()]
        after = text[match.end():match.end() + 2]
        direct_denial = re.search(
            r"(?:没有|不带|不含|不支持|不能支持|无法支持|不提供|不具备|无)\s*$",
            before,
        )
        nearby_denial = re.search(
            r"(?:没有|不带|不含|不支持|不能支持|无法支持|不提供|不具备|无)"
            r"[^，。；、]{0,6}$",
            before,
        )
        nearby_assertion = re.search(
            r"(?:(?<!没)有|(?<!不)支持|(?<!不)具备|(?<!不)提供|配备)"
            r"[\u3400-\u9fff]{0,2}$",
            before,
        )
        if (direct_denial or (nearby_denial and not nearby_assertion)) and not after.startswith("问题"):
            negative = True
        else:
            positive = True
    return positive, negative


def _has_product_source_conflict(product: Product) -> bool:
    """Exclude a product when its current sources explicitly disagree on a capability."""
    texts = (product.description, *product.tags, *product.reviews)
    for feature in _CONFLICT_FEATURES:
        mentions = [_feature_polarities(text, feature) for text in texts]
        if any(positive for positive, _ in mentions) and any(
            negative for _, negative in mentions
        ):
            return True
    return False


def _documents(products: list[Product]) -> list[EvidenceDocument]:
    documents: list[EvidenceDocument] = []
    for product in products:
        documents.append(
            EvidenceDocument(
                source_id=f"{product.product_id}:description",
                product_id=product.product_id,
                source_type="description",
                text=f"{product.name} {product.category} {' '.join(product.tags)} {product.description}",
            )
        )
        for index, review in enumerate(product.reviews, start=1):
            documents.append(
                EvidenceDocument(
                    source_id=f"{product.product_id}:review:{index}",
                    product_id=product.product_id,
                    source_type="review",
                    text=review,
                )
            )
    return documents


def _excerpt(text: str, limit: int = 100) -> str:
    clean = " ".join(text.split())
    return clean[:limit] + ("…" if len(clean) > limit else "")


class ShoppingService:
    """Coordinates independent domain nodes and keeps repository calls testable."""

    def __init__(
        self, store: CatalogStore, composer: AnswerComposer, settings: Settings | None = None
    ):
        self.store = store
        self.composer = composer
        self.settings = settings or Settings()
        self.embedding = create_embedding(self.settings)
        self.categories = sorted(
            {product.category for product in store.list_products(in_stock_only=False)},
            key=len,
            reverse=True,
        )
        self.conversations = ConversationStore(store.engine)
        self.experiments = (
            ExperimentStore(self.settings.database_url)
            if self.settings.experiment_enabled else None
        )
        self.planner = PlanningAgent()
        self.verifier = VerificationAgent(
            store, _matches_explicit_features, _has_product_source_conflict
        )
        self._index_lock = threading.RLock()
        self.refresh_index()
        self.product_graph = self._build_graph()
        self.product_agent = ProductAgent(
            self.product_graph, self.settings.tool_timeout_seconds
        )
        self.graph = self._build_router_graph()

    def refresh_index(self) -> None:
        """Rebuild sparse and vector indexes from current, versioned sources."""
        with self._index_lock:
            self._refresh_index_locked()

    def _refresh_index_locked(self) -> None:
        # Capture a consistent catalog revision. An import between the document
        # read and signature read must not mark an older index as current.
        for _ in range(3):
            revision = self.store.document_revision_signature()
            products = self.store.list_products(in_stock_only=False)
            source_documents = self.store.list_documents()
            if revision == self.store.document_revision_signature():
                break
        else:
            raise RuntimeError("Catalog changed while rebuilding the evidence index")
        self.trusted_product_ids = {
            source.product_id
            for source in source_documents
            if source.product_id is not None and source.license != "unspecified"
        }
        documents = [
            EvidenceDocument(
                source_id=source.source_id,
                product_id=source.product_id,
                source_type=source.source_type,
                text=source.text,
                source_url=source.source_url or "",
                license=source.license,
                updated_at=source.updated_at,
                version=source.version,
            )
            for source in source_documents
        ]
        if not documents:
            documents = _documents(products)
        self.retriever = EvidenceIndex(
            [document for document in documents if document.product_id is not None],
            self.embedding,
            lazy_vector=self.embedding.provider == "bailian",
            on_search=lambda result, index: self._record_retrieval("product", result, index),
        )
        self.faq_retriever = EvidenceIndex(
            [document for document in documents if document.source_type == "answer"],
            self.embedding,
            lazy_vector=self.embedding.provider == "bailian",
            on_search=lambda result, index: self._record_retrieval("faq", result, index),
        )
        self.knowledge_agent = KnowledgeAgent(
            self.store, self.faq_retriever, self.settings.tool_timeout_seconds
        )
        self.categories = sorted({p.category for p in products}, key=len, reverse=True)
        self._indexed_revisions = revision

    def _record_retrieval(self, route: str, result: SearchResult, index: EvidenceIndex) -> None:
        records = _retrieval_records.get()
        if records is None:
            return
        used = result.actual_mode in {"vector", "hybrid"} and bool(index.documents)
        records[route] = RetrievalDiagnostic(
            requested_mode=result.requested_mode,
            actual_mode=result.actual_mode,
            actual_provider=self.embedding.provider if used else "bm25",
            embedding_used=used,
            status="fallback" if result.fallback_reason else "success",
            fallback_reason=result.fallback_reason,
            index_state=("failed" if index.vector_error else
                         "ready" if index.vector is not None else "lazy"),
        )

    def _refresh_if_changed(self) -> None:
        current = self.store.document_revision_signature()
        if current != self._indexed_revisions:
            with self._index_lock:
                if self.store.document_revision_signature() != self._indexed_revisions:
                    self._refresh_index_locked()

    def _build_graph(self):
        graph = StateGraph(ShoppingState)
        graph.add_node("profile", self._load_profile)
        graph.add_node("candidates", self._find_candidates)
        graph.add_node("join", lambda state: {})
        graph.add_node("retrieve", self._retrieve_evidence)
        graph.add_node("rank", self._rank_products)
        graph.add_node("verify_stock", self._verify_stock)
        graph.add_node("empty", self._empty_answer)

        graph.add_edge(START, "profile")
        graph.add_edge(START, "candidates")
        graph.add_edge(["profile", "candidates"], "join")
        graph.add_conditional_edges(
            "join",
            lambda state: "retrieve" if state.get("candidates") else "empty",
            {"retrieve": "retrieve", "empty": "empty"},
        )
        graph.add_edge("retrieve", "rank")
        graph.add_edge("rank", "verify_stock")
        graph.add_edge("verify_stock", END)
        graph.add_edge("empty", END)
        return graph.compile()

    async def _load_profile(self, state: ShoppingState) -> dict[str, Any]:
        request = state["request"]
        try:
            counts = await asyncio.wait_for(
                asyncio.to_thread(self.store.get_user_category_counts, request.user_id),
                timeout=self.settings.tool_timeout_seconds,
            )
        except Exception:
            counts = {}
        return {"profile_counts": counts}

    async def _find_candidates(self, state: ShoppingState) -> dict[str, Any]:
        request = state["request"]
        inferred_category = _category_from_query(request.query, self.categories)
        category = request.category or inferred_category
        parsed_budget = _budget_from_query(request.query)
        if request.max_price is None:
            max_price = parsed_budget
        elif parsed_budget is None:
            max_price = request.max_price
        else:
            max_price = min(request.max_price, parsed_budget)
        products = await asyncio.wait_for(
            asyncio.to_thread(self.store.list_products, category, max_price, True),
            timeout=self.settings.tool_timeout_seconds,
        )
        trusted = [
            product for product in products
            if product.product_id in self.trusted_product_ids
        ]
        source_conflict = any(_has_product_source_conflict(product) for product in trusted)
        conditions = parse_conditions(request.query, category=category, max_price=max_price)
        condition_judgments = [
            judge_conditions(conditions, product, self.store) for product in trusted
        ]
        condition_conflict = any(
            judgment.conflict for group in condition_judgments for judgment in group
        )
        condition_unknown = any(
            judgment.status == "unknown" for group in condition_judgments
            for judgment in group
        )
        products = [
            product for product in trusted
            if not _has_product_source_conflict(product)
            and _matches_explicit_features(request.query, product)
            and all(
                judgment.status == "supported" for judgment in
                judge_conditions(conditions, product, self.store)
            )
        ]
        context_ids = state.get("context_product_ids", ())
        if context_ids:
            products = [product for product in products if product.product_id in context_ids]
        return {
            "candidates": products, "category": category, "max_price": max_price,
            "source_conflict": source_conflict,
            "conditions": conditions,
            "condition_conflict": condition_conflict,
            "condition_unknown": condition_unknown,
        }

    async def _retrieve_evidence(self, state: ShoppingState) -> dict[str, Any]:
        product_ids = {product.product_id for product in state["candidates"]}
        query = state["request"].query
        requested_mode = state.get("retrieval_mode", "bm25")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.retriever.search, query, product_ids,
                    max(30, state["request"].num_items * 8), requested_mode,
                ),
                timeout=self.settings.tool_timeout_seconds,
            )
            warnings = [result.fallback_reason] if result.fallback_reason else []
            return {
                "hits": result.hits, "actual_mode": result.actual_mode,
                "warnings": warnings,
            }
        except Exception as exc:
            return {
                "hits": [], "actual_mode": "bm25",
                "warnings": [f"商品检索失败：{type(exc).__name__}；仅使用商品库事实。"],
            }

    async def _rank_products(self, state: ShoppingState) -> dict[str, Any]:
        evidence_score: dict[str, float] = {}
        for hit in state.get("hits", []):
            product_id = hit.document.product_id
            evidence_score[product_id] = max(evidence_score.get(product_id, 0.0), hit.score)
        profile_counts = state.get("profile_counts", {})

        def rank_key(product: Product) -> tuple[float, float, str]:
            score = evidence_score.get(product.product_id, 0.0)
            score += 0.2 * math.log1p(profile_counts.get(product.category, 0))
            return (-score, product.price, product.product_id)

        return {"ranked": sorted(state["candidates"], key=rank_key)}

    async def _verify_stock(self, state: ShoppingState) -> dict[str, Any]:
        request = state["request"]
        has_explicit_claims = bool(required_claims(request.query))
        by_product: dict[str, list[RetrievalHit]] = {}
        for hit in state.get("hits", []):
            by_product.setdefault(hit.document.product_id, []).append(hit)

        recommendations: list[Recommendation] = []
        for candidate in state.get("ranked", []):
            # A fresh read is required because stock may change after candidate search.
            current = await asyncio.to_thread(self.store.get_product, candidate.product_id)
            if current is None or current.stock <= 0:
                continue
            category = state.get("category")
            if category is not None and current.category != category:
                continue
            if not _matches_explicit_features(request.query, current):
                continue
            max_price = state.get("max_price")
            if max_price is not None and current.price > max_price:
                continue
            judgments = judge_conditions(state.get("conditions", ()), current, self.store)
            if not judgments_are_current(judgments, self.store):
                continue
            description_source = await asyncio.to_thread(
                self.store.get_product_description_document, current.product_id
            )
            if has_explicit_claims and (
                description_source is None or missing_claims(
                    request.query, description_source.text
                )
            ):
                continue
            evidence = []
            for hit in by_product.get(current.product_id, [])[:2]:
                source = self.store.get_document(hit.document.source_id)
                quote = verbatim_source_text(source) if source is not None else ""
                if source is not None and quote and source.product_id == current.product_id:
                    evidence.append(EvidenceRef(
                        source_id=source.source_id,
                        source_type=source.source_type,
                        excerpt=_excerpt(quote),
                    ))
            description_quote = verbatim_source_text(description_source) if description_source is not None else ""
            if description_source is not None and description_quote:
                evidence = [
                    EvidenceRef(
                        source_id=description_source.source_id,
                        source_type="description",
                        excerpt=claim_excerpt(request.query, description_quote),
                    ),
                    *[
                        ref for ref in evidence
                        if ref.source_id != description_source.source_id
                    ][:1],
                ]
            if evidence:
                reason = f"相关资料提到：{evidence[0].excerpt} [{evidence[0].source_id}]"
            else:
                reason = f"属于{current.category}类目，价格¥{current.price:g}；暂无匹配的详细资料。"
            recommendations.append(
                Recommendation(
                    product_id=current.product_id,
                    name=current.name,
                    category=current.category,
                    price=current.price,
                    stock=current.stock,
                    reason=reason,
                    evidence=evidence,
                    condition_judgments=list(judgments),
                )
            )
            if len(recommendations) == request.num_items:
                break
        warnings: list[str] = list(state.get("warnings", []))
        if len(recommendations) < request.num_items:
            warnings.append("满足条件且当前有货的商品少于请求数量。")
        if not state.get("hits"):
            warnings.append("未检索到匹配的商品资料，推荐理由仅使用基本商品信息。")
        if state.get("condition_unknown"):
            warnings.append("部分明确条件无法从当前来源核实，相关商品已排除。")
        if state.get("condition_conflict"):
            warnings.append("当前来源存在条件事实冲突，相关商品已排除。")
        return {"recommendations": recommendations, "warnings": warnings}

    async def _compose_answer(self, state: ShoppingState) -> dict[str, Any]:
        answer, llm_used, warning = await self.composer.compose(
            state["request"].query, state.get("recommendations", [])
        )
        warnings = list(state.get("warnings", []))
        if warning:
            warnings.append(warning)
        return {"answer": answer, "llm_used": llm_used, "warnings": warnings}

    async def _empty_answer(self, state: ShoppingState) -> dict[str, Any]:
        return {
            "recommendations": [],
            "hits": [],
            "answer": "暂时没有满足条件且有库存的商品，请调整类目或预算。",
            "llm_used": False,
            "warnings": ["商品筛选结果为空。"],
            "condition_conflict": state.get("condition_conflict", False),
            "condition_unknown": state.get("condition_unknown", False),
        }

    def _build_router_graph(self):
        """The planner changes which tools execute for each request type."""
        graph = StateGraph(RoutingState)
        graph.add_node("plan", self._plan_task)
        graph.add_node("product_agent", self._run_product_agent)
        graph.add_node("knowledge_agent", self._run_knowledge_agent)
        graph.add_node("mixed_agents", self._run_mixed_agents)
        graph.add_node("verification_agent", self._finalize)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges(
            "plan",
            lambda state: state["decision"].route,
            {
                "product": "product_agent",
                "faq": "knowledge_agent",
                "mixed": "mixed_agents",
            },
        )
        for node in ("product_agent", "knowledge_agent", "mixed_agents"):
            graph.add_edge(node, "verification_agent")
        graph.add_edge("verification_agent", END)
        return graph.compile()

    async def _plan_task(self, state: RoutingState) -> dict[str, Any]:
        decision = self.planner.plan(PlanningInput(
            state["request"], tuple(self.categories),
            state.get("history_product_ids", ()),
        ))
        return {"decision": decision}

    async def _run_product_agent(self, state: RoutingState) -> dict[str, Any]:
        decision = state["decision"]
        outcome = await self.product_agent.run(ProductTask(
            state["request"], state["mode"], decision.context_product_ids
        ))
        return {"product_state": outcome.state}

    async def _run_knowledge_agent(self, state: RoutingState) -> dict[str, Any]:
        result = await self.knowledge_agent.run(
            KnowledgeInput(state["request"].query, state["mode"])
        )
        return {"knowledge": result}

    async def _run_mixed_agents(self, state: RoutingState) -> dict[str, Any]:
        decision = state["decision"]
        product_task = ProductTask(
            state["request"], state["mode"], decision.context_product_ids
        )
        knowledge_task = KnowledgeInput(state["request"].query, state["mode"])
        product, knowledge = await asyncio.gather(
            self.product_agent.run(product_task),
            self.knowledge_agent.run(knowledge_task),
        )
        return {"product_state": product.state, "knowledge": knowledge}

    async def _finalize(self, state: RoutingState) -> dict[str, Any]:
        decision = state["decision"]
        product = state.get("product_state", {})
        knowledge = state.get("knowledge")
        request = state["request"]
        verification = await asyncio.wait_for(
            asyncio.to_thread(
                self.verifier.run,
                VerificationInput(
                    request,
                    tuple(product.get("recommendations", [])),
                    product.get("category"),
                    product.get("max_price"),
                ),
            ),
            timeout=self.settings.tool_timeout_seconds,
        )
        recommendations = list(verification.recommendations)
        if decision.route in {"product", "mixed"}:
            product_answer, llm_used, model_warning = await self.composer.compose(
                request.query, recommendations
            )
        else:
            product_answer, llm_used, model_warning = "", False, None
        warnings = list(product.get("warnings", []))
        if verification.removed_count:
            warnings.append("最终复核移除了已变更或缺货的商品。")
        source_conflict = bool(product.get("source_conflict") or verification.conflicted_count)
        if source_conflict:
            warnings.append("部分商品资料对能力描述存在冲突，相关商品已排除。")
            product_answer += " 部分商品的来源对能力描述有冲突，暂无法核实这些商品。"
        if product.get("condition_conflict") and not source_conflict:
            warnings.append("部分商品当前来源对硬条件有冲突，相关商品已排除。")
            product_answer += " 部分商品当前来源有冲突，暂无法核实。"
        if product.get("condition_unknown") and not recommendations:
            warnings.append("明确条件无法从当前来源核实；不推荐未知商品。")
            product_answer += " 明确条件无法核实，请查看更完整的商品资料。"
        if model_warning:
            warnings.append(model_warning)
        knowledge_evidence = []
        if knowledge is not None:
            warnings.extend(knowledge.warnings)
            try:
                knowledge_evidence = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: [
                            ref for ref in knowledge.evidence
                            if self.store.get_document(ref.source_id) is not None
                        ]
                    ),
                    timeout=self.settings.tool_timeout_seconds,
                )
            except Exception as exc:
                warnings.append(f"FAQ 最终来源核验失败：{type(exc).__name__}")
            if knowledge.evidence and not knowledge_evidence:
                warnings.append("FAQ 来源已失效。")
                knowledge = knowledge.__class__(
                    "FAQ 来源已失效，无法核实答案。",
                    (), (), knowledge.warnings, knowledge.actual_mode,
                )
        if decision.route == "faq":
            answer = knowledge.answer
        elif decision.route == "mixed":
            faq_answer = knowledge.answer
            answer = product_answer + " " + faq_answer
        else:
            answer = product_answer
        trace = ["planner"]
        if decision.route in {"product", "mixed"}:
            if "profile_counts" in product:
                trace.append("profile")
            if "candidates" in product:
                trace.append("catalog")
            if "actual_mode" in product:
                trace.append(f"product_retrieval:{product['actual_mode']}")
            if "ranked" in product:
                trace.append("rank")
            if "recommendations" in product and "ranked" in product:
                trace.append("stock_verify")
            if any("商品工具失败" in warning for warning in product.get("warnings", [])):
                trace.append("product_agent_failure")
        if decision.route in {"faq", "mixed"}:
            if any("FAQ 检索失败" in warning for warning in knowledge.warnings):
                trace.append("faq_retrieval_failure")
            else:
                trace.append(f"faq_retrieval:{knowledge.actual_mode}")
        trace.extend(["source_verify", "compose"])
        effective_modes: dict[str, str] = {}
        if decision.route in {"product", "mixed"} and "actual_mode" in product:
            effective_modes["product"] = product["actual_mode"]
        if decision.route in {"faq", "mixed"}:
            effective_modes["faq"] = knowledge.actual_mode
        effective_mode = effective_modes.get("product") or effective_modes.get("faq")
        assignment = state.get("assignment")
        response = ShopResponse(
            request_id=state["request_id"],
            user_id=request.user_id,
            answer=answer,
            recommendations=recommendations,
            retrieval_used=bool(product.get("hits")) or bool(knowledge_evidence),
            llm_used=llm_used,
            warnings=warnings,
            route=decision.route,
            tool_trace=trace,
            retrieval_mode=effective_mode,
            requested_retrieval_mode=state["mode"],
            effective_retrieval_modes=effective_modes,
            conversation_id=request.conversation_id,
            experiment_id=assignment.experiment_id if assignment else None,
            variant=assignment.variant if assignment else None,
            routing_reason=decision.rationale,
            knowledge_evidence=knowledge_evidence,
        )
        return {"response": response}

    async def recommend(self, request: ShopRequest) -> ShopResponse:
        started = time.perf_counter()
        request_id = str(uuid.uuid4())
        preflight_warnings: list[str] = []
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._refresh_if_changed),
                timeout=self.settings.tool_timeout_seconds,
            )
        except Exception as exc:
            preflight_warnings.append(f"索引刷新失败：{type(exc).__name__}")
        try:
            history = (
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self.conversations.recent,
                        request.user_id, request.conversation_id,
                    ),
                    timeout=self.settings.tool_timeout_seconds,
                )
                if request.conversation_id else []
            )
        except Exception as exc:
            history = []
            preflight_warnings.append(f"会话读取失败：{type(exc).__name__}")
        previous_ids = next(
            (turn.product_ids for turn in reversed(history) if turn.product_ids),
            (),
        )
        try:
            assignment = (
                await asyncio.wait_for(
                    asyncio.to_thread(self.experiments.assign, request.user_id),
                    timeout=self.settings.tool_timeout_seconds,
                )
                if self.experiments else None
            )
        except Exception as exc:
            assignment = None
            preflight_warnings.append(f"实验分桶失败：{type(exc).__name__}")
        mode = (
            assignment.retrieval_mode if assignment else
            (request.retrieval_mode or self.settings.retrieval_mode)
        )
        if mode not in {"bm25", "vector", "hybrid"}:
            mode = "bm25"
        records: dict[str, RetrievalDiagnostic] = {}
        token = _retrieval_records.set(records)
        try:
            result = await self.graph.ainvoke({
                "request": request, "mode": mode,
                "history_product_ids": previous_ids,
                "assignment": assignment,
                "request_id": request_id,
            })
            response = result["response"]
        except Exception as exc:
            response = ShopResponse(
                request_id=request_id, user_id=request.user_id,
                answer="工具暂时不可用，无法核实商品或服务信息。",
                warnings=[f"工具流程失败：{type(exc).__name__}"],
                route="product", tool_trace=["planner", "failure_fallback"],
                requested_retrieval_mode=mode, conversation_id=request.conversation_id,
            )
        finally:
            _retrieval_records.reset(token)
        response.configured_embedding_provider = self.embedding.provider
        response.configured_embedding_model = self.embedding.model
        # A timed-out worker can finish later. Copy completed records now and
        # never claim vector success for an incomplete tool invocation.
        response.retrieval_diagnostics = {
            key: value.model_copy(deep=True) for key, value in records.copy().items()
            if response.effective_retrieval_modes.get(key) == value.actual_mode
        }
        for route in (("product", "faq") if response.route == "mixed" else (response.route,)):
            if route not in response.retrieval_diagnostics:
                actual = response.effective_retrieval_modes.get(route)
                failed = any("失败" in warning for warning in response.warnings)
                response.retrieval_diagnostics[route] = RetrievalDiagnostic(
                    requested_mode=mode, actual_mode=actual,
                    actual_provider=None, embedding_used=False,
                    status="failed" if failed else "not_used",
                    fallback_reason="; ".join(response.warnings) if failed else None,
                    index_state="unknown",
                )
        response.warnings.extend(preflight_warnings)
        if self.experiments and assignment is not None:
            try:
                executed_modes = list(response.effective_retrieval_modes.values())
                actual_mode = (
                    "bm25" if "bm25" in executed_modes
                    else (executed_modes[0] if executed_modes else None)
                )
                fallback_reason = (
                    "检索未执行或实际策略与分配策略不同"
                    if actual_mode != mode else None
                )
                self.experiments.record_exposure(
                    request.user_id, request_id,
                    [item.product_id for item in response.recommendations],
                    actual_retrieval_mode=actual_mode,
                    fallback_reason=fallback_reason,
                )
            except Exception as exc:
                response.warnings.append(f"实验曝光记录失败：{type(exc).__name__}")
                response.experiment_id = None
                response.variant = None
        if request.conversation_id:
            try:
                self.conversations.append(
                    request.user_id, request.conversation_id, request_id,
                    request.query, response.route,
                    [item.product_id for item in response.recommendations],
                    [
                        ref.source_id for item in response.recommendations
                        for ref in item.evidence
                    ] + [ref.source_id for ref in response.knowledge_evidence],
                )
            except Exception as exc:
                response.warnings.append(f"对话状态保存失败：{type(exc).__name__}")
        response.total_latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return response


def create_service(settings: Settings | None = None) -> ShoppingService:
    settings = settings or Settings.from_env()
    store = CatalogStore(settings.database_url)
    store.initialize()
    if settings.seed_demo and not store.list_products(in_stock_only=False):
        seed_demo_data(store)
    if settings.seed_demo:
        seed_demo_faq_data(store)
    return ShoppingService(store, AnswerComposer(settings), settings)
