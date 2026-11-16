"""Speaking a sentence: live Sarvam text-to-speech, else the pre-generated clip of exactly that sentence (W0.5, W3.2).

One rule for every endpoint that speaks: the clip is only ever the sentence that was asked for (the cache is keyed on the
language and the exact text), it says ``cached: true`` when it is not live, and when there is neither live audio nor a clip
the caller gets ``VoiceUnavailable`` with the honest reason - never silence passed off as an answer.
"""

from __future__ import annotations

import base64

from verity.audio_cache import AudioCache
from verity.voice import SarvamClient, VoiceUnavailable

VOICE_MESSAGE = "Voice is not configured on this server (no SARVAM_API_KEY); use browser speech and the keypad."


def synthesise(text: str, language: str, voice: SarvamClient | None, cache: AudioCache) -> dict:
    """``{"audio_base64", "format": "wav", "cached"}`` for ``text``; raises ``VoiceUnavailable`` when it cannot be spoken."""
    if voice is None:
        failure = VOICE_MESSAGE
    else:
        try:
            return {**voice.tts(text, language), "cached": False}
        except VoiceUnavailable as exc:
            failure = str(exc)
    clip = cache.lookup(text, language)
    if clip is None:
        raise VoiceUnavailable(failure)
    return {"audio_base64": base64.b64encode(clip).decode("ascii"), "format": "wav", "cached": True}


def try_synthesise(text: str, language: str, voice: SarvamClient | None, cache: AudioCache) -> dict | None:
    """As ``synthesise``, but ``None`` instead of an error: for answers that are still useful as text (the app then speaks
    them with the browser's own voice)."""
    try:
        return synthesise(text, language, voice, cache)
    except VoiceUnavailable:
        return None
