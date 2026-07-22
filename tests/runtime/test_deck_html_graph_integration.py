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
from core.deck.pipeline.graph import PptGraph
from core.deck.domain.schemas import PROMPT_VERSION, RENDERER_VERSION, SCHEMA_VERSION


PAGE_CATEGORIES = (
    "cover",
    "catalog",
    "content",
    "chart",
    "section",
    "content",
    "summary",
    "thanks",
)


def _human_payload(messages) -> dict:
    return json.loads(messages[-1][1])


class BriefLLM:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        payload = _human_payload(messages)
        assert payload["raw_user_input"] == "验证完整规划、统一设计、逐页修订和图片型导出。"
        assert "explicit_options" not in payload
        assert "deterministic_brief" not in payload
        return {
            "title": "八页集成测试演示",
            "subtitle": "",
            "presenter": "",
            "audience": "演示系统开发与验收人员",
            "scene": "general",
            "goal": "验证演示文稿流水线的完整执行路径",
            "audience_outcome": "确认规划、设计、修订和导出能够连续完成",
            "communication_job": "演示结束时，验收人员应能确认完整流水线可连续执行。",
            "language": "zh-CN",
            "target_page_count": 8,
            "aspect_ratio": "16:9",
            "tone": "清晰、技术导向",
            "content_constraints": ["覆盖规划、统一设计、逐页修订和图片型导出"],
            "brand_preferences": [],
            "color_preferences": [],
            "style_preferences": [],
            "source_mode": "general_knowledge",
        }


class OutlineLLM:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raw_prompt = messages[-1][1]
        if raw_prompt.lstrip().startswith("{"):
            payload = json.loads(raw_prompt)
            assert payload["target_page_count"] == 8
            assert "hard_constraints" not in payload
            assert "forbidden_fields" not in payload
            return {
                "scene": "general",
                "pages": [
                    {
                        "page_no": index,
                        "page_category": category,
                        "topic": f"第{_CHINESE_ORDINALS[index - 1]}页形成独立判断",
                    }
                    for index, category in enumerate(PAGE_CATEGORIES, start=1)
                ],
            }
        assert "<presentation_context>" in raw_prompt
        assert 'intended for this audience: "演示系统开发与验收人员"' in raw_prompt
        assert "requested final deck length is 8 pages" in raw_prompt
        assert '"hard_constraints"' not in raw_prompt
        return {
            "title": "八页集成测试演示",
            "central_thesis": "先锁定整套计划与设计，再按顺序完成每一页。",
            "narrative_strategy": "建立问题，验证路径，最后收束行动。",
            "sections": [
                {
                    "section_id": "sec01",
                    "title": "完整执行路径",
                    "purpose": "验证规划、设计、修订和导出能够连续完成。",
                    "topics": [
                        {
                            "topic_id": f"t{index:02d}",
                            "title": f"第{_CHINESE_ORDINALS[index - 1]}个逻辑判断",
                            "intent": f"推进第{_CHINESE_ORDINALS[index - 1]}个叙事任务。",
                            "key_points": ["推进整套叙事，同时避免重复前序结论。"],
                        }
                        for index in range(1, 9)
                    ],
                }
            ],
        }


class ContentLLM:
    def __init__(self) -> None:
        self.slide_ids: list[str] = []
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0

    def invoke(self, messages):
        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            # A short overlap window makes Send concurrency observable without
            # making the integration test materially slower.
            time.sleep(0.02)
            payload = _human_payload(messages)
            slide = payload["current_slide"]
            self.slide_ids.append(slide["slide_id"])
            return {
                "slide_id": slide["slide_id"],
                "assertion_title": slide["assertion_title"],
                "takeaway": slide["takeaway"],
                "content_blocks": [
                    {
                        "type": "paragraph",
                        "text": slide["takeaway"],
                    }
                ],
                "emphasis": [slide["takeaway"]],
                "suggested_icon_keys": [],
            }
        finally:
            with self._lock:
                self._inflight -= 1


class DesignerLLM:
    def __init__(self) -> None:
        self.initial_calls: list[str] = []
        self.repair_calls: list[tuple[str, int]] = []
        self.repair_icon_shortlists: list[list[str]] = []
        self.layout_preferences: dict[str, dict] = {}
        self.previous_compositions: dict[str, list[dict]] = {}

    def invoke(self, messages):
        payload = _human_payload(messages)
        if "repair_attempt" in payload:
            slide = payload["slide_plan"]
            attempt = int(payload["repair_attempt"])
            self.repair_calls.append((slide["slide_id"], attempt))
            self.repair_icon_shortlists.append(payload["available_icon_keys"])
            marker = (
                "repair-success" if slide["slide_id"] == "s03" else "always-invalid"
            )
        else:
            slide = payload["current_slide"]
            self.initial_calls.append(slide["slide_id"])
            self.layout_preferences[slide["slide_id"]] = payload["layout_preference"]
            self.previous_compositions[slide["slide_id"]] = payload["previous_compositions"]
            if slide["slide_id"] == "s03":
                marker = "repair-once"
            elif slide["slide_id"] == "s05":
                marker = "always-invalid"
            else:
                marker = "model-valid"
        return _html_spec(slide, marker)


class DeterministicBrowserRenderer:
    """Exercise graph routing while leaving browser geometry to its own tests."""

    def __init__(self) -> None:
        self.measure_markers: list[str] = []
        self.rendered_pages: list[int] = []
        self.captured_pages: list[int] = []

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
        self.measure_markers.append(marker)
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
        self.rendered_pages.append(page)
        captured_png = None
        if diagnostics:
            return SimpleNamespace(
                measurement=SimpleNamespace(diagnostics=diagnostics),
                captured_png=captured_png,
            )
        self.captured_pages.append(page)
        color = (20 + page * 11, 40 + page * 7, 70 + page * 5)
        image = Image.new("RGB", (3200, 1800), color)
        image.save(png_path, format="PNG")
        digest = hashlib.sha256(Path(png_path).read_bytes()).hexdigest()
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


def _html_spec(slide: dict, marker: str) -> dict:
    index = int(slide["index"])
    return {
        "slide_id": slide["slide_id"],
        "layout_family": slide["layout_family"],
        "layout_variant": "integration_test",
        "composition_summary": (
            f"Deterministic integration composition for {slide['slide_id']}"
        ),
        "body_html": (
            f'<section class="slide integration-slide {marker}">'
            f'<header data-box="title" data-title-lines="2"><h1>{slide["assertion_title"]}</h1></header>'
            f'<main data-box="content"><p>{slide["takeaway"]}</p></main>'
            f'<footer data-box="footer">{index:02d}</footer>'
            "</section>"
        ),
        "page_css": "",
        "icon_keys": [],
        "estimated_density": "medium",
    }


_CHINESE_ORDINALS = ("一", "二", "三", "四", "五", "六", "七", "八")


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
        "user_description": "验证完整规划、统一设计、逐页修订和图片型导出。",
        "uploaded_file_text": "",
        "source_chunks": [],
        "current_slide_index": 0,
        "completed_slides": {},
        "layout_history": [],
        "validation_issues": [],
    }


def test_parallel_html_graph_repairs_falls_back_streams_and_exports_eight_pages(tmp_path):
    brief = BriefLLM()
    outline = OutlineLLM()
    content = ContentLLM()
    designer = DesignerLLM()
    browser = DeterministicBrowserRenderer()
    graph = PptGraph(
        {"brief": brief, "outline": outline, "writer": content, "designer": designer},
        tmp_path,
        browser_session=object(),
        browser_renderer=browser,
        max_repairs=2,
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

    assert latest_state.get("deck_plan") and latest_state.get("design_spec")
    assert brief.calls == 1
    assert outline.calls == 2
    assert content.max_inflight >= 2
    expected_slide_ids = [f"s{index:02d}" for index in range(1, 9)]
    assert sorted(content.slide_ids) == expected_slide_ids
    assert sorted(designer.initial_calls) == expected_slide_ids
    assert sorted(designer.repair_calls) == [("s03", 1), ("s05", 1), ("s05", 2)]
    assert len(designer.repair_icon_shortlists) == 3
    assert all(len(keys) <= 12 for keys in designer.repair_icon_shortlists)

    assert [event.progress.current for event in previews] == list(range(1, 9))
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
        {"slide_id", "layout_family", "layout_variant", "composition_summary", "estimated_density"}
        <= item.keys()
        for item in latest_state["layout_history"]
    )
    completed = sorted(
        latest_state["completed_slides"].values(), key=lambda item: item["index"]
    )
    assert [item["slide_id"] for item in completed] == [
        f"s{index:02d}" for index in range(1, 9)
    ]
    assert [
        designer.layout_preferences[slide_id]["layout_variant"]
        for slide_id in expected_slide_ids
    ] == [
        "offset_orbit",
        "numbered_axis",
        "text_left_signal_right",
        "hero_left",
        "band",
        "evidence_left_answer_right",
        "centered",
        "answer_field",
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
    for index, item in enumerate(completed, start=1):
        preview = project / item["preview_path"]
        assert preview.is_file()
        with Image.open(preview) as image:
            assert image.size == (3200, 1800)
            assert image.mode == "RGB"
        assert hashlib.sha256(preview.read_bytes()).hexdigest() == item["preview_hash"]

    export_manifest = json.loads(
        (project / "reports" / "export-manifest.json").read_text(encoding="utf-8")
    )
    assert export_manifest["image_based"] is True
    assert export_manifest["editable"] is False
    assert [item["slide_id"] for item in export_manifest["slides"]] == [
        f"s{index:02d}" for index in range(1, 9)
    ]
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
