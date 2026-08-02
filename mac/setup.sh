#!/bin/bash
# 哲学者アリーナ Mac 初回セットアップ
set -e
cd "$(dirname "$0")/.."

echo "── 1/4 claude CLI 確認 ──"
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI が見つかりません。先にインストール & ログインしてください。"
  echo "  curl -fsSL https://claude.ai/install.sh | bash"
  exit 1
fi
claude --version

echo "── 2/4 Python venv 構築 ──"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "── 3/4 .env 生成 ──"
if [ ! -f .env ]; then
  cat > .env <<EOF
PASSPHRASE=オヨヨ
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
EOF
  echo ".env を生成しました（PASSPHRASE: オヨヨ）"
else
  echo ".env は既存のためスキップ"
fi

echo "── 4/4 cloudflared 確認 ──"
if ! command -v cloudflared >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared
  else
    echo "ERROR: Homebrew がありません。手動で cloudflared をインストールしてください。"
    echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
fi

echo ""
echo "セットアップ完了。起動: bash mac/run.sh"
