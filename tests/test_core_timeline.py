import json

from mobile_agent_os.visualization.timeline import write_timeline


def test_timeline_contains_agents_states_and_ipc(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state_timeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"t": 0, "agent": "calendar_agent", "state": "READY"}),
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
    (run_dir / "metrics.json").write_text(json.dumps({"task": "calendar_gmail_meeting_detail", "runtime": "multidisplay_split_phase"}), encoding="utf-8")

    path = write_timeline(tmp_path, [run_dir])
    html = path.read_text(encoding="utf-8")
    assert "calendar_agent" in html
    assert "WAIT_PEER" in html
    assert "RuntimeInformationRequest" in html
    assert "Agent State Lanes" in html
    assert "IPC Ledger" in html
    assert "Key Runtime Events" in html
