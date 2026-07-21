"""Deterministic red-flag checks for source-sensitive presentation claims.

This is intentionally narrower than general fact checking.  It identifies a
small set of high-risk claim shapes that a presentation model must either copy
from the supplied source or explicitly associate with a valid source fact.
Ordinary framing, recommendations, and headings remain available to the model.
"""

from __future__ import annotations

import re
import unicodedata


QualitativeClaim = tuple[str, str]


_ENGLISH_ATTRIBUTION_RE = re.compile(
    r"\b(?:"
    r"(?:(?i:ceo|cto|cfo|coo|chief(?:\s+\w+){0,3}|founder|president|"
    r"vice\s+president|vp|director|manager|spokesperson|analyst|researcher|professor|dr\.?))"
    r"(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}"
    r"|[A-Z][A-Za-z&.'-]*(?:\s+[A-Z][A-Za-z&.'-]*){0,2}"
    r")\s+(?i:said|says|stated|states|reported|reports|announced|announces|"
    r"confirmed|confirms|revealed|reveals|claimed|claims|told)\b"
)
_CHINESE_ATTRIBUTION_RE = re.compile(
    r"(?:[\u3400-\u9fff]{2,16}|[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,2})"
    r"(?:表示|称|指出|宣布|透露|确认|坦言|认为)"
)
_ENGLISH_PERSON_FACT_RE = re.compile(
    r"\b[A-Z][a-zA-Z.'-]*(?:\s+[A-Z][a-zA-Z.'-]*){0,2}\s+(?:"
    r"(?i:is|was|became|serves?\s+as|served\s+as)\s+(?:the\s+)?"
    r"(?i:ceo|cto|cfo|coo|chief(?:\s+\w+){0,3}|founder|president|vice\s+president|vp|"
    r"director|manager|head\s+of\s+\w+|professor|researcher|spokesperson)"
    r"|(?i:joined|founded|leads?|led|works?\s+at|worked\s+at)\s+"
    r"[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}"
    r")\b"
)
_ENGLISH_APPOSITIVE_PERSON_RE = re.compile(
    r"\b[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,2}\s*[,—–-]\s*"
    r"(?:(?:an?\s+|the\s+)?(?:[A-Z][A-Za-z0-9&.'-]*\s+)?)"
    r"(?i:ceo|cto|cfo|coo|chief(?:\s+\w+){0,3}|founder|president|vice\s+president|vp|"
    r"director|manager|head\s+of\s+\w+|professor|researcher|spokesperson)\b"
)
_ENGLISH_PARENTHETICAL_PERSON_RE = re.compile(
    r"\b[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,2}\s*[（(]\s*"
    r"(?:(?:an?\s+|the\s+)?(?:[A-Z][A-Za-z0-9&.'-]*\s+)?)"
    r"(?i:ceo|cto|cfo|coo|chief(?:\s+\w+){0,3}|founder|president|vice\s+president|vp|"
    r"director|manager|head\s+of\s+\w+|professor|researcher|spokesperson)"
    r"(?:\s+(?i:of|at)\s+[A-Z][A-Za-z0-9&.'-]*)?\s*[）)]"
)
_CHINESE_PERSON_FACT_RE = re.compile(
    r"[\u3400-\u9fff]{2,4}(?:是|担任)(?:CEO|CTO|CFO|COO|首席[^，。；]{1,12}|"
    r"创始人|总裁|负责人|总监|经理|教授|研究员|发言人)"
    r"|[\u3400-\u9fff]{2,4}(?:加入了?|创立了?|领导)(?:[A-Z][A-Za-z0-9&.'-]*|"
    r"[\u3400-\u9fff]{2,16})"
)
_CHINESE_APPOSITIVE_PERSON_RE = re.compile(
    r"[\u3400-\u9fff]{2,4}\s*[，,、—–-]\s*"
    r"(?:(?:[A-Z][A-Za-z0-9&.'-]*|[\u3400-\u9fff]{2,12})\s*)?"
    r"(?:CEO|CTO|CFO|COO|首席[^，。；]{1,12}|创始人|总裁|负责人|总监|经理|教授|研究员|发言人)"
)
_ENGLISH_EVIDENCE_RE = re.compile(
    r"\b(?:research|data|evidence|results?|stud(?:y|ies)|surveys?|case\s+stud(?:y|ies)|analysis)"
    r"\s+(?:has\s+|have\s+)?(?:shown|showed|shows?|demonstrated|demonstrates?|proved|proves?|"
    r"found|finds|confirmed|confirms)\b",
    re.IGNORECASE,
)
_CHINESE_EVIDENCE_RE = re.compile(r"(?:结果|数据|证据|研究|调查|案例|分析)(?:显示|表明|证明|发现|确认)")
_ENGLISH_CUSTOMER_CASE_RE = re.compile(
    r"\b(?:customers?|clients?|enterprises?|companies|brands?|organizations?|teams?|users?)\b"
    r".{0,80}\b(?:adopted|deployed|implemented|launched|migrated|selected|chose|used|reported|"
    r"achieved|reduced|increased|improved|grew|saved|praised|preferred|loved|trusted)\b",
    re.IGNORECASE,
)
_ENGLISH_GENERIC_CUSTOMER_USAGE_RE = re.compile(
    r"\b(?:customers|clients|enterprises|companies|brands|organizations|teams|users)\s+"
    r"(?:(?:already|currently|now|regularly|typically|widely)\s+)?"
    r"(?:use|used|rely|relies|relied)\b",
    re.IGNORECASE,
)
_ENGLISH_NAMED_TOKEN_PATTERN = r"(?:[A-Z][A-Za-z0-9&.'-]*|[a-z]+[A-Z][A-Za-z0-9&.'-]*)"
_ENGLISH_NAMED_SUBJECT_PATTERN = (
    rf"{_ENGLISH_NAMED_TOKEN_PATTERN}(?:\s+{_ENGLISH_NAMED_TOKEN_PATTERN}){{0,3}}"
)
_ENGLISH_PRODUCT_OBJECT_PATTERN = (
    r"(?:(?i:the|our|this|that|its|their)\s+"
    r"(?i:product|platform|system|tool|service|solution|software|app|application|workflow)"
    rf"|(?:(?i:the|our|this|that)\s+)?{_ENGLISH_NAMED_TOKEN_PATTERN}"
    r"(?:\s+(?i:platform|product|system|tool|service|solution|software|app|workflow))?)"
)
_ENGLISH_NAMED_CASE_RE = re.compile(
    rf"\b{_ENGLISH_NAMED_SUBJECT_PATTERN}\s+"
    r"(?:(?i:has|had)\s+)?(?i:adopted|deployed|implemented|launched|migrated|selected|chose)\b"
)
_ENGLISH_NAMED_USAGE_RELATION_RE = re.compile(
    rf"\b{_ENGLISH_NAMED_SUBJECT_PATTERN}\s+"
    r"(?:(?i:already|currently|now|regularly|typically|widely|actively|still)\s+)?"
    r"(?i:uses|used|relies|relied|prefers|preferred|trusts|trusted)\s+"
    rf"(?:(?i:on)\s+)?{_ENGLISH_PRODUCT_OBJECT_PATTERN}\b"
)
_ENGLISH_NAMED_CUSTOMER_RELATION_RE = re.compile(
    r"\b(?:"
    rf"(?!(?:This|That|These|Those|It|There)\b){_ENGLISH_NAMED_SUBJECT_PATTERN}\s+"
    r"(?i:is|was|became|remains)\s+(?:(?i:an?|the|our|one\s+of)\s+)?"
    rf"(?:{_ENGLISH_NAMED_TOKEN_PATTERN}(?:'s)?\s+)?"
    r"(?i:customer|client|enterprise\s+customer|user)"
    r"(?!\s+(?i:journey|experience|story|segment|outcome|success|service|support|research|"
    r"insight|feedback|path|pathway|lifecycle|value))"
    rf"(?:\s+(?i:of|for)\s+{_ENGLISH_NAMED_TOKEN_PATTERN})?"
    rf"|(?!(?:This|That|These|Those|It|There)\b){_ENGLISH_NAMED_SUBJECT_PATTERN}\s*[,]\s*"
    rf"(?:(?i:an?|the)\s+)?(?:{_ENGLISH_NAMED_TOKEN_PATTERN}\s+)?"
    r"(?i:customer|client|user)\b"
    rf"|{_ENGLISH_NAMED_TOKEN_PATTERN}\s+(?i:customer|client)\s+{_ENGLISH_NAMED_TOKEN_PATTERN}"
    r")"
)
_CHINESE_CUSTOMER_CASE_RE = re.compile(
    r"(?:某|一家|一位|该|这家)(?:客户|企业|品牌|公司|组织|团队|用户).{0,50}"
    r"(?:采用|部署|上线|迁移|选择|使用|反馈|实现|提升|降低|减少|增长|节省|称赞|认可)"
    r"|(?:客户|企业|品牌|公司|组织|团队|用户|用户案例).{0,40}(?:已|已经|曾|成功).{0,16}"
    r"(?:采用|部署|上线|迁移|选择|使用|反馈|实现|提升|降低|减少|增长|节省)"
    r"|(?:客户|企业|品牌|公司|组织|团队|用户|用户案例).{0,50}"
    r"(?:采用了|部署了|上线了|迁移了|选择了|使用了|反馈称|反馈表示|实现了|提升了|"
    r"降低了|减少了|增长了|节省了|称赞|认可)"
)
_CHINESE_CUSTOMER_USAGE_RE = re.compile(
    r"(?:客户|企业|品牌|公司|组织|团队|用户)\s*(?:正在|目前|普遍|通常|已经|已)?\s*"
    r"(?:使用|依赖|采用)\s*(?:[A-Z][A-Za-z0-9_.-]*|该|这款|本|"
    r"[\u3400-\u9fff]{2,12}(?:平台|产品|工具|系统|方案))"
)
_CHINESE_NAMED_USAGE_RE = re.compile(
    r"(?:[A-Z][A-Za-z0-9&.'-]*|[\u3400-\u9fff]{2,12})\s*"
    r"(?:目前|当前|已经|已|正在|持续)?\s*"
    r"(?:采用|部署|上线|迁移|选择|引入|购买|使用|依赖)了?\s*"
    r"(?:[A-Za-z][A-Za-z0-9_.-]*|(?:该|本|这款|其|我们的)?"
    r"(?:平台|产品|系统|工具|服务|方案|软件|应用|工作流)|"
    r"[\u3400-\u9fff]{2,12}(?:平台|产品|系统|工具|服务|方案|软件|应用))"
)
_CHINESE_NAMED_CUSTOMER_RELATION_RE = re.compile(
    r"(?:[A-Z][A-Za-z0-9&.'-]*|[\u3400-\u9fff]{2,12})\s*是\s*"
    r"(?:(?:[A-Z][A-Za-z0-9&.'-]*|[\u3400-\u9fff]{2,12})\s*的\s*)?"
    r"(?:客户|用户|合作企业)"
)
_ENGLISH_PERFECT_OUTCOME_RE = re.compile(
    r"\b(?:has|have|had)\s+(?:successfully\s+)?(?:achieved|reduced|increased|improved|grown|saved|"
    r"delivered|raised|lowered|cut|doubled|tripled|accelerated|eliminated|boosted)\b",
    re.IGNORECASE,
)
_ENGLISH_NAMED_OUTCOME_RE = re.compile(
    r"\b(?:"
    r"[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}"
    r"|(?i:we|our\s+(?:team|company|product|platform|program|initiative|workflow)|"
    r"the\s+(?:team|company|product|platform|program|initiative|workflow))"
    r")\s+(?:(?i:has|have|had)\s+)?(?:(?i:successfully)\s+)?"
    r"(?i:achieved|reduced|increased|improved|grew|saved|delivered|raised|lowered|cut|"
    r"doubled|tripled|accelerated|eliminated|boosted)\b"
)
_ENGLISH_PRESENT_OUTCOME_RE = re.compile(
    r"\b(?:"
    r"(?!(?:Clarity|Design|Structure|Simplicity|Focus|Communication|Storytelling|Workflow|"
    r"Process|Approach|Strategy|Presentation|Deck)\b)"
    r"[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}"
    r"|(?i:our|the)\s+(?i:product|platform|system|workflow|program|solution|tool)"
    r")\s+(?i:achieves|reduces|increases|improves|grows|saves|delivers|raises|lowers|cuts|"
    r"doubles|triples|accelerates|eliminates|boosts)\b"
)
_ENGLISH_COMPARATIVE_OUTCOME_RE = re.compile(
    r"\b(?:"
    r"(?!(?:Clarity|Design|Structure|Simplicity|Focus|Communication|Storytelling|Workflow|"
    r"Process|Approach|Strategy|Presentation|Deck)\b)"
    r"[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}"
    r"|(?i:our|the)\s+(?i:product|platform|system|workflow|program|solution|tool)"
    r")\s+(?i:makes?|made|drives?|drove|renders?|rendered|keeps?|kept|leaves?|left)\s+(?:"
    r".{1,80}\b(?i:faster|slower|easier|harder|better|worse|safer|cheaper|simpler|clearer|"
    r"higher|lower|more\s+efficient|less\s+expensive)\b"
    r"|(?i:faster|slower|easier|harder|better|worse|safer|cheaper|simpler|clearer|"
    r"higher|lower|more\s+efficient|less\s+expensive)\b\s+.{1,80})"
)
_ENGLISH_WITH_COMPARATIVE_OUTCOME_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}\s+"
    r"(?i:is|was|became)\s+(?:(?i:now|already)\s+)?"
    r"(?i:faster|slower|easier|harder|better|worse|safer|cheaper|simpler|clearer|higher|lower)"
    r"\s+(?i:with|using|after)\s+[A-Z][A-Za-z0-9&.'-]*\b"
)
_ENGLISH_METRIC_OUTCOME_RE = re.compile(
    r"\b(?:revenue|retention|conversion|adoption|engagement|satisfaction|productivity|efficiency|"
    r"latency|costs?|time|risk|churn|sales|profit|growth)\s+(?:(?:has|have|had)\s+)?"
    r"(?:grew|rose|fell|improved|increased|decreased|declined|dropped|doubled|tripled)\b",
    re.IGNORECASE,
)
_ENGLISH_PASSIVE_OUTCOME_RE = re.compile(
    r"\b(?:costs?|time|risk|churn|latency|revenue|retention|conversion|adoption|engagement|"
    r"satisfaction|productivity|efficiency|sales|profit|growth)\s+"
    r"(?:(?:was|were)|(?:has|have|had)\s+been)\s+(?:successfully\s+)?"
    r"(?:reduced|increased|improved|raised|lowered|cut|accelerated|eliminated|boosted)\s+"
    r"by\s+[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}\b",
    re.IGNORECASE,
)
_CHINESE_REALIZED_OUTCOME_RE = re.compile(
    r"(?:已|已经|曾|成功|目前已).{0,16}(?:实现|提升|降低|减少|增长|节省|完成|交付|达到|获得|改善)"
    r"|(?:[\u3400-\u9fffA-Za-z0-9·]{2,24}).{0,12}"
    r"(?:实现了|提升了|降低了|减少了|增长了|节省了|完成了|交付了|达到了|获得了|改善了)"
)
_CHINESE_COMPARATIVE_OUTCOME_RE = re.compile(
    r"(?:[A-Z][A-Za-z0-9&.'-]*|[\u3400-\u9fff]{2,16}|该(?:平台|产品|系统|工具|方案))"
    r"\s*(?:让|使).{1,50}(?:更快|更慢|更高|更低|更好|更差|更容易|更困难|更简单|更清晰|"
    r"更安全|更便宜)"
)
_MODAL_OR_ADVISORY_RE = re.compile(
    r"\b(?:can|could|may|might|should|would|will|aims?\s+to|designed\s+to|"
    r"helps?\s+to|enables?\s+\w+\s+to|recommend(?:s|ed)?\s+to|needs?\s+to)\b"
    r"|(?:可以|能够|应当|应该|将会|建议|旨在|目标是|有助于|帮助|需要)",
    re.IGNORECASE,
)
_NONASSERTIVE_CLAUSE_PREFIX_RE = re.compile(
    r"^\s*(?:what\s+if|suppose|supposing|imagine|assuming?|consider\s+whether)\b"
    r"|^\s*(?:假设|假如|如果|若|设想|想象|是否|会不会)",
    re.IGNORECASE,
)
_QUOTED_SPAN_RE = re.compile(
    r"“([^”\r\n]+)”|‘([^’\r\n]+)’|\"([^\"\r\n]+)\"|"
    r"「([^」\r\n]+)」|『([^』\r\n]+)』"
)
_METALINGUISTIC_PREFIX_RE = re.compile(
    r"(?:\b(?:the\s+)?(?:label|title|heading|caption|term|phrase|word|tag|placeholder|example)"
    r"(?:\s+(?:is|was|reads?))?\s*$"
    r"|(?:^|[.!?;。！？；]\s*)(?:please\s+)?(?:use|write|display|render|set|call|name|label|rename)"
    r"(?:\s+(?:this|the)\s+(?:section|slide|page|label|title|heading|caption|term|phrase))?\s*$"
    r"|(?:术语|短语|词语|标题|标签|占位文案)(?:是|为|写作|：|:)?\s*$"
    r"|(?:^|[。！？；]\s*)(?:请)?(?:使用|写下|显示|渲染|设置|命名为|标题为|标签为|称为)\s*$)",
    re.IGNORECASE,
)
_METALINGUISTIC_SUFFIX_RE = re.compile(
    r"^\s*(?:(?:as|for)\s+(?:an?\s+|the\s+)?(?:draft\s+)?"
    r"(?:label|heading|title|caption|term|phrase|tag|placeholder|example)\b"
    r"|(?:is|was)\s+(?:only\s+)?(?:an?\s+|the\s+)?(?:draft\s+)?"
    r"(?:label|heading|title|caption|term|phrase|tag|placeholder|example)\b"
    r"|(?:作为|用作|仅作为)(?:页面|本页|幻灯片|章节)?"
    r"(?:标签|标题|小标题|说明|术语|短语|占位文案|示例))",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR_RE = re.compile(
    r"[\r\n]+|[.!?。！？；;，,]+|"
    r"\b(?:and|but|while|whereas|however|yet)\b|"
    r"(?:但是|然而|并且|同时|然后|但|而|且)",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(
    r"^(?:(?:source|quote|quotation|interview(?:\s+(?:transcript|excerpt))?|transcript|statement|fact|evidence)"
    r"|(?:来源|引述|访谈(?:原文)?|原文|陈述|事实|证据))\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
_SOURCE_QUOTE_LABEL_PREFIX_RE = re.compile(
    r"(?:(?:source|quote|quotation|interview(?:\s+(?:transcript|excerpt))?|transcript|statement|fact|evidence)"
    r"|(?:来源|引述|访谈(?:原文)?|原文|陈述|事实|证据))\s*[:：]\s*$",
    re.IGNORECASE,
)
_SOURCE_QUOTE_LABEL_LINE_RE = re.compile(
    r"^\s*(?:(?:source|quote|quotation|interview(?:\s+(?:transcript|excerpt))?|transcript|statement|fact|evidence)"
    r"|(?:来源|引述|访谈(?:原文)?|原文|陈述|事实|证据))\s*[:：]",
    re.IGNORECASE,
)
_SOURCE_NEGATION_PREFIX_RE = re.compile(
    r"(?:\b(?:not|never|false|den(?:y|ies|ied)|disput(?:e|es|ed)|reject(?:s|ed)?|"
    r"unsupported|unverified|no\s+evidence|cannot\s+confirm|did\s+not)\b|"
    r"(?:并非|不是|不实|虚假|否认|驳斥|没有证据|无法确认|未曾|从未))",
    re.IGNORECASE,
)
_SOURCE_NEGATION_SUFFIX_RE = re.compile(
    r"^\s*(?:(?:is|was|remains)\s+(?:false|untrue|unsupported|unverified|incorrect)|"
    r"(?:was|is)\s+(?:denied|disputed|rejected|disproven|refuted|debunked)|"
    r"(?:并不属实|不属实|是错误的|是虚假的|被否认|被驳斥))",
    re.IGNORECASE,
)
_SOURCE_NONASSERTIVE_PREFIX_RE = re.compile(
    r"(?:^|[.!?;:。！？；：]\s*)(?:suppose|imagine|assume|assuming|what\s+if|consider(?:\s+whether)?|"
    r"hypothetical(?:\s+(?:example|scenario))?|for\s+(?:example|illustration)|example|scenario|"
    r"sample|illustration|perhaps)\b.*$"
    r"|(?:^|[.!?;:。！？；：]\s*)e\s*\.\s*g\s*\.?\s*[,，:：-]?\s*.*$"
    r"|\b(?:an?\s+)?(?:example|scenario|sample|illustration)\s*[:：—–-]\s*$"
    r"|\b(?:the\s+)?(?:open\s+)?question\s+(?:is|was|remains)\s*$"
    r"|(?:^|[。！？；：]\s*)(?:假设|假如|如果|若|设想|想象|假定|试想|例如|比如|举例|"
    r"场景|情景|考虑是否|示例|假想示例|假设示例|开放问题是|问题是|是否).*$",
    re.IGNORECASE,
)
_SOURCE_NONASSERTIVE_SUFFIX_RE = re.compile(
    r"^\s*(?:(?:is|was|remains)\s+(?:an?\s+)?(?:open\s+)?question\b|"
    r"(?:is|was)\s+(?:hypothetical|unknown|uncertain)\b|"
    r"(?:仍是|还是|只是)(?:一个)?(?:开放问题|假设|假想|未知情况)|尚不确定|有待确认|是否属实)",
    re.IGNORECASE,
)


def qualitative_claims(value: str, *, force_quote: bool = False) -> list[QualitativeClaim]:
    text = str(value or "").strip()
    if not text:
        return []
    if force_quote:
        return [("quote", text)]

    claims = [("quote", quoted) for quoted in _substantive_quoted_spans(text)]
    analysis_text = _without_metalinguistic_quotes(text)
    for fragment in claim_fragments(analysis_text):
        for kind in qualitative_claim_kinds(fragment):
            claims.append((kind, fragment))
    return claims


def _substantive_quoted_spans(value: str) -> list[str]:
    text = str(value or "")
    result: list[str] = []
    for match in _QUOTED_SPAN_RE.finditer(text):
        quoted = next((item.strip() for item in match.groups() if item is not None), "")
        latin_words = re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", quoted)
        cjk_chars = re.findall(r"[\u3400-\u9fff]", quoted)
        if (
            (len(latin_words) >= 3 or len(cjk_chars) >= 6)
            and not _quote_is_metalinguistic(text, match.start(), match.end())
        ):
            result.append(quoted)
    return result


def _quote_is_metalinguistic(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 120) : start]
    suffix = text[end : end + 120]
    return bool(
        _METALINGUISTIC_PREFIX_RE.search(prefix)
        or _METALINGUISTIC_SUFFIX_RE.search(suffix)
    )


def _without_metalinguistic_quotes(text: str) -> str:
    result: list[str] = []
    offset = 0
    for match in _QUOTED_SPAN_RE.finditer(text):
        result.append(text[offset : match.start()])
        if not _quote_is_metalinguistic(text, match.start(), match.end()):
            result.append(match.group(0))
        offset = match.end()
    result.append(text[offset:])
    return "".join(result)


def qualitative_claim_kinds(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []

    kinds: list[str] = []
    if (
        _ENGLISH_PERSON_FACT_RE.search(text)
        or _ENGLISH_APPOSITIVE_PERSON_RE.search(text)
        or _ENGLISH_PARENTHETICAL_PERSON_RE.search(text)
        or _CHINESE_PERSON_FACT_RE.search(text)
        or _CHINESE_APPOSITIVE_PERSON_RE.search(text)
    ):
        kinds.append("person_fact")
    if (
        _ENGLISH_ATTRIBUTION_RE.search(text)
        or _CHINESE_ATTRIBUTION_RE.search(text)
        or _ENGLISH_EVIDENCE_RE.search(text)
        or _CHINESE_EVIDENCE_RE.search(text)
    ):
        kinds.append("attribution")

    # Advisory language exempts only its own clause.  A modal prefix must not
    # launder a later customer or realized-outcome assertion.
    scopes = (
        [item.strip() for item in _CLAUSE_SEPARATOR_RE.split(text) if item.strip()]
        if _MODAL_OR_ADVISORY_RE.search(text)
        else [text]
    )
    for scope in scopes:
        if _MODAL_OR_ADVISORY_RE.search(scope):
            continue
        if (
            _ENGLISH_CUSTOMER_CASE_RE.search(scope)
            or _ENGLISH_GENERIC_CUSTOMER_USAGE_RE.search(scope)
            or _ENGLISH_NAMED_CASE_RE.search(scope)
            or _ENGLISH_NAMED_USAGE_RELATION_RE.search(scope)
            or _ENGLISH_NAMED_CUSTOMER_RELATION_RE.search(scope)
            or _CHINESE_CUSTOMER_CASE_RE.search(scope)
            or _CHINESE_CUSTOMER_USAGE_RE.search(scope)
            or _CHINESE_NAMED_USAGE_RE.search(scope)
            or _CHINESE_NAMED_CUSTOMER_RELATION_RE.search(scope)
        ):
            if "customer_case" not in kinds:
                kinds.append("customer_case")
        if (
            _ENGLISH_PERFECT_OUTCOME_RE.search(scope)
            or _ENGLISH_NAMED_OUTCOME_RE.search(scope)
            or _ENGLISH_PRESENT_OUTCOME_RE.search(scope)
            or _ENGLISH_COMPARATIVE_OUTCOME_RE.search(scope)
            or _ENGLISH_WITH_COMPARATIVE_OUTCOME_RE.search(scope)
            or _ENGLISH_METRIC_OUTCOME_RE.search(scope)
            or _ENGLISH_PASSIVE_OUTCOME_RE.search(scope)
            or _CHINESE_REALIZED_OUTCOME_RE.search(scope)
            or _CHINESE_COMPARATIVE_OUTCOME_RE.search(scope)
        ):
            if "realized_outcome" not in kinds:
                kinds.append("realized_outcome")
    return kinds


def claim_fragments(value: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"[^.!?。！？\r\n]+(?:[.!?。！？]+|$)", str(value or "")):
        sentence = match.group(0).strip()
        if not sentence or re.search(r"[?？]+\s*$", sentence):
            continue
        fragment = sentence.rstrip(".!。！").strip(" \t\r\n\"'“”‘’")
        if not fragment or _NONASSERTIVE_CLAUSE_PREFIX_RE.search(fragment):
            continue
        result.append(fragment)
    return result


def canonical_claim_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\"'“”‘’.,!?。！？；;:：、—–-]+", "", normalized)


def deduplicate_qualitative_claims(claims: list[QualitativeClaim]) -> list[QualitativeClaim]:
    result: list[QualitativeClaim] = []
    seen: set[tuple[str, str]] = set()
    for kind, value in claims:
        key = (kind, canonical_claim_text(value))
        if key not in seen:
            seen.add(key)
            result.append((kind, value))
    return result


def unsupported_qualitative_claims(
    claims: list[QualitativeClaim],
    source_values: list[str],
) -> list[QualitativeClaim]:
    canonical_sources = {
        canonical_claim_text(unit)
        for value in source_values
        for unit in _source_support_units(value)
        if canonical_claim_text(unit)
    }
    return [
        (kind, value)
        for kind, value in deduplicate_qualitative_claims(claims)
        if canonical_claim_text(value) not in canonical_sources
    ]


def _source_support_units(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []

    units: list[str] = []

    def add_unit(candidate: str) -> None:
        item = str(candidate or "").strip()
        if item and not _source_unit_is_nonassertive(item):
            units.append(item)

    add_unit(text)
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    for line in lines:
        add_unit(line)
        unquoted_line = _QUOTED_SPAN_RE.sub(" ", line)
        for sentence in re.findall(r"[^.!?。！？]+(?:[.!?。！？]+|$)", unquoted_line):
            add_unit(sentence)
        label_match = _SOURCE_LABEL_RE.fullmatch(line)
        if label_match:
            add_unit(label_match.group(1))
    for match in _QUOTED_SPAN_RE.finditer(text):
        if _quote_is_metalinguistic(text, match.start(), match.end()):
            continue
        if _source_quote_is_negated(text, match.start(), match.end()):
            continue
        if _source_quote_is_nonassertive(text, match.start(), match.end()):
            continue
        if not _source_quote_is_authoritative(text, match.start(), match.end()):
            continue
        for item in match.groups():
            if item:
                add_unit(item)
    return list(dict.fromkeys(units))


def _source_quote_is_negated(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 100) : start]
    suffix = text[end : end + 100]
    return bool(
        _SOURCE_NEGATION_PREFIX_RE.search(prefix)
        or _SOURCE_NEGATION_SUFFIX_RE.search(suffix)
    )


def _source_quote_is_nonassertive(text: str, start: int, end: int) -> bool:
    quoted = text[start:end]
    prefix = text[max(0, start - 140) : start]
    suffix = text[end : end + 140]
    return bool(
        re.search(r"[?？][\"'”’」』]*\s*$", quoted)
        or _SOURCE_NONASSERTIVE_PREFIX_RE.search(prefix)
        or _SOURCE_NONASSERTIVE_SUFFIX_RE.search(suffix)
    )


def _source_quote_is_authoritative(text: str, start: int, end: int) -> bool:
    before = text[:start]
    after = text[end:]
    if not canonical_claim_text(before) and not canonical_claim_text(after):
        return True

    line_prefix = before.rsplit("\n", 1)[-1]
    if _SOURCE_QUOTE_LABEL_LINE_RE.search(line_prefix):
        return True
    if _SOURCE_QUOTE_LABEL_PREFIX_RE.search(line_prefix):
        return True

    attribution_prefix = before[-180:]
    return bool(
        re.search(
            r"\b(?:said|stated|reported|confirmed|says|states|reports|confirms)"
            r"(?:\s+that)?\s*[,，:：]?\s*$",
            attribution_prefix,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(?:表示|称|指出|确认|说道|说)\s*[,，:：]?\s*$",
            attribution_prefix,
        )
    )


def _source_unit_is_nonassertive(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        re.search(r"[?？][\"'”’」』]*\s*$", text)
        or _SOURCE_NONASSERTIVE_PREFIX_RE.search(text)
        or _SOURCE_NONASSERTIVE_SUFFIX_RE.search(text)
    )


__all__ = [
    "QualitativeClaim",
    "canonical_claim_text",
    "deduplicate_qualitative_claims",
    "qualitative_claims",
    "unsupported_qualitative_claims",
]
