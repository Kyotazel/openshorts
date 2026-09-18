"""Bahasa untuk semua teks copy yang ditulis Gemini (caption, hook, judul).

Dulu prompt detail/visual diakhiri dengan "tulis SEMUA teks dalam
TRANSCRIPT_LANGUAGE", jadi video berbahasa Inggris menghasilkan caption
Inggris — padahal audiensnya Indonesia. Sekarang bahasa copy punya setelannya
sendiri:

    COPY_LANGUAGE=id      # default: Indonesia, apa pun bahasa videonya
    COPY_LANGUAGE=auto    # ikut bahasa transkrip lagi (perilaku lama)
    COPY_LANGUAGE=es      # kode bahasa lain

Hanya memakai standard library, seperti clip_selection.py: main.py dan
hook_grounding.py sama-sama mengimpornya, dan tesnya harus bisa jalan tanpa
dependensi video yang berat.
"""
import os

DEFAULT_COPY_LANGUAGE = "id"
_FOLLOW_TRANSCRIPT = {"auto", "source", "transcript"}
# Whisper menulis "unknown" kalau tidak yakin; itu bukan kode bahasa.
_UNKNOWN = {"", "unknown", "none", "n/a", "null"}

# Ditulis lengkap untuk prompt: kode telanjang "id" terbaca seperti nama
# variabel, dan model jauh lebih patuh pada "tulis dalam Indonesian (id)".
_LANGUAGE_NAMES = {
    "id": "Indonesian", "en": "English", "es": "Spanish", "pt": "Portuguese",
    "ms": "Malay", "hi": "Hindi", "ar": "Arabic", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "fr": "French", "de": "German",
    "ru": "Russian", "tr": "Turkish", "th": "Thai", "vi": "Vietnamese",
    "it": "Italian", "nl": "Dutch", "tl": "Filipino",
}


def copy_language_for(transcript_language=None) -> str:
    """Kode bahasa untuk copy, dari env COPY_LANGUAGE (default Indonesia).

    Tidak diset atau kosong -> DEFAULT_COPY_LANGUAGE.
    "auto"/"source"/"transcript" -> ikut bahasa transkrip (perilaku lama).
    Nilai lain -> dipakai apa adanya sebagai kode bahasa.
    """
    raw = (os.environ.get("COPY_LANGUAGE") or "").strip()
    if raw.lower() in _FOLLOW_TRANSCRIPT:
        detected = str(transcript_language or "").strip()
        if detected.lower() in _UNKNOWN:
            return DEFAULT_COPY_LANGUAGE
        return detected
    if not raw:
        return DEFAULT_COPY_LANGUAGE
    return raw


def language_label(code: str) -> str:
    """ "id" -> "Indonesian (id)". Kode yang tidak dikenal dibiarkan apa adanya."""
    code = str(code or "").strip() or DEFAULT_COPY_LANGUAGE
    name = _LANGUAGE_NAMES.get(code.lower())
    return f"{name} ({code})" if name else code
