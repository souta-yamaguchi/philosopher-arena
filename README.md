# 哲学者アリーナ

古の賢者（ソクラテス / ニーチェ / カント / ウィトゲンシュタイン）と対話する Web アプリ。

## 機能
- 4 哲学者をその場で切り替え
- 円卓会議（参加メンバー選択可、各賢者は独立応答）
- チャット履歴は哲学者別に保持
- ストリーミング応答（タイプライター風フェードイン）
- 合言葉ゲート
- IP 別レート制限 + 全体日次キャップ

## 応答生成の仕組み（v2: API → Claude Code CLI）
Anthropic API は使わない。ホスト機にインストール済みの Claude Code CLI（`claude -p`）を
サブプロセスで呼び、サブスクリプション枠で応答を生成する（API 従量課金ゼロ）。
- ペルソナは `--system-prompt` で注入
- `--tools ""` + `--setting-sources ""` + 専用 cwd（`runtime/`）で、ツール・CLAUDE.md・フックを完全遮断
- 通常チャット: `--output-format stream-json` でストリーミング
- 円卓会議: `--output-format json` で一括取得 → サニタイズ

## 必要環境
- Claude Code CLI（インストール & ログイン済み）
- Python 3.10+

## ローカル起動
```bash
pip install -r requirements.txt
python app.py
# http://127.0.0.1:5001
```

## 環境変数（.env）
| 変数 | 用途 |
|---|---|
| `PASSPHRASE` | 合言葉（デフォルト: `オヨヨ`）|
| `SECRET_KEY` | Flask セッション用（未設定なら毎回ランダム生成）|
| `CLAUDE_BIN` | claude CLI のパス（PATH にあれば不要）|
| `CLAUDE_MODEL` | 使用モデル（デフォルト: `sonnet`）|
| `CLAUDE_TIMEOUT` | 1 応答のタイムアウト秒（デフォルト: 180）|

## Mac 本番運用（Cloudflare Tunnel 公開）
```bash
git clone https://github.com/souta-yamaguchi/philosopher-arena.git
cd philosopher-arena
bash mac/setup.sh   # venv 構築 + .env 生成 + cloudflared インストール
bash mac/run.sh     # gunicorn + Quick Tunnel 起動（公開 URL がログに表示される）
```
- Quick Tunnel は起動のたびに URL が変わる。固定 URL にするには
  Cloudflare にドメインを登録して Named Tunnel を使う（mac/run.sh のコメント参照）
- Mac のスリープ中はサイトも停止する（システム設定 → エネルギーでスリープ無効推奨）

## ファイル構成
```
philosopher_arena/
├── app.py                        メインアプリ（claude -p 呼び出し含む）
├── requirements.txt
├── .env.example
├── runtime/                      claude 実行用の空 cwd（設定汚染防止・git 管理外）
├── mac/
│   ├── setup.sh                  Mac 初回セットアップ
│   └── run.sh                    gunicorn + cloudflared 起動
├── philosophers/                 各哲学者の人格ファイル
│   ├── socrates.md
│   ├── nietzsche.md
│   ├── kant.md
│   └── wittgenstein.md
└── templates/
    ├── gate.html                 合言葉ゲート
    └── index.html                対話画面
```
