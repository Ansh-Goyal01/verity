"""Generate the Hindi UI strings from the approved English JSON source."""

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from verity.paths import REPO_ROOT

MODEL = "sarvam-translate:v1"
TRANSLATE_URL = "https://api.sarvam.ai/translate"
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.-]*\}")


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    raise ValueError("Expected UI strings, objects or lists in the source JSON.")


def substitute(value: str | dict | list, translations: dict[str, str]) -> str | dict | list:
    if isinstance(value, str):
        return translations.get(value, value)
    if isinstance(value, dict):
        return {key: substitute(child, translations) for key, child in value.items()}
    return [substitute(child, translations) for child in value]


def translate(source: dict, client: httpx.Client, key: str) -> dict:
    unique = dict.fromkeys(strings(source))
    if any(len(text) > 2000 for text in unique):
        raise ValueError("A UI string exceeds the 2,000 character translation limit.")
    translated = {}
    for index, text in enumerate(unique, start=1):
        response = client.post(TRANSLATE_URL, headers={"api-subscription-key": key}, json={
            "input": text, "source_language_code": "en-IN", "target_language_code": "hi-IN", "model": MODEL,
        })
        if not response.is_success:
            raise ValueError(f"Translation failed on string {index} (HTTP {response.status_code}).")
        result = response.json().get("translated_text")
        if not isinstance(result, str) or not result.strip():
            raise ValueError(f"Translation was empty on string {index}.")
        if Counter(PLACEHOLDER.findall(text)) != Counter(PLACEHOLDER.findall(result)):
            raise ValueError(f"Translation changed placeholders on string {index}.")
        translated[text] = result
    return substitute(source, translated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("english_json", type=Path)
    args = parser.parse_args()
    output = REPO_ROOT / "web" / "i18n" / "hi.json"
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        parser.exit(1, "Set SARVAM_API_KEY in the process environment.\n")
    if args.english_json.resolve() == output.resolve():
        parser.exit(1, "Provide the English source JSON.\n")
    try:
        source = json.loads(args.english_json.read_text(encoding="utf-8-sig"))
        if not isinstance(source, dict) or not source:
            raise ValueError("Expected a nonempty JSON object of UI strings.")
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            hindi = translate(source, client, key)
        output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=output.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                json.dump(hindi, temp, ensure_ascii=False, indent=2)
                temp.write("\n")
                temp.close()
                temp_path.replace(output)
            finally:
                temp_path.unlink(missing_ok=True)
    except (httpx.HTTPError, OSError, ValueError, AttributeError) as exc:
        message = (str(exc) if type(exc) is ValueError else "Could not read, translate or write the UI strings.")
        parser.exit(1, message + "\n")
    print("Wrote web/i18n/hi.json for Ansh’s review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
