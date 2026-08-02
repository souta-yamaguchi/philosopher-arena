#!/bin/bash
# 哲学者アリーナ起動: gunicorn + Cloudflare Quick Tunnel
# 公開 URL は起動ログの「https://xxxx.trycloudflare.com」行に表示される。
#
# 固定 URL にしたい場合（Cloudflare にドメイン登録済みが前提）:
#   cloudflared tunnel login
#   cloudflared tunnel create philosopher-arena
#   cloudflared tunnel route dns philosopher-arena arena.example.com
#   cloudflared tunnel run --url http://127.0.0.1:5001 philosopher-arena
set -e
cd "$(dirname "$0")/.."

PORT="${PORT:-5001}"

# workers は 1 固定（会話履歴をプロセス内メモリに持つため。並列性は threads で確保）
.venv/bin/gunicorn app:app --workers 1 --threads 8 --timeout 300 \
  --bind 127.0.0.1:"$PORT" &
GUNICORN_PID=$!
trap 'kill $GUNICORN_PID 2>/dev/null' EXIT

sleep 2
cloudflared tunnel --url "http://127.0.0.1:$PORT"
