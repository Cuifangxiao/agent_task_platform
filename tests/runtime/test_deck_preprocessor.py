import inspect
import json

import pytest
from pydantic import ValidationError

import core.deck.agents.preprocessor.graph as preprocessor_graph
from core.deck.agents.preprocessor import (
    GlobalMeta,
    GlobalMetaInterpretation,
    LogicalOutline,
    PagePlan,
    PagePlanItem,
    Preprocessor,
)
from core.deck.agents.preprocessor.templates import SceneTemplateRepository
from core.deck.agents.processor.layout import prepare_processor_input


class _BoundSchemaLLM:
    def __init__(self, parent, model_type):
        self.parent = parent
        self.model_type = model_type

    def invoke(self, messages):
        payload = json.loads(messages[-1][1])
        self.parent.calls.append((self.model_type, payload))
        if self.model_type is GlobalMetaInterpretation:
            return {
                "title": "Transformer 入门",
                "subtitle": "",
                "presenter": "",
                "audience": "刚接触深度学习的学生",
                "scene": "thesis_defense",
                "goal": "解释 Transformer 的核心机制",
                "audience_outcome": "建立对注意力机制的基础认知",
                "communication_job": "学生应理解 Transformer 的核心机制并形成继续学习的框架。",
                "language": "zh-CN",
                "target_page_count": 3,
                "aspect_ratio": "16:9",
                "tone": "清晰、教学导向",
                "content_constraints": ["解释注意力机制", "说明整体结构"],
                "brand_preferences": [],
                "color_preferences": [],
                "style_preferences": [],
                "source_mode": "general_knowledge",
            }
        if self.model_type is LogicalOutline:
            return {
                "title": "Transformer 入门",
                "central_thesis": "理解注意力机制是理解整体结构的基础",
                "narrative_strategy": "从学习问题进入核心机制，再回到整体结构",
                "sections": [
                    {
                        "section_id": "sec01",
                        "title": "核心机制",
                        "purpose": "建立概念基础",
                        "topics": [
                            {
                                "topic_id": "t01",
                                "title": "注意力机制",
                                "intent": "解释信息关联方式",
                                "key_points": ["关联序列中的上下文信息"],
                            },
                            {
                                "topic_id": "t02",
                                "title": "整体结构",
                                "intent": "说明核心模块如何协作",
                                "key_points": ["从局部机制回到整体认知"],
                            },
                        ],
                    }
                ],
            }
        if self.model_type is PagePlan:
            target = payload["hard_constraints"]["target_page_count"]
            pages = [
                {"page_no": 1, "page_category": "cover", "topic": "Transformer 入门"},
                {"page_no": 2, "page_category": "catalog", "topic": "内容结构"},
            ]
            for page_no in range(3, target - 1):
                pages.append(
                    {
                        "page_no": page_no,
                        "page_category": "content",
                        "topic": "注意力机制" if page_no % 2 else "整体结构",
                    }
                )
            pages.extend(
                [
                    {
                        "page_no": target - 1,
                        "page_category": "summary",
                        "topic": "核心认识",
                    },
                    {
                        "page_no": target,
                        "page_category": "thanks",
                        "topic": "总结与交流",
                    },
                ]
            )
            return {"scene": "thesis_defense", "pages": pages}
        raise AssertionError(f"unexpected schema: {self.model_type}")


class _SchemaAwareLLM:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, model_type, **_kwargs):
        return _BoundSchemaLLM(self, model_type)


def _initial_state(request_id: str = "preprocessor-test"):
    return {
        "task_id": request_id,
        "request_id": request_id,
        "user_description": "请生成三页 Transformer 论文答辩 PPT，面向初学者。",
        "uploaded_file_text": "",
        "expected_slide_count": 0,
        "language": "",
        "aspect_ratio": "16:9",
    }


def test_page_plan_schema_forbids_layout_and_content_fields():
    valid = {"page_no": 1, "page_category": "content", "topic": "核心机制"}
    assert PagePlanItem.model_validate(valid).topic == "核心机制"

    for forbidden in ("layout_id", "layout_template", "layout_family", "content", "html"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            PagePlanItem.model_validate({**valid, forbidden: "not-allowed"})


def test_global_meta_and_logical_outline_keep_runtime_and_page_fields_out():
    meta_payload = {
        "title": "Topic",
        "subtitle": "",
        "presenter": "",
        "audience": "Audience",
        "scene": "general",
        "goal": "Explain the topic",
        "audience_outcome": "Understand the topic",
        "communication_job": "The audience should understand the topic.",
        "language": "en-US",
        "target_page_count": 4,
        "aspect_ratio": "16:9",
        "tone": "clear",
        "content_constraints": [],
        "brand_preferences": [],
        "color_preferences": [],
        "style_preferences": [],
        "source_mode": "general_knowledge",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        GlobalMetaInterpretation.model_validate({**meta_payload, "source_text": "runtime only"})

    outline_payload = {
        "title": "Topic",
        "central_thesis": "Explain the topic",
        "narrative_strategy": "Move from context to conclusion",
        "sections": [
            {
                "section_id": "sec01",
                "title": "Context",
                "purpose": "Establish the question",
                "topics": [
                    {
                        "topic_id": "t01",
                        "title": "Question",
                        "intent": "Define the question",
                        "key_points": [],
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        LogicalOutline.model_validate({**outline_payload, "pages": []})
    outline_payload["sections"][0]["topics"][0]["page_no"] = 1
    with pytest.raises(ValidationError, match="Extra inputs"):
        LogicalOutline.model_validate(outline_payload)


def test_scene_template_repository_matches_asset_a_contract():
    template = SceneTemplateRepository().resolve("论文答辩")

    assert template.model_dump(mode="json") == {
        "scene": "thesis_defense",
        "mandatory_page_types": ["cover", "catalog", "thanks"],
        "suggest_page_order": [
            "cover",
            "catalog",
            "content",
            "content",
            "chart",
            "summary",
            "thanks",
        ],
        "page_min": 10,
        "page_max": 16,
    }


def test_preprocessor_runs_three_separated_steps_and_persists_contracts(tmp_path):
    llm = _SchemaAwareLLM()
    preprocessor = Preprocessor({"brief": llm, "outline": llm}, tmp_path)

    result = preprocessor.run(_initial_state())

    meta = GlobalMeta.model_validate(result["global_meta"])
    outline = LogicalOutline.model_validate(result["logical_outline"])
    page_plan = PagePlan.model_validate(result["page_plan"])
    assert meta.title == "Transformer 入门"
    assert outline.title == meta.title
    assert len(page_plan.pages) == 10
    assert [page.page_category.value for page in page_plan.pages[:2]] == ["cover", "catalog"]
    assert page_plan.pages[-1].page_category.value == "thanks"

    call_types = [model_type for model_type, _payload in llm.calls]
    assert call_types == [GlobalMetaInterpretation, LogicalOutline, PagePlan]
    outline_payload = llm.calls[1][1]
    assert "scene_narrative_template" not in outline_payload
    assert "layout_recipe_manifest" not in outline_payload
    page_payload = llm.calls[2][1]
    assert page_payload["scene_narrative_template"]["scene"] == "thesis_defense"
    assert page_payload["hard_constraints"]["output_fields_per_page"] == [
        "page_no",
        "page_category",
        "topic",
    ]

    project = tmp_path / "preprocessor-test"
    assert (project / "global-meta.json").is_file()
    assert (project / "logical-outline.json").is_file()
    assert (project / "logical-outline.txt").is_file()
    assert (project / "scene-narrative-template.json").is_file()
    persisted_page_plan = json.loads((project / "page-plan.json").read_text(encoding="utf-8"))
    assert persisted_page_plan == page_plan.model_dump(mode="json")


def test_preprocessor_module_has_no_design_or_renderer_dependency():
    source = inspect.getsource(preprocessor_graph)

    assert "core.deck.design_system" not in source
    assert "LayoutFamily" not in source
    assert "HTMLAssembler" not in source
    assert "ImagePptxExporter" not in source


def test_processor_layout_planning_runs_after_and_does_not_mutate_page_plan(monkeypatch):
    llm = _SchemaAwareLLM()
    preprocessed = Preprocessor({"brief": llm, "outline": llm}).run(_initial_state("adapter"))
    original_page_plan = json.loads(json.dumps(preprocessed["page_plan"]))
    monkeypatch.setattr(
        "core.deck.agents.processor.layout.get_stream_writer",
        lambda: lambda _event: None,
    )

    adapted = prepare_processor_input(preprocessed)

    assert preprocessed["page_plan"] == original_page_plan
    assert all("layout_family" not in page for page in preprocessed["page_plan"]["pages"])
    assert all("layout_family" in slide for slide in adapted["deck_plan"]["slides"])


def test_requirement_parsing_still_requires_the_brief_model(monkeypatch):
    monkeypatch.setattr(preprocessor_graph, "get_stream_writer", lambda: lambda _event: None)

    with pytest.raises(ValueError, match="missing llm for deck node 'brief'"):
        preprocessor_graph.parse_requirements(_initial_state("missing"), {})
