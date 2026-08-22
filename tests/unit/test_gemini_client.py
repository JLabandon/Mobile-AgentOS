from types import SimpleNamespace

from mobile_agent_os.model_clients.gemini import GeminiScreenClient


def test_gemini_screen_client_parses_action_response_without_network(tmp_path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"fixture")
    client = GeminiScreenClient.__new__(GeminiScreenClient)
    client.model = "fixture-model"
    client._types = SimpleNamespace(
        Part=SimpleNamespace(from_bytes=lambda **kwargs: kwargs),
        GenerateContentConfig=lambda **kwargs: kwargs,
    )
    client.client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: SimpleNamespace(text='{"action":"complete","message":"done"}'))
    )
    client.build_action_prompt = lambda **kwargs: "fixture prompt"

    assert client.decide_ui_action(
        screenshot_path=screenshot,
        agent_name="notes",
        app_label="Notes",
        task_instruction="Read a note.",
    ) == {"action": "complete", "message": "done"}
