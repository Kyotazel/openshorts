#!/usr/bin/env bash
#
# Buka route `/output/` di nginx untuk clip1.ordoagentic.ai
#   -> listing + download klip hasil job dari browser mana pun (termasuk incognito),
#      tanpa login, langsung dari /var/www/html/openshorts/output/
#
# Cara pakai di server (jalankan dari folder repo):
#   bash deploy/nginx-output-route.sh
#
# Idempotent: aman dijalankan ulang. Kalau nginx -t gagal, config di-restore otomatis.
set -euo pipefail

SITE=/etc/nginx/sites-available/clip1
OUTPUT_DIR=/var/www/html/openshorts/output
BACKUP=${SITE}.bak

echo "== 1/5 Backup config =="
if [ -f "$BACKUP" ]; then
    echo "   backup sudah ada ($BACKUP), skip"
else
    sudo cp "$SITE" "$BACKUP"
    echo "   backup -> $BACKUP"
fi

echo "== 2/5 Cek folder output =="
[ -d "$OUTPUT_DIR" ] || { echo "   ERROR: $OUTPUT_DIR tidak ada — mkdir -p dulu"; exit 1; }
echo "   ok: $OUTPUT_DIR"

echo "== 3/5 Insert location block =="
sudo python3 - "$SITE" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
block = """
    # Raw output: lihat/download klip dari browser mana pun (tanpa login)
    location /output/ {
        alias /var/www/html/openshorts/output/;
        autoindex on;
    }
"""
if 'location /output/' in s:
    print('   sudah ada, skip')
    sys.exit(0)
idx = s.find('location / {')
if idx == -1:
    print('   ERROR: marker "location / {" tidak ketemu — insert manual via nano')
    sys.exit(2)
open(p, 'w').write(s[:idx] + block + s[idx:])
print('   inserted OK (sebelum "location / {")')
PYEOF

echo "== 4/5 nginx -t =="
if ! sudo nginx -t; then
    echo "   ERROR: nginx -t gagal — restore backup"
    sudo cp "$BACKUP" "$SITE"
    exit 1
fi

echo "== 5/5 reload + verify =="
sudo systemctl reload nginx
echo "   Selesai. Contoh hasil:"
echo "   https://clip1.ordoagentic.ai/output/<job_id>/"
echo "   (listing job & semua klip; klik langsung download/play)"
