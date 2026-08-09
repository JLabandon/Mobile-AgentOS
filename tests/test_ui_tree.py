from pathlib import Path

from agent_ipc_mvp.ui_tree import find_node, parse_bounds, parse_ui_xml, visible_texts


def test_parse_bounds_center() -> None:
    bounds = parse_bounds("[10,20][30,60]")
    assert bounds.center == (20, 40)


def test_parse_ui_xml_and_match(tmp_path: Path) -> None:
    xml = tmp_path / "window.xml"
    xml.write_text(
        """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="Title" content-desc="" resource-id="field_title" class="android.widget.EditText" clickable="true" enabled="true" bounds="[0,0][100,40]" />
  <node text="" content-desc="Add alarm" resource-id="button_alarm" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,50][100,90]" />
</hierarchy>
""",
        encoding="utf-8",
    )
    nodes = parse_ui_xml(xml)
    assert len(nodes) == 2
    assert visible_texts(nodes) == ["Title", "Add alarm"]
    assert find_node(nodes, target_text="alarm").resource_id == "button_alarm"
    assert find_node(nodes, editable_only=True).resource_id == "field_title"
