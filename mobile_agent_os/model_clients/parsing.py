from __future__ import annotations

import json

from .base import ModelClientError


def parse_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char == "{":
                try:
                    value, _ = decoder.raw_decode(text[index:])
                    break
                except json.JSONDecodeError:
                    pass
        else:
            raise ModelClientError("model did not return a JSON object") from None
    if not isinstance(value, dict):
        raise ModelClientError("model did not return a JSON object")
    return value
