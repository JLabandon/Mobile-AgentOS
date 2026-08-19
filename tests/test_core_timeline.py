import json

from mobile_agent_os.visualization.timeline import write_timeline


def test_timeline_contains_agents_states_and_ipc(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state_timeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"t": 0, "agent": "calendar_agent", "state": "READY"}),
                json.dumps({"t": 0.5, "agent": "calendar_agent", "state": "SWITCH"}),
                json.dumps({"t": 1, "agent": "calendar_agent", "state": "WAIT_PEER"}),
                json.dumps({"t": 2, "agent": "keep_agent", "state": "RUNNING"}),
                json.dumps({"t": 3, "agent": "calendar_agent", "state": "DONE"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "ipc_ledger.jsonl").write_text(
        json.dumps(
            {
                "status": "created",
                "from_agent": "calendar_agent",
                "to_agent": "keep_agent",
                "message_kind": "RuntimeInformationRequest",
                "request_summary": "meeting location",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"time": "2026-08-13T10:00:00", "kind": "app_launch", "agent": "calendar_agent", "package": "com.google.android.calendar"}),
                json.dumps({"time": "2026-08-13T10:00:02", "kind": "peer_result_delivered", "source_agent": "keep_agent", "target_agent": "calendar_agent"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(json.dumps({"task": "calendar_gmail_meeting_detail", "runtime": "agentos_parallel"}), encoding="utf-8")

    path = write_timeline(tmp_path, [run_dir])
    html = path.read_text(encoding="utf-8")
    assert "calendar_agent" in html
    assert "WAIT_PEER" in html
    assert "RuntimeInformationRequest" in html
    assert "Agent State Lanes" in html
    assert "IPC Ledger" in html
    assert "Key Runtime Events" in html
    assert "SWITCH" in html
    assert "class=\"switch\"" not in html
    assert "table-layout: fixed" in html
    assert "overflow-wrap: anywhere" in html


def test_steward_serial_renders_as_one_lane(tmp_path) -> None:
    run_dir = tmp_path / "steward"
    run_dir.mkdir()
    (run_dir / "state_timeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"t": 0, "agent": "gmail_agent", "state": "SWITCH"}),
                json.dumps({"t": 1, "agent": "gmail_agent", "state": "THINKING"}),
                json.dumps({"t": 2, "agent": "calendar_agent", "state": "SWITCH"}),
                json.dumps({"t": 3, "agent": "calendar_agent", "state": "ACTING"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "ipc_ledger.jsonl").write_text("", encoding="utf-8")
    (run_dir / "trace.jsonl").write_text("", encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps({"task": "x", "runtime": "steward_serial"}), encoding="utf-8")

    html = write_timeline(tmp_path, [run_dir]).read_text(encoding="utf-8")

    assert "Steward Serial" in html
    assert "stewardSerialLane" in html
    assert "shortState(ev.state)" in html
    assert "stewardAgentLegend" in html
    assert "agent-coded" in html


def test_timeline_keeps_runtime_start_clock_and_planning_gap(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state_timeline.jsonl").write_text(
        json.dumps({"t": 4.0, "agent": "calendar_agent", "state": "THINKING"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "ipc_ledger.jsonl").write_text("", encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        json.dumps({"time": "2026-08-13T10:00:00", "t": 0.0, "kind": "runtime_start"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps({"task": "x", "runtime": "agentos_parallel", "wall_clock_time": 10.0}),
        encoding="utf-8",
    )

    html = write_timeline(tmp_path, [run_dir]).read_text(encoding="utf-8")

    assert "PLANNING" in html
    assert '"t": 4.0' in html
    assert '"wall_clock_time": 10.0' in html
