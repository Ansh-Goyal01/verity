# Sarvam API notes (W3.1)

What the vendor documentation said on **2026-11-16**, what was NOT read, and what the code does about it. Read by a
fetch agent from the public pages below (page text only; nothing from this project was sent). Anything not listed
under "read" is unverified: do not treat it as checked.

## Pages read

- https://docs.sarvam.ai/api/getting-started/models
- https://docs.sarvam.ai/
- https://docs.sarvam.ai/api/api-guides-tutorials/models/saaras (speech-to-text)
- https://docs.sarvam.ai/api/api-guides-tutorials/models/bulbul (text-to-speech)
- https://docs.sarvam.ai/api/token-factory/chat-completions (the first ~39,900 of 51,777 characters verbatim)
- https://docs.sarvam.ai/api/api-guides-tutorials/text-processing/overview (translate)

## Pages NOT read (so unknown here)

The `/api-reference/...` endpoint pages (speech-to-text transcribe, text-to-speech convert, translate, chat
completions v1) and the pricing / rate-limits page (`/api/getting-started/pricing#rate-limits`). **No rate limit or
price figure is known from the docs.** The response shape of text-to-speech is not stated on the page read; the
`audios` array this client parses was verified by live calls (F7, 2026-11-12, and the six cached clips of W0.5).

## Facts, by model family

| Family | The docs say | This project |
| --- | --- | --- |
| Speech-to-text | `saaras:v4` is the default and recommended model; `saaras:v3` is "previous, still available". `mode` (`transcribe` default, `translate`, `verbatim`, `translit`, `codemix`) works on v3 and v4 only. `language_code` omitted or `unknown` auto-detects. `keyterms` (up to 50, v4 only) biases recognition. Synchronous REST: clips up to 30 s, formats WAV/MP3/AAC/OGG/OPUS/FLAC/WebM and others auto-detected. Response: `transcript`, `language_code`, `language_probability`, `timestamps`, `diarized_transcript`. `mode=translate` "always produces English text" and the page documents no second, original-script field. | default `saaras:v4`, mode `transcribe`, chosen by the 2026-11-16 bench (v4 writes digits; v3 writes number words; `docs/DECISIONS.md` W3.1); `SARVAM_STT_MODEL`, `SARVAM_STT_MODE` |
| Text-to-speech | `bulbul:v4-flash` is "latest, recommended for new integrations"; `bulbul:v3` is the stable API default when `model` is omitted; `bulbul:v2` legacy. Fields: `text`, `language_code`, `model`, `speaker`, `pace` (0.5-2.0), `speech_sample_rate` (default 22050 Hz). Max 2,500 characters per request. v3 default speaker `shubh` is NOT valid on v4-flash (which wants persona ids such as `simran_en_customer`). | default `bulbul:v3`, default speaker; `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER` |
| Chat | `sarvam-105b` (128K context, hybrid reasoning, `low` by default; `reasoning_effort` low / medium / high, and null switches thinking off); `sarvam-105b-conversations` (32K, no reasoning); `sarvam-30b` and `sarvam-m` deprecated. `POST /v1/chat/completions`; `response_format` `json_schema` (with `strict`) and `json_object`; auth `api-subscription-key` or `Authorization: Bearer`. | default `sarvam-105b`, reasoning `low`; `SARVAM_CHAT_MODEL`, `SARVAM_CHAT_REASONING` (`low`/`medium`/`high`/`none`=null/`omit`) |
| Translate | `mayura:v1` (1,000 characters max input) and `sarvam-translate:v1` (2,000). `POST /translate`. | `mayura:v1` |

## Discrepancies and notices in the docs

- The models page says Sarvam-Translate covers 23 languages (22 Indian + English); the text-processing page says 22
  scheduled Indian languages. Not resolved; we do not rely on either number.
- The homepage says "Bulbul V3 reaches end of life on **Voice Agents** on November 10, 2026". That is a different
  product from the text-to-speech API this project calls; no sunset for `bulbul:v3` or `saaras:v3` was found on the API pages.
- The fetch result also contained text unrelated to Sarvam (instructions for other tools); it was ignored.

## What the code does about it (engine/verity/voice.py)

`VoiceModels` holds every id; `models_from_settings` reads `SARVAM_*` from the environment, then `.env`; a bad mode or
reasoning value fails at startup naming the setting. The paid-call log (`SarvamClient.calls`) records the model each call
actually used. Which defaults the demo runs on is decided by measurement, in `docs/DECISIONS.md` (W3.1) and
`results/voice_ride_check_latency.json`, not by the vendor's "recommended" label.
