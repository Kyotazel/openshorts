"""yt-dlp format selector: highest source (VP9 ok, AV1 skipped), optional cap.

Paid per-GB attempts stay at 720p regardless of DOWNLOAD_SOURCE_HEIGHT.
"""
import download_format as df


class TestYoutubeDownloadFormat:
    def test_default_is_uncapped_merge_skipping_av1(self):
        got = df.youtube_download_format()
        assert got == "bestvideo[vcodec!*=av01]+bestaudio/best[vcodec!*=av01]"
        assert "height<=" not in got
        assert "avc1" not in got

    def test_optional_1080_cap_still_allows_vp9(self):
        got = df.youtube_download_format(max_height=1080)
        assert "height<=?1080" in got
        assert "avc1" not in got
        assert "vcodec!*=av01" in got
        assert "+bestaudio" in got

    def test_paid_proxy_keeps_720_cost_cap(self):
        got = df.youtube_download_format(capped=True, max_height="max")
        assert "height<=?720" in got
        assert "height<=?1080" not in got
        assert "avc1" not in got

    def test_paid_cap_wins_over_requested_height(self):
        got = df.youtube_download_format(capped=True, max_height=1080)
        assert "height<=?720" in got
        assert "1080" not in got


class TestDownloadSourceHeight:
    def test_unset_and_blank_mean_max(self, monkeypatch):
        monkeypatch.delenv("DOWNLOAD_SOURCE_HEIGHT", raising=False)
        assert df.download_source_height() == "max"
        monkeypatch.setenv("DOWNLOAD_SOURCE_HEIGHT", "")
        assert df.download_source_height() == "max"
        monkeypatch.setenv("DOWNLOAD_SOURCE_HEIGHT", "max")
        assert df.download_source_height() == "max"

    def test_numeric_height(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_SOURCE_HEIGHT", "1080")
        assert df.download_source_height() == 1080

    def test_garbage_falls_back_to_max(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_SOURCE_HEIGHT", "uhd")
        assert df.download_source_height() == "max"


class TestScrubNodeIpcEnv:
    def test_drops_pm2_ipc_keys_from_a_copy(self):
        env = {"NODE_CHANNEL_FD": "3", "NODE_CHANNEL_SERIALIZATION_MODE": "json",
               "PATH": "/usr/bin"}
        df.scrub_node_ipc_env(env)
        assert "NODE_CHANNEL_FD" not in env
        assert "NODE_CHANNEL_SERIALIZATION_MODE" not in env
        assert env["PATH"] == "/usr/bin"
