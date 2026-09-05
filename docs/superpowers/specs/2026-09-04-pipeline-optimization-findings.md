# Temuan Optimasi Pipeline (catatan berjalan)

Tanggal: 2026-09-04
Konteks: transcribe sudah dipindah dari lokal ke OpenRouter sehingga jauh lebih cepat.
Kesepakatan: jalankan dulu untuk melihat tahap mana yang lama, baru log diperkaya.
File ini adalah catatan temuan, bukan spec implementasi.

## 1. Download perlu progress bar (MB/GB + persen)

Status: **selesai diimplementasi** (2026-09-04).
Lokasi UI: panel **System Logs** dashboard (seperti di screenshot user) — `dashboard/src/App.jsx:1855-1885`, diisi via polling `GET /api/status/{job_id}` tiap 2 detik (`App.jsx:697-730`).

Kondisi saat ini:
- Hook download ada di `main.py:843-852` (`_progress_hook`), tapi hanya menyimpan byte ke `_dl_bytes` internal untuk hitung proxy billing.
- User hanya melihat `📥 Download attempt: {label}` (`main.py:905`), `✅ Download succeeded` (`main.py:914`), dan `✅ Video downloaded in {s}s` (`main.py:966`).
- Selama download berjalan (bisa menit-menit, retry sampai 10x), tidak ada update sama sekali → kelihatan stuck.
- Yang justru membanjiri panel System Logs adalah log verbose mentah yt-dlp (`quiet: False, verbose: True` di `main.py:823`): baris `[info] Downloading 1 format(s)`, `[debug] Invoking http downloader on "https://rr...googlevideo.com/..."` (URL panjang bocor ke UI seperti di screenshot), dan `[download] Destination: output/...`.
- Jadi masalah ganda: info yang dibutuhkan (progress %) tidak ada, info yang tidak dibutuhkan (URL googlevideo, debug downloader) memenuhi log.

Kebutuhan:
- Di panel System Logs yang sama (posisi seperti screenshot), tampilkan baris progress user-visible yang di-update berkala: sudah berapa MB/GB dari total berapa, plus persen.
- Contoh: `📥 Downloading… 145/380 MB (38%) — 8.2 MB/s, ETA 29s`.
- Update berkala (throttle, mis. tiap 2 detik atau tiap 5%) agar tidak spam log yang di-poll frontend tiap 2 detik.
- Harus survive kasus total tidak diketahui (fragmented/HLS): fallback tampilkan `downloaded + speed` tanpa persen.
- Pastikan baris progress tidak dibuang filter `log_view.py:20-30` / `_visible_logs()` (`app.py:1763-1778`), atau kirim sebagai marker `PROGRESS` parseable terpisah.
- Pertimbangkan meredam log verbose yt-dlp (`verbose: True` → hanya warning/error + progress ringkas sendiri), supaya URL googlevideo panjang tidak bocor ke System Logs dan progress bar mudah dibaca.

Catatan teknis:
- `yt-dlp` progress hook sudah menyediakan `downloaded_bytes`, `total_bytes`, `total_bytes_estimate`, `speed`, `eta`, `fragment_index/count` — tinggal print, tidak perlu ubah strategi download/retry.
- Perlu throttle berdasarkan waktu, bukan tiap callback, karena hook dipanggil sangat sering.
- Perlu format human-readable MB/GB konsisten (MB < 1024, GB di atasnya).
- Opsi: `quiet: True` + `progress_hooks` sendiri, atau `quiet: False, verbose: False` + filter baris `[debug]`/`[info]` agar tidak sampai ke `jobs[logs]`.

## Update dari run user (screenshot System Logs)

Temuan baru: progress download **sudah ada datanya, tapi munculnya menumpuk di akhir**.
Bukti di screenshot: puluhan segmen `[download] 47.3% of 41.19MiB at 12.35MiB/s ETA 00:01 ... 100% of 41.19MiB`
tersambung dalam SATU entri log raksasa, baru terlihat setelah download selesai (tepat sebelum baris `[Merger]`).
Selama download berjalan, panel kelihatan diam di baris `[debug] Invoking http downloader...`.

Akar masalah (dugaan kuat, perlu verifikasi saat implementasi):
- yt-dlp menulis progress-nya dengan carriage return (`\r`, tulis-ulang baris yang sama),
  bukan newline (`\n`).
- Pembaca log backend `enqueue_output()` di `app.py:1781-1823` memakai `iter(out.readline, b'')`
  yang hanya bangun per `\n`. Jadi semua update `\r` menumpuk di buffer pipe dan baru terkirim
  sebagai satu baris raksasa saat ada `\n` (di akhir download).
- Akibat tambahan: frontend yang poll tiap 2 detik menerima satu payload log raksasa sekaligus,
  bukan update bertahap.

Arah perbaikan (belum diimplementasi):
- Jangan andalkan stdout `\r` milik yt-dlp untuk progress user-visible.
- Cetak progress dari `_progress_hook()` milik sendiri (`main.py:843-852`) yang jalan in-process:
  baris `📥 Downloading… X/Y MB (P%) — speed, ETA` dengan `\n` + `flush=True`, di-throttle
  (mis. tiap 2 detik atau tiap 5%).
- Redam output progress bawaan yt-dlp (`quiet` / non-verbose / template progress sendiri)
  supaya tidak ada lagi baris `[download] ...` mentah yang menumpuk, dan URL googlevideo
  panjang tidak bocor ke System Logs.

Implementasi (2026-09-04, TDD — RED→GREEN verified):
- Modul baru `download_progress.py`: `format_bytes/format_speed/format_eta/format_line`
  + class `DownloadProgress` (emit pertama langsung, throttle 5 dtk, reset saat ganti file
  atau byte mundur karena attempt baru). Contoh output:
  `📥 Downloading… 41.2/82.4 MB (50%) — 11.4 MB/s, ETA 00:03`.
- `main.py: _base_opts` → `quiet: True, verbose: False, no_warnings: True` supaya baris
  `[debug] Invoking http downloader...` + URL googlevideo + `[download] ...\r` tidak lagi
  membanjiri System Logs; progress realtime dicetak dari `_progress_hook` via reporter
  (newline + flush, lolos pipe `readline` di `app.py:1784`).
- `log_view.py`: baris `📥 Downloading…` lolos filter cloud (aturan baru). Sekalian perbaiki
  `_PATH_RE` yang tadinya ikut memakan `10.0/100.0` dan `MB/s` (dianggap path); sekarang
  hanya token path betulan (2+ slash atau ekstensi file) yang dipotong.
- Bonus fix dari error screenshot user: `source_trim.format_clock()` yang badannya
  unreachable (crash `AttributeError` setelah download 712 dtk) diperbaiki jadi fungsi
  betulan (`765 → 12:45`), jadi trim source start/end sekarang jalan.
- Test: `tests/test_download_progress.py` (baru, 11 test), `test_format_clock`
  di `test_source_trim.py`, `test_download_progress_survives_cloud_filter` di
  `test_log_view.py`. Suite area terkait hijau: 48 passed, 5 skipped.

Revisi peramping — satu baris per persen + kesimpulan (2026-09-04):
- Masalah: `quiet: True` saja ternyata TIDAK membungkam progress yt-dlp. Dari audit kode
  yt-dlp (`downloader/common.py:294-301`): `quiet` hanya memilih `MultilinePrinter`
  non-verbose, tapi baris `[download]/[Merger]` tetap dicetak (plain `\n` karena
  `preserve_output`). Itu yang menumpuk di screenshot user: puluhan segmen
  `[download] 4.8% ... 5.1% ...` tersambung dalam entri log raksasa.
- Perbaikan: `noprogress: True` di `_base_opts` → yt-dlp memakai `QuietMultilinePrinter`
  (benar-benar diam). Sabuk-pengaman kedua: `enqueue_output()` di `app.py` membuang
  baris berawalan `[download]/[Merger]/[debug]/[info]` supaya tidak ada noise lolos
  ke System Logs maupun ke filter cloud.
- Reporter diubah dari throttle-waktu menjadi gate-persen: persen bulat baru selalu
  emit (realtime walau pipe lambat), pengulangan persen yang sama tidak pernah emit.
  File 8 GB → ~100 baris pendek, bukan ribuan. Contoh:
  `📥 Downloading… 476.8 MB/8.0 GB (5%) — 12.0 MB/s, ETA 10:17`
- Baris penutup lama `✅ Video downloaded in {s}s: {path}` (bocor path lokal, format
  detik mentah) diganti `format_done()`: `✅ Download complete: 8.0 GB in 10:17`
  — total size + durasi `MM:SS`, tanpa path.
- Test: suite area terkait hijau 53 passed, 5 skipped (14 test di
  `test_download_progress.py` termasuk gate-persen, baris kesimpulan, dan pin
  perilaku buang-noise).

## 2. Timestamp System Logs harus waktu kejadian, bukan waktu repaint (opsi B)

Status: **selesai diimplementasi** (2026-09-04).
Lokasi: panel System Logs — `dashboard/src/App.jsx:1893-1904`, endpoint
`GET /api/status/{job_id}` (`app.py`).

Masalah (dari screenshot user): semua baris menampilkan jam yang sama dan selalu
bergeser ke waktu terbaru (`11:06:40 AM` di semua baris). Penyebab: frontend
me-render `new Date().toLocaleTimeString()` di dalam `logs.map()` — jam repaint
browser, bukan jam kejadian. Backend memang tidak menyimpan waktu (array string
polos), jadi tidak ada data benar yang bisa ditampilkan.

Implementasi (TDD — RED 7 gagal → GREEN):
- Penyimpanan: entri log kini objek `{"ts": epoch, "msg": "..."}` via satu helper
  `_append_log(job_id, msg)` (waktu server saat baris masuk). Semua `.append()`
  langsung di `enqueue_output`/`run_job` diganti ke helper ini.
- Kompatibilitas dua lapis (tidak ada breaking change):
  - `_log_messages()` menormalkan objek→`.msg` / string→apa adanya untuk semua
    konsumen string lama (`_visible_logs`/cloud filter, `_job_error_text`/alert).
  - `GET /api/status` tetap kirim `logs` array string persis seperti dulu, plus
    field sejajar baru `logs_v2: [{ts, msg}]`. Tab lama yang terbuka tetap jalan.
  - Entri legacy dari disk (string) → `ts: null`, dirender tanpa jam (bukan jam palsu).
  - Cloud view: `logs_v2` difilter whitelist + collapse duplikat sama seperti
    `friendly_logs`, tiap baris lolos membawa `ts` aslinya.
  - `saas_jobs` tidak disentuh (jalur terpisah, bisa menyusul).
- Frontend: state sejajar `logTs` + helper `applyLogs()` (pakai `logs_v2` bila ada,
  fallback `logs` + null). Render jam dari `ts` server; baris tanpa `ts` tanpa jam.
  Setter lokal (starting/error/restore/reset) ikut mengisi `logTs`.
- Verifikasi: `tests/test_log_timestamps.py` (baru, 7 test); suite area terdampak
  hijau 107 passed (`venv`: timestamps + download + trim + log_view + deploy_drain
  + handover); frontend `vite build` lolos 3.17s.

## 3. Progress trim + heartbeat LLM (gap 1 & 2 dari run user)

Status: **selesai diimplementasi** (2026-09-04). Kecepatan tidak diubah —
hanya yang sunyi dibuat bersuara, sesuai permintaan.

Gap 1 — trim sunyi 168 dtk (`trim_source`, `source_trim.py`):
- Modul baru `trim_progress.py`: parse `out_time_ms` dari ffmpeg `-progress pipe`,
  satu baris per persen (`✂️ Trimming… 150/300s (50%)`), baris penutup 100%
  dipaksa saat `progress=end`. `trim_source()` kini menjalankan ffmpeg via Popen
  dengan `-progress pipe:1` + reporter (custom `run_ffmpeg` tetap didukung untuk test).
  Encode-nya identik (tier/preset tidak diubah) — yang berubah hanya visibility.
- Baris lolos filter cloud (aturan baru di `log_view.py`).
- Uji live video 6 dtk: baris persen keluar realtime, trim selesai normal.

Gap 2 — LLM detail sunyi 106 dtk (`_run_gemini_stage`, `main.py`):
- Setiap attempt kini membuka dengan `🤖 Detail pass (attempt 2/3)…` (label
  score/detail diteruskan dari `_run_stage_split` via param baru `label`).
- Heartbeat thread daemon tiap 20 dtk selama blocking API call:
  `🤖 Detail pass still waiting for the model… (40s)`; berhenti + join saat
  attempt selesai (sukses/gagal/retry). Timeout, retry, dan model tidak diubah.
- Baris retry yang sudah ada diberi `flush=True` + diteruskan label attempt,
  jadi setelah backoff user melihat attempt baru dimulai, bukan hening.
- Baris attempt/heartbeat lolos filter cloud (aturan baru).

Verifikasi (TDD):
- `tests/test_trim_progress.py` (7 test) + `tests/test_llm_progress.py` (4 test),
  RED→GREEN. Suite area terdampak hijau 121 passed (termasuk block_split yang
  fake-nya diselaraskan ke signature baru).
- Catatan: 7 test di `test_gemini_retry.py`/`test_llm_backend.py` gagal, tapi
  terkonfirmasi pre-existing (gagal identik di tree bersih via `git stash` —
  fake schema `object`, bukan regresi perubahan ini).

## 4. (kosong — diisi dari hasil pengukuran berikutnya)
