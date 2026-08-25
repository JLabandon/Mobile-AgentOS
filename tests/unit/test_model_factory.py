from mobile_agent_os.model_clients.factory import create_screen_model_client, create_text_model_client
from mobile_agent_os.model_clients.gemini import DEFAULT_GEMINI_MODEL, GeminiScreenClient


def test_gemini_lite_is_the_default_text_and_screen_client(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-key")
    text = create_text_model_client()
    screen = create_screen_model_client()
    assert isinstance(text, GeminiScreenClient)
    assert isinstance(screen, GeminiScreenClient)
    assert text.model == DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
