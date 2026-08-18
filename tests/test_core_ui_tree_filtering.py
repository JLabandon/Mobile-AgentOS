from pathlib import Path

from mobile_agent_os.android.ui_tree import parse_ui_xml, prompt_snapshot


def test_parse_ui_xml_filters_layout_noise_and_keeps_controls(tmp_path: Path) -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="" resource-id="com.example:id/root_layout" class="android.widget.FrameLayout"
        package="com.example" content-desc="" clickable="false" enabled="true"
        focusable="false" checkable="false" checked="false" selected="false"
        focused="false" bounds="[0,0][1080,2400]" />
  <node text="Search in mail" resource-id="com.example:id/search" class="android.widget.EditText"
        package="com.example" content-desc="" clickable="true" enabled="true"
        focusable="true" checkable="false" checked="false" selected="false"
        focused="false" bounds="[100,100][900,220]" />
  <node text="" resource-id="com.example:id/fab_create" class="android.widget.Button"
        package="com.example" content-desc="Create new item" clickable="true"
        enabled="true" focusable="true" checkable="false" checked="false"
        selected="false" focused="false" bounds="[850,2000][1030,2180]" />
</hierarchy>
"""
    path = tmp_path / "window.xml"
    path.write_text(xml, encoding="utf-8")

    nodes = parse_ui_xml(path)

    assert [node.label for node in nodes] == ["Search in mail", "Create new item"]
    text = prompt_snapshot(nodes)
    assert "root_layout" not in text
    assert "bounds" in text


def test_clickable_container_inherits_descendant_text_and_action_point(tmp_path: Path) -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.Button" package="com.example"
        content-desc="" clickable="true" enabled="true" focusable="true"
        checkable="false" checked="false" selected="false" focused="false"
        bounds="[0,457][1080,622]">
    <node text="" resource-id="com.example:id/text_container" class="android.widget.LinearLayout"
          package="com.example" content-desc="" clickable="false" enabled="true"
          focusable="false" checkable="false" checked="false" selected="false"
          focused="false" bounds="[130,470][900,610]">
      <node text="Googleplex, Amphitheatre Parkway" resource-id="com.example:id/title"
            class="android.widget.TextView" package="com.example" content-desc=""
            clickable="false" enabled="true" focusable="false" checkable="false"
            checked="false" selected="false" focused="false" bounds="[170,500][850,550]" />
    </node>
  </node>
</hierarchy>
"""
    path = tmp_path / "window.xml"
    path.write_text(xml, encoding="utf-8")

    nodes = parse_ui_xml(path)

    assert nodes[0].clickable
    assert nodes[0].label == "Googleplex, Amphitheatre Parkway"
    assert nodes[0].action_center == (198, 525)


def test_clickable_container_with_clickable_child_keeps_container_center(tmp_path: Path) -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="" resource-id="com.example:id/card" class="androidx.cardview.widget.CardView"
        package="com.example" content-desc="" clickable="true" enabled="true"
        focusable="true" checkable="false" checked="false" selected="false"
        focused="false" bounds="[40,1000][1040,1350]">
    <node text="4:00 PM" resource-id="com.example:id/time" class="android.widget.TextView"
          package="com.example" content-desc="4:00 PM" clickable="true" enabled="true"
          focusable="true" checkable="false" checked="false" selected="false"
          focused="false" bounds="[100,1120][420,1300]" />
  </node>
</hierarchy>
"""
    path = tmp_path / "window.xml"
    path.write_text(xml, encoding="utf-8")

    nodes = parse_ui_xml(path)

    assert nodes[0].clickable
    assert nodes[0].label == "4:00 PM"
    assert nodes[0].action_center is None
    assert nodes[0].to_prompt_dict()["action_center"] == [540, 1175]
