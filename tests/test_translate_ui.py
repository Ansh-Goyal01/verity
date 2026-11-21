import json

import httpx
import pytest

from scripts.translate_ui import MODEL, translate


def test_translates_unique_strings_and_keeps_placeholders():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"translated_text": "बैटरी {battery}%"})

    source = {"header": "Battery {battery}%", "nested": {"labels": ["Battery {battery}%", ""]}}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = translate(source, client, "test-key")
    assert result == {"header": "बैटरी {battery}%", "nested": {"labels": ["बैटरी {battery}%", ""]}}
    assert requests == [{"input": "Battery {battery}%", "source_language_code": "en-IN",
                         "target_language_code": "hi-IN", "model": MODEL}]


def test_rejects_translation_with_modified_placeholder():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"translated_text": "बैटरी {charge}"}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match="changed placeholders"):
            translate({"header": "Battery {battery}"}, client, "test-key")
