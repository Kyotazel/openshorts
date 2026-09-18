"""Bahasa copy: default Indonesia, dan prompt benar-benar memakainya.

Konten Inggris harus menghasilkan caption Indonesia, dan begitu juga konten
berbahasa lain. Yang diuji di sini: pemilihan bahasanya (copy_language.py) dan
bahwa ketiga prompt benar-benar memakai bahasa itu sebagai bahasa KELUARAN,
sementara bahasa transkrip tetap dikirim sebagai konteks.
"""
import pytest

from copy_language import (DEFAULT_COPY_LANGUAGE, copy_language_for,
                           language_label)


class TestCopyLanguageFor:
    def test_tanpa_setelan_hasilnya_indonesia(self, monkeypatch):
        monkeypatch.delenv("COPY_LANGUAGE", raising=False)
        assert copy_language_for("en") == "id"
        assert copy_language_for("ja") == "id"

    def test_auto_ikut_bahasa_transkrip_lagi(self, monkeypatch):
        monkeypatch.setenv("COPY_LANGUAGE", "auto")
        assert copy_language_for("en") == "en"
        assert copy_language_for("es") == "es"

    def test_auto_tanpa_bahasa_transkrip_kembali_ke_default(self, monkeypatch):
        monkeypatch.setenv("COPY_LANGUAGE", "auto")
        assert copy_language_for(None) == DEFAULT_COPY_LANGUAGE
        assert copy_language_for("") == DEFAULT_COPY_LANGUAGE
        # Whisper menulis "unknown" kalau tidak yakin — tetap Indonesia.
        assert copy_language_for("unknown") == DEFAULT_COPY_LANGUAGE

    def test_kode_bahasa_lain_menang(self, monkeypatch):
        monkeypatch.setenv("COPY_LANGUAGE", "ms")
        assert copy_language_for("en") == "ms"

    def test_kosong_berarti_default(self, monkeypatch):
        monkeypatch.setenv("COPY_LANGUAGE", "   ")
        assert copy_language_for("en") == "id"

    def test_huruf_besar_kecil_tidak_masalah(self, monkeypatch):
        monkeypatch.setenv("COPY_LANGUAGE", "AUTO")
        assert copy_language_for("en") == "en"


class TestLanguageLabel:
    def test_kode_dikenal_ditulis_lengkap(self):
        assert language_label("id") == "Indonesian (id)"
        assert language_label("en") == "English (en)"

    def test_kode_tidak_dikenal_dibiarkan(self):
        assert language_label("xx") == "xx"

    def test_kosong_berarti_default(self):
        assert language_label("") == "Indonesian (id)"


class TestPrompts:
    def _detail(self, copy_language="Indonesian (id)"):
        gw = pytest.importorskip("gemini_worker")
        return gw.DETAIL_PROMPT_TEMPLATE.format(
            video_duration=100, language="en", copy_language=copy_language,
            min_clips=2, max_clips=4, min_secs=10.0, max_secs=20.0,
            windows_json="[]")

    def test_prompt_detail_menulis_copy_dalam_bahasa_copy(self):
        text = self._detail()
        assert "written in Indonesian (id)" in text
        assert "COPY_LANGUAGE: Indonesian (id)" in text
        # Bahasa transkrip tetap dikirim, tapi hanya sebagai konteks.
        assert "TRANSCRIPT_LANGUAGE: en" in text

    def test_tidak_ada_lagi_perintah_menulis_copy_dalam_bahasa_transkrip(self):
        text = self._detail()
        assert "written in TRANSCRIPT_LANGUAGE" not in text
        assert "hook in TRANSCRIPT_LANGUAGE" not in text

    def test_mode_auto_kembali_ke_bahasa_transkrip(self):
        text = self._detail("Spanish (es)")
        assert "written in Spanish (es)" in text

    def test_prompt_visual_ikut_bahasa_copy(self):
        gw = pytest.importorskip("gemini_worker")
        text = gw.VISUAL_PROMPT_TEMPLATE.format(
            video_duration=100, copy_language="Indonesian (id)",
            min_clips=2, max_clips=4, min_secs=10.0, max_secs=20.0)
        assert "catchy copy in Indonesian (id)" in text

    def test_prompt_hook_grounding_ikut_bahasa_copy(self):
        gw = pytest.importorskip("gemini_worker")
        text = gw.GROUNDED_HOOK_PROMPT.format(
            language="en", copy_language="Indonesian (id)",
            current_hook="a", current_title="b", transcript="c")
        # Keduanya boleh terpotong baris di template, jadi jangan ikut baris.
        assert "max 10 words, in Indonesian (id)" in text
        assert "Indonesian (id), no fake claims" in text
        assert "COPY_LANGUAGE: Indonesian (id)" in text
