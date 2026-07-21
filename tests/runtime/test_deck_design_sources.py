from xml.etree import ElementTree as ET

from core.deck.design import (
    list_template_packs,
    list_visualizations,
    render_template_reference_svg,
    render_visualization_reference_svg,
    select_visualization,
    visualization_svg_source,
)
from core.deck.quality.svg import validate_svg_page
from core.deck.rendering.svg import SVG_ALLOWED_TAGS


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _assert_restricted_svg(svg: str) -> ET.Element:
    root = ET.fromstring(svg)
    assert root.get("width") == "1280"
    assert root.get("height") == "720"
    assert root.get("viewBox") == "0 0 1280 720"
    ids = []
    for element in root.iter():
        assert _local_name(element.tag) in SVG_ALLOWED_TAGS
        if element.get("id"):
            ids.append(element.get("id"))
        for name in element.attrib:
            local = _local_name(name).casefold()
            assert local not in {"href", "style", "transform"}
            assert not local.startswith("on")
    assert len(ids) == len(set(ids))
    return root


def test_every_template_layout_has_a_complete_valid_reference_svg():
    for pack in list_template_packs():
        for layout in pack.layouts:
            svg = render_template_reference_svg(pack.template_id, layout["layout_id"])
            root = _assert_restricted_svg(svg)

            assert root.get("data-reference-kind") == "template"
            assert root.get("data-template-id") == pack.template_id
            assert root.get("data-template-mode") == pack.template_mode
            assert root.get("data-layout-id") == layout["layout_id"]
            assert root.find(".//*[@id='template-background']").get("fill") in set(pack.theme.palette.values())
            expected_zones = len(layout.get("geometry") or {})
            assert len(root.findall(".//*[@data-zone]")) == expected_zones
            assert validate_svg_page(svg, page=1, theme=pack.theme) == []


def test_template_reference_accepts_confirmed_theme_without_changing_geometry():
    pack = list_template_packs()[0]
    theme = pack.theme.to_dict()
    theme["palette"]["accent"] = "#123456"

    svg = render_template_reference_svg(pack.template_id, pack.layouts[0]["layout_id"], theme=theme)
    root = _assert_restricted_svg(svg)

    assert "#123456" in svg
    assert len(root.findall(".//*[@data-zone]")) == len(pack.layouts[0]["geometry"])


def test_catalog_has_complete_executable_template_packs():
    packs = list_template_packs()
    assert len(packs) == 11
    assert sum(pack.source.get("kind") == "ppt-master" for pack in packs) == 7

    for pack in packs:
        assert pack.theme.palette
        assert pack.theme.fonts
        assert pack.layout_principles
        assert pack.layouts


def test_visualization_catalog_covers_the_common_artist_grammars():
    expected = {"bar", "line", "donut", "timeline", "process", "matrix", "funnel", "architecture", "table", "kpi", "comparison"}
    assert {item.visualization_id for item in list_visualizations()} == expected


def test_visualization_selection_prefers_intent_then_component():
    assert select_visualization(component="chart-takeaway", intent="按月折线趋势图").visualization_id == "line"
    assert select_visualization(component="content-basic", intent="用户转化漏斗").visualization_id == "funnel"
    assert select_visualization(component="comparison", intent="各区域销售排名").visualization_id == "bar"
    assert select_visualization(component="quadrant").visualization_id == "matrix"
    assert select_visualization(component="data-table").visualization_id == "table"
    assert select_visualization(intent="系统分层架构").visualization_id == "architecture"


def test_every_visualization_renders_as_valid_native_safe_svg():
    pack = list_template_packs()[1]
    outputs = set()
    for spec in list_visualizations():
        svg = render_visualization_reference_svg(spec.visualization_id, template_id=pack.template_id)
        outputs.add(svg)
        root = _assert_restricted_svg(svg)

        assert root.get("data-reference-kind") == "visualization"
        assert root.get("data-visualization-id") == spec.visualization_id
        assert root.get("data-template-id") == pack.template_id
        assert validate_svg_page(svg, page=1, theme=pack.theme) == []
    assert len(outputs) == len(list_visualizations())


def test_visualization_svg_source_selects_and_renders_in_one_call():
    svg = visualization_svg_source(component="chart-takeaway", intent="渠道占比环形图", template_id="keynote_canvas")
    root = _assert_restricted_svg(svg)

    assert root.get("data-visualization-id") == "donut"
    assert root.get("data-template-id") == "keynote_canvas"
