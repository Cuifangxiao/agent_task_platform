import hashlib
import json
from collections import Counter
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from core.deck.domain.contracts import EventType, PipelineEvent, ResourceType
from core.deck.domain.schemas import (
    DeckOutlineSpec,
    HtmlSlideSpec,
    PROMPT_VERSION,
    RENDERER_VERSION,
    SCHEMA_VERSION,
    SlideDesignPlan,
)
from core.deck.pipeline.graph import PptGraph


GLOBAL_STYLE = {
    "primary_color": "#0B3A53",
    "secondary_color": "#146C94",
    "accent_color": "#B45309",
    "background_color": "#F7FAFC",
    "surface_color": "#FFFFFF",
    "text_primary": "#111827",
    "text_secondary": "#374151",
    "text_muted": "#4B5563",
    "border_color": "#CBD5E1",
    "success_color": "#15803D",
}

SLIDE_DEFINITIONS = (
    ("cover", "cover_minimal", "mixed"),
    ("context", "editorial_split", "paragraph"),
    ("evidence", "three_points", "list"),
    ("process", "process", "list"),
    ("decision", "comparison", "mixed"),
    ("process", "timeline", "list"),
    ("insight", "statement_focus", "paragraph"),
    ("closing", "closing_resolution", "paragraph"),
)


def _human_payload(messages) -> dict:
    return json.loads(messages[-1][1])


class OutlineLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def invoke(self, messages):
        self.calls += 1
        payload = _human_payload(messages)
        self.payloads.append(payload)
        assert payload["user_request"] == "生成8页演示，验证完整规划、统一设计、逐页修订和图片型导出。"
        assert payload["requested_page_count"] == 8
        assert payload["source_markdown"].startswith(payload["user_request"])
        assert payload["hard_constraints"]["minimum_page_count"] == 1
        assert payload["hard_constraints"]["maximum_page_count"] == 30
        assert payload["hard_constraints"]["global_style_is_the_only_palette_source"] is True
        assert payload["hard_constraints"]["external_resources"] == "forbidden"
        assert set(payload["available_assets"]) == {
            "source_files",
            "source_slices",
            "declared_asset_lines",
            "local_assets",
            "rules",
        }
        assert payload["available_assets"]["local_assets"] == []
        return DeckOutlineSpec.model_validate(
            {
                "title": "八页三阶段集成测试演示",
                "subtitle": "一次全局规划，逐页设计与 HTML 生成",
                "metadata": {
                    "author": "PPT Pipeline Test",
                    "date": "2026-08-03",
                    "language": "zh-CN",
                    "keywords": ["三阶段", "并行", "DOM QA", "PPTX"],
                    "design_style": "清晰、技术导向、留白充分",
                    "source_summary": "验证三模型链路、修复、回退、预览和图片型导出。",
                    "global_style_spec": GLOBAL_STYLE,
                },
                "slides": [
                    {
                        "id": f"s{index:02d}",
                        "title": f"第{index}页形成独立判断",
                        "type": role,
                        "key_points": [
                            f"第{index}页的批准结论",
                            f"第{index}页的验证依据",
                        ],
                        "layout_suggestion": layout,
                        "content_mode": content_mode,
                        "content_note": f"推进第{index}个叙事任务，并与相邻页面形成变化。",
                    }
                    for index, (role, layout, content_mode) in enumerate(
                        SLIDE_DEFINITIONS,
                        start=1,
                    )
                ],
            }
        ).model_dump(mode="json")


class StyleLLM:
    """Return complete per-page plans while making branch concurrency observable."""

    def __init__(self, concurrent_wave: int = 4) -> None:
        self.calls: list[str] = []
        self.completion_order: list[str] = []
        self.plans: dict[str, dict] = {}
        self.max_inflight = 0
        self._inflight = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(concurrent_wave, timeout=5)
        self._concurrent_wave = concurrent_wave

    def invoke(self, messages):
        payload = _human_payload(messages)
        slide = payload["current_slide"]
        slide_id = slide["id"]
        index = int(slide_id[1:])

        assert payload["deck"]["title"] == "八页三阶段集成测试演示"
        assert payload["global_style_spec"] == GLOBAL_STYLE
        assert [item["slide_id"] for item in payload["slide_summaries"]] == [
            f"s{page:02d}" for page in range(1, 9)
        ]
        assert payload["hard_constraints"] == {
            "slide_id": slide_id,
            "layout_type": slide["layout_suggestion"],
            "inherit_global_style_exactly": True,
            "do_not_return_hidden_chain_of_thought": True,
        }
        assert payload["locked_layout"]["slide_id"] == slide_id
        assert payload["locked_layout"]["layout_family"] == slide["layout_suggestion"]
        assert slide["layout_suggestion"] in payload["supported_layout_types"]
        assert payload["relevant_sources"]
        assert all(item["trusted_local_only"] for item in payload["relevant_assets"])

        with self._lock:
            call_position = len(self.calls)
            self.calls.append(slide_id)
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            if call_position < self._concurrent_wave:
                self._barrier.wait()
            # The higher-index page in the first wave completes first, proving
            # fan-in does not rely on branch completion order.
            time.sleep(0.015 * (9 - index))
            plan = SlideDesignPlan.model_validate(
                {
                    "slide_id": slide_id,
                    "design_rationale": {
                        "request_analysis": f"聚焦{slide['title']}，不扩写未批准事实。",
                        "content_structure": "标题先行，主体承载批准结论与依据。",
                        "layout_reasoning": (
                            f"沿用锁定的 {slide['layout_suggestion']} 布局并避开相邻页面重复。"
                        ),
                        "design_choices": "使用统一色板、批准字体和本地资产。",
                        "consistency_check": "颜色、slide_id 与布局均继承全局锁定值。",
                    },
                    "design_specifications": {
                        "layout_type": slide["layout_suggestion"],
                        "color_scheme": payload["global_style_spec"],
                        "typography": {
                            "font_family": ", ".join(payload["approved_fonts"]),
                            "title": "48px/1.15/700",
                            "heading": "30px/1.2/600",
                            "body": "24px/1.4/400",
                            "caption": "18px/1.35/400",
                        },
                        "key_elements": ["assertion title", "approved content", "page number"],
                        "visual_hierarchy": ["title", "takeaway", "supporting evidence"],
                    },
                }
            ).model_dump(mode="json")
            with self._lock:
                self.plans[slide_id] = plan
                self.completion_order.append(slide_id)
            return plan
        finally:
            with self._lock:
                self._inflight -= 1


class DesignerLLM:
    def __init__(self, style: StyleLLM) -> None:
        self.style = style
        self.initial_calls: list[str] = []
        self.repair_calls: list[tuple[str, int]] = []
        self.repair_icon_shortlists: list[list[str]] = []
        self.repair_diagnostics: list[list[dict]] = []
        self.received_design_plans: dict[str, dict] = {}
        self.received_global_styles: dict[str, dict] = {}
        self.layout_preferences: dict[str, dict] = {}
        self.previous_compositions: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def invoke(self, messages):
        payload = _human_payload(messages)
        if "repair_attempt" in payload:
            slide = payload["slide_plan"]
            slide_id = slide["slide_id"]
            attempt = int(payload["repair_attempt"])
            self._assert_design_context(payload, slide_id)
            assert payload["slide_outline"]["id"] == slide_id
            assert payload["current_html_spec"]["slide_id"] == slide_id
            assert payload["diagnostics"]
            with self._lock:
                self.repair_calls.append((slide_id, attempt))
                self.repair_icon_shortlists.append(payload["available_icon_keys"])
                self.repair_diagnostics.append(payload["diagnostics"])
            marker = "repair-success" if slide_id == "s03" else "always-invalid"
            return _html_spec(
                slide_id=slide_id,
                index=int(slide["index"]),
                title=slide["assertion_title"],
                takeaway=slide["takeaway"],
                layout_family=slide["layout_family"],
                layout_variant=payload["current_html_spec"]["layout_variant"],
                marker=marker,
            )

        outline_slide = payload["current_slide"]
        content = payload["slide_content"]
        preference = payload["layout_preference"]
        slide_id = outline_slide["id"]
        self._assert_design_context(payload, slide_id)
        assert content["slide_id"] == slide_id
        assert preference["slide_id"] == slide_id
        assert payload["html_contract"]["canvas"] == "1600x900"
        assert payload["html_contract"]["javascript"] == "forbidden"
        assert payload["html_contract"]["external_resources"] == "forbidden"
        with self._lock:
            self.initial_calls.append(slide_id)
            self.layout_preferences[slide_id] = preference
            self.previous_compositions[slide_id] = payload["previous_compositions"]

        marker = {
            "s03": "repair-once",
            "s05": "always-invalid",
        }.get(slide_id, "model-valid")
        return _html_spec(
            slide_id=slide_id,
            index=int(preference["index"]),
            title=content["assertion_title"],
            takeaway=content["takeaway"],
            layout_family=payload["slide_design_plan"]["design_specifications"]["layout_type"],
            layout_variant=preference["layout_variant"],
            marker=marker,
        )

    def _assert_design_context(self, payload: dict, slide_id: str) -> None:
        design_plan = SlideDesignPlan.model_validate(
            payload["slide_design_plan"]
        ).model_dump(mode="json")
        assert design_plan["slide_id"] == slide_id
        assert design_plan == self.style.plans[slide_id]
        assert payload["global_style_spec"] == GLOBAL_STYLE
        assert design_plan["design_specifications"]["color_scheme"] == GLOBAL_STYLE
        with self._lock:
            self.received_design_plans[slide_id] = design_plan
            self.received_global_styles[slide_id] = payload["global_style_spec"]


class DeterministicBrowserRenderer:
    """Exercise graph routing while leaving browser geometry to its own tests."""

    def __init__(self) -> None:
        self.measure_markers: list[str] = []
        self.rendered_pages: list[int] = []
        self.captured_pages: list[int] = []
        self._lock = threading.Lock()

    def measure_and_capture(
        self,
        document: str,
        png_path: Path,
        *,
        font_families,
    ):
        del font_families
        marker = next(
            (
                candidate
                for candidate in (
                    "repair-once",
                    "always-invalid",
                    "repair-success",
                    "model-valid",
                )
                if candidate in document
            ),
            "program-fallback",
        )
        diagnostics = ()
        if marker in {"repair-once", "always-invalid"}:
            diagnostics = (
                {
                    "code": "TEST_TEXT_OVERFLOW",
                    "category": "content",
                    "severity": "error",
                    "selector": '[data-box="content"]',
                    "message": "Synthetic overflow used to exercise graph routing.",
                    "suggestion": "Return a repaired page.",
                    "box": {},
                    "details": {"marker": marker},
                },
            )
        page = int(Path(png_path).name.split(".")[1].rsplit("-", 1)[-1])
        with self._lock:
            self.measure_markers.append(marker)
            self.rendered_pages.append(page)
        if diagnostics:
            return SimpleNamespace(
                measurement=SimpleNamespace(diagnostics=diagnostics),
                captured_png=None,
            )

        color = (20 + page * 11, 40 + page * 7, 70 + page * 5)
        image = Image.new("RGB", (3200, 1800), color)
        image.save(png_path, format="PNG")
        digest = hashlib.sha256(Path(png_path).read_bytes()).hexdigest()
        with self._lock:
            self.captured_pages.append(page)
        return SimpleNamespace(
            measurement=SimpleNamespace(diagnostics=diagnostics),
            captured_png=SimpleNamespace(path=Path(png_path), sha256=digest),
        )

    def capture(self, *_args, **_kwargs):
        raise AssertionError("capture must reuse the speculative PNG without loading HTML again")

    def metadata(self, *, font_families):
        return {
            "renderer": "deterministic-graph-test",
            "font_families": list(font_families),
            "render_profile": {"width": 1600, "height": 900, "deviceScaleFactor": 2},
        }


def _html_spec(
    *,
    slide_id: str,
    index: int,
    title: str,
    takeaway: str,
    layout_family: str,
    layout_variant: str,
    marker: str,
) -> dict:
    return HtmlSlideSpec.model_validate(
        {
            "slide_id": slide_id,
            "layout_family": layout_family,
            "layout_variant": layout_variant,
            "composition_summary": f"Deterministic integration composition for {slide_id}",
            "body_html": (
                f'<section class="slide integration-slide {marker}">'
                f'<header data-box="title" data-title-lines="2"><h1>{title}</h1></header>'
                f'<main data-box="content"><p>{takeaway}</p></main>'
                f'<footer data-box="footer">{index:02d}</footer>'
                "</section>"
            ),
            "page_css": "",
            "icon_keys": [],
            "estimated_density": "medium",
        }
    ).model_dump(mode="json")


def _initial_state(output_root: Path, request_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "renderer_version": RENDERER_VERSION,
        "task_id": request_id,
        "request_id": request_id,
        "project_dir": str((output_root / request_id).resolve()),
        "status": "created",
        "deck_title": "",
        "user_description": "生成8页演示，验证完整规划、统一设计、逐页修订和图片型导出。",
        "uploaded_file_text": "本地材料包含架构图、KPI 表格和八页验收说明。",
        "source_chunks": [
            {
                "source_file": "acceptance.md",
                "page": 1,
                "section": "三阶段",
                "excerpt": "三阶段链路必须保持全局样式、逐页规划、DOM QA 和图片型导出。",
            }
        ],
        "current_slide_index": 0,
        "completed_slides": {},
        "layout_history": [],
        "validation_issues": [],
    }


def test_three_model_html_graph_repairs_falls_back_streams_and_exports_eight_pages(tmp_path):
    outline = OutlineLLM()
    style = StyleLLM(concurrent_wave=4)
    designer = DesignerLLM(style)
    browser = DeterministicBrowserRenderer()
    graph = PptGraph(
        {"outline": outline, "style": style, "designer": designer},
        tmp_path,
        browser_session=object(),
        browser_renderer=browser,
        max_repairs=2,
        slide_concurrency=4,
    )

    latest_state: dict = {}
    previews: list[PipelineEvent] = []
    for mode, data in graph.stream(
        _initial_state(tmp_path, "html-graph-eight"),
        stream_mode=["custom", "values"],
    ):
        if mode == "values":
            latest_state = data
            continue
        assert mode == "custom"
        assert isinstance(data, PipelineEvent)
        if data.type is EventType.ARTIFACT_READY and any(
            resource.type is ResourceType.PAGE_PREVIEW for resource in data.resources
        ):
            previews.append(data)

    expected_slide_ids = [f"s{index:02d}" for index in range(1, 9)]
    assert outline.calls == 1
    assert len(outline.payloads) == 1
    assert Counter(style.calls) == Counter({slide_id: 1 for slide_id in expected_slide_ids})
    assert style.max_inflight >= 4
    assert sorted(style.completion_order) == expected_slide_ids
    assert style.completion_order != expected_slide_ids
    assert Counter(designer.initial_calls) == Counter(
        {slide_id: 1 for slide_id in expected_slide_ids}
    )
    assert designer.received_design_plans == style.plans
    assert set(designer.received_global_styles) == set(expected_slide_ids)
    assert all(value == GLOBAL_STYLE for value in designer.received_global_styles.values())
    assert sorted(designer.repair_calls) == [("s03", 1), ("s05", 1), ("s05", 2)]
    assert len(designer.repair_icon_shortlists) == 3
    assert all(len(keys) <= 12 for keys in designer.repair_icon_shortlists)
    assert all(
        any(item["code"] == "TEST_TEXT_OVERFLOW" for item in diagnostics)
        for diagnostics in designer.repair_diagnostics
    )

    validated_outline = DeckOutlineSpec.model_validate(latest_state["deck_outline"])
    assert validated_outline.metadata.global_style_spec.model_dump(mode="json") == GLOBAL_STYLE
    assert latest_state["global_style_spec"] == GLOBAL_STYLE
    assert latest_state["design_spec"]["global_style_spec"] == GLOBAL_STYLE
    for index, slide_id in enumerate(expected_slide_ids):
        plan = SlideDesignPlan.model_validate(style.plans[slide_id])
        assert plan.slide_id == slide_id
        assert plan.design_specifications.color_scheme.model_dump(mode="json") == GLOBAL_STYLE
        assert (
            plan.design_specifications.layout_type.value
            == validated_outline.slides[index].layout_suggestion.value
        )

    assert [event.progress.current for event in previews] == list(range(1, 9))
    preview_emission_order = [event.page.page_index for event in previews]
    assert preview_emission_order != list(range(1, 9))
    previews = sorted(previews, key=lambda event: event.page.page_index)
    assert [event.page.page_index for event in previews] == list(range(1, 9))
    assert [event.page.page_id for event in previews] == expected_slide_ids
    assert len({event.event_id for event in previews}) == 8
    assert all(event.event_id and event.revision for event in previews)
    assert [event.resources[0].relative_path for event in previews] == [
        f"previews/page-{index:03d}.png" for index in range(1, 9)
    ]

    assert latest_state["status"] == "completed"
    assert latest_state["current_slide_index"] == 8
    assert all(
        {
            "slide_id",
            "layout_family",
            "layout_variant",
            "composition_summary",
            "estimated_density",
        }
        <= item.keys()
        for item in latest_state["layout_history"]
    )
    completed = sorted(
        latest_state["completed_slides"].values(),
        key=lambda item: item["index"],
    )
    assert [item["slide_id"] for item in completed] == expected_slide_ids
    assert [
        designer.layout_preferences[slide_id]
        for slide_id in expected_slide_ids
    ] == [
        latest_state["layout_preferences"][slide_id]
        for slide_id in expected_slide_ids
    ]
    assert [
        len(designer.previous_compositions[slide_id])
        for slide_id in expected_slide_ids
    ] == [0, 1, 2, 3, 4, 4, 4, 4]
    assert [event.revision for event in previews] == [
        item["artifact_hash"] for item in completed
    ]
    assert completed[2]["repair_attempts"] == 1
    assert completed[2]["fallback_used"] is False
    assert completed[4]["repair_attempts"] == 2
    assert completed[4]["fallback_used"] is True
    assert all(len(item["preview_hash"]) == 64 for item in completed)
    assert Counter(browser.rendered_pages) == Counter(
        {1: 1, 2: 1, 3: 2, 4: 1, 5: 4, 6: 1, 7: 1, 8: 1}
    )
    assert Counter(browser.captured_pages) == Counter(range(1, 9))

    project = tmp_path / "html-graph-eight"
    assert not list(project.rglob("*.speculative.png"))
    assert json.loads((project / "deck-outline.json").read_text(encoding="utf-8")) == (
        validated_outline.model_dump(mode="json")
    )
    assert json.loads((project / "global-style.json").read_text(encoding="utf-8")) == GLOBAL_STYLE
    for index, item in enumerate(completed, start=1):
        slide_id = f"s{index:02d}"
        preview = project / item["preview_path"]
        assert preview.is_file()
        with Image.open(preview) as image:
            assert image.size == (3200, 1800)
            assert image.mode == "RGB"
        assert hashlib.sha256(preview.read_bytes()).hexdigest() == item["preview_hash"]

        design_path = project / item["design_plan_path"]
        html_spec_path = project / item["html_spec_path"]
        assert design_path == project / "slide_specs" / f"page-{index:03d}.design.json"
        assert html_spec_path == project / "slide_specs" / f"page-{index:03d}.html.json"
        assert json.loads(design_path.read_text(encoding="utf-8")) == style.plans[slide_id]
        assert HtmlSlideSpec.model_validate_json(
            html_spec_path.read_text(encoding="utf-8")
        ).slide_id == slide_id

    export_manifest = json.loads(
        (project / "reports" / "export-manifest.json").read_text(encoding="utf-8")
    )
    assert export_manifest["image_based"] is True
    assert export_manifest["editable"] is False
    assert [item["slide_id"] for item in export_manifest["slides"]] == expected_slide_ids
    slide_artifacts = json.loads(
        (project / "reports" / "slide-artifacts.json").read_text(encoding="utf-8")
    )
    assert [item["slide_id"] for item in slide_artifacts["slides"]] == expected_slide_ids

    presentation = Presentation(latest_state["artifact_path"])
    assert len(presentation.slides) == 8
    for slide in presentation.slides:
        assert len(slide.shapes) == 1
        picture = slide.shapes[0]
        assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
        assert (picture.left, picture.top) == (0, 0)
        assert picture.width == presentation.slide_width
        assert picture.height == presentation.slide_height
