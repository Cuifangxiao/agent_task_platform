import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from core.deck.domain.contracts import EventType, PipelineEvent, ResourceType
from core.deck.pipeline.graph import PptGraph
from core.deck.domain.schemas import PROMPT_VERSION, RENDERER_VERSION, SCHEMA_VERSION


SLIDE_ROSTER = (
    ("cover", "cover_minimal", "anchor"),
    ("context", "editorial_split", "dense"),
    ("insight", "three_points", "breathing"),
    ("evidence", "comparison", "dense"),
    ("problem", "statement_focus", "anchor"),
    ("process", "process", "dense"),
    ("decision", "timeline", "breathing"),
    ("closing", "closing_resolution", "anchor"),
)


def _human_payload(messages) -> dict:
    return json.loads(messages[-1][1])


class OutlineLLM:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        payload = _human_payload(messages)
        assert payload["hard_constraints"]["slide_count"] == 8
        slides = []
        for index, (role, family, rhythm) in enumerate(SLIDE_ROSTER, start=1):
            slides.append(
                {
                    "slide_id": f"s{index:02d}",
                    "index": index,
                    "role": role,
                    "assertion_title": f"第{_CHINESE_ORDINALS[index - 1]}页形成独立判断",
                    "takeaway": f"这一页推进{_CHINESE_ORDINALS[index - 1]}个叙事任务。",
                    "content_intent": "推进整套叙事，同时避免重复前页结论。",
                    "layout_family": family,
                    "rhythm": rhythm,
                    "transition_from_previous": (
                        "承接前页并提出下一层问题。" if index > 1 else ""
                    ),
                }
            )
        return {
            "deck_title": "八页集成测试演示",
            "core_message": "先锁定整套计划与设计，再按顺序完成每一页。",
            "narrative_arc": ["建立问题", "验证路径", "收束行动"],
            "slides": slides,
        }


class ContentLLM:
    def __init__(self) -> None:
        self.slide_ids: list[str] = []

    def invoke(self, messages):
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
            "source_refs": [],
        }


class DesignerLLM:
    def __init__(self) -> None:
        self.initial_calls: list[str] = []
        self.repair_calls: list[tuple[str, int]] = []
        self.repair_icon_shortlists: list[list[str]] = []

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
        self.captured_pages: list[int] = []

    def measure(self, document: str, *, font_families):
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
        return SimpleNamespace(diagnostics=diagnostics)

    def capture(self, document: str, png_path: Path, *, font_families):
        del font_families
        assert "repair-once" not in document
        assert "always-invalid" not in document
        page = int(Path(png_path).stem.rsplit("-", 1)[-1])
        self.captured_pages.append(page)
        color = (20 + page * 11, 40 + page * 7, 70 + page * 5)
        image = Image.new("RGB", (3200, 1800), color)
        image.save(png_path, format="PNG")
        digest = hashlib.sha256(Path(png_path).read_bytes()).hexdigest()
        return SimpleNamespace(path=Path(png_path), sha256=digest)

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
        "deck_title": "八页集成测试演示",
        "user_description": "验证完整规划、统一设计、逐页修订和图片型导出。",
        "uploaded_file_text": "",
        "source_chunks": [],
        "expected_slide_count": 8,
        "language": "zh-CN",
        "aspect_ratio": "16:9",
        "requested_template_id": "executive_signal",
        "current_slide_index": 0,
        "completed_slides": {},
        "layout_history": [],
        "validation_issues": [],
    }


def test_flat_html_graph_repairs_falls_back_streams_and_exports_eight_pages(tmp_path):
    outline = OutlineLLM()
    content = ContentLLM()
    designer = DesignerLLM()
    browser = DeterministicBrowserRenderer()
    graph = PptGraph(
        {"outline": outline, "writer": content, "designer": designer},
        tmp_path,
        browser_session=object(),
        browser_renderer=browser,
        max_repairs=2,
    )

    latest_state: dict = {}
    previews: list[PipelineEvent] = []
    page_events_saw_global_contract: list[bool] = []
    for mode, data in graph.stream(
        _initial_state(tmp_path, "html-graph-eight"),
        stream_mode=["custom", "values"],
    ):
        if mode == "values":
            latest_state = data
            continue
        assert mode == "custom"
        assert isinstance(data, PipelineEvent)
        if data.page is not None:
            page_events_saw_global_contract.append(
                bool(latest_state.get("deck_plan") and latest_state.get("design_spec"))
            )
        if data.type is EventType.ARTIFACT_READY and any(
            resource.type is ResourceType.PAGE_PREVIEW for resource in data.resources
        ):
            previews.append(data)

    assert page_events_saw_global_contract and all(page_events_saw_global_contract)
    assert outline.calls == 1
    assert content.slide_ids == [f"s{index:02d}" for index in range(1, 9)]
    assert designer.initial_calls == [f"s{index:02d}" for index in range(1, 9)]
    assert designer.repair_calls == [("s03", 1), ("s05", 1), ("s05", 2)]
    assert len(designer.repair_icon_shortlists) == 3
    assert all(len(keys) <= 12 for keys in designer.repair_icon_shortlists)

    assert [event.page.page_index for event in previews] == list(range(1, 9))
    assert [event.page.page_id for event in previews] == [
        f"s{index:02d}" for index in range(1, 9)
    ]
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
    assert [event.revision for event in previews] == [
        item["artifact_hash"] for item in completed
    ]
    assert completed[2]["repair_attempts"] == 1
    assert completed[2]["fallback_used"] is False
    assert completed[4]["repair_attempts"] == 2
    assert completed[4]["fallback_used"] is True
    assert all(len(item["preview_hash"]) == 64 for item in completed)
    assert browser.captured_pages == list(range(1, 9))

    project = tmp_path / "html-graph-eight"
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

    presentation = Presentation(latest_state["artifact_path"])
    assert len(presentation.slides) == 8
    for slide in presentation.slides:
        assert len(slide.shapes) == 1
        picture = slide.shapes[0]
        assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
        assert (picture.left, picture.top) == (0, 0)
        assert picture.width == presentation.slide_width
        assert picture.height == presentation.slide_height
