import io
import zipfile
from unittest.mock import patch

import pytest

from app.services.openai_code_interpreter import (
    OpenAICodeInterpreterError,
    _extract_container_artifacts,
    validate_pptx_bytes,
    validate_visual_design_bytes,
)


def _valid_pptx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("ppt/presentation.xml", "<presentation></presentation>")
    return buf.getvalue()


def test_validate_pptx_bytes_accepts_minimal_valid_package() -> None:
    validate_pptx_bytes(_valid_pptx_bytes())


def test_validate_pptx_bytes_rejects_invalid_package() -> None:
    with pytest.raises(OpenAICodeInterpreterError):
        validate_pptx_bytes(b"not-a-pptx")


def test_extract_container_artifacts_finds_file_citation() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Done",
                        "annotations": [
                            {
                                "type": "container_file_citation",
                                "container_id": "ctr_123",
                                "file_id": "file_456",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert _extract_container_artifacts(response) == ("ctr_123", "file_456")


def test_extract_container_artifacts_finds_code_interpreter_output_file() -> None:
    response = {
        "output": [
            {
                "type": "code_interpreter_call",
                "container_id": "ctr_789",
                "outputs": [
                    {
                        "type": "files",
                        "files": [
                            {
                                "file_id": "file_pptx",
                                "filename": "generated_presentation.pptx",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert _extract_container_artifacts(response) == ("ctr_789", "file_pptx")


def test_validate_visual_design_rejects_text_only_deck() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<p:sp><a:t>Title</a:t></p:sp><p:sp><a:t>Body</a:t></p:sp>',
        )
        zf.writestr(
            "ppt/slides/slide2.xml",
            '<p:sp><a:t>Title</a:t></p:sp><p:sp><a:t>Body</a:t></p:sp>',
        )
    with pytest.raises(OpenAICodeInterpreterError, match="visual design"):
        validate_visual_design_bytes(buf.getvalue())


def test_http_json_retries_transient_connection_failures() -> None:
    from app.services import openai_code_interpreter as provider

    class Response:
        def raise_for_status(self):
            return None

    calls = {"count": 0}

    def request(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise provider.requests.exceptions.ConnectionError("reset")
        return Response()

    with patch.object(provider.requests, "request", side_effect=request), patch.object(provider.time, "sleep"):
        result = provider._http_json("POST", "/responses", token="test", timeout=30)

    assert isinstance(result, Response)
    assert calls["count"] == 3


def test_hybrid_visual_spec_parses_responses_output() -> None:
    from app.services.hybrid_visual_spec import _parse_json, _output_text

    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '[{"type":"title","title":"Topic","content":{"subtitle":"Brief"}},{"type":"cards","title":"Pillars","content":{"cards":[]}},{"type":"thank_you","title":"Next steps","content":{"bullets":["Act"]}}]',
                    }
                ],
            }
        ]
    }
    assert len(_parse_json(_output_text(payload))) == 3
