# 哲学者アリーナ

古の賢者（ソクラテス / ニーチェ / カント / ウィトゲンシュタイン）と対話する Web アプリ。

## 機能
- 4 哲学者をその場で切り替え
- チャット履歴は哲学者別に保持
- ストリーミング応答（タイプライター風フェードイン）
- 合言葉ゲート
- プロンプトキャッシュ（90% OFF）
- IP 別レート制限 + 全体日次キャップ

## ローカル起動
```bash
python app.py
# http://127.0.0.1:5001
```

## 環境変数
| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `PASSPHRASE` | 合言葉（デフォルト: `オヨヨ`）|
| `SECRET_KEY` | Flask セッション用（未設定なら毎回ランダム生成）|

## Render デプロイ
1. GitHub にリポジトリ作成 → push
2. Render で New Web Service → GitHub リポジトリ選択
3. Environment に上記 3 つを登録
4. Build Command: `pip install -r requirements.txt`
5. Start Command: Procfile にて自動

## ファイル構成
```
philosopher_arena/
├── app.py                        メインアプリ
├── Procfile                      gunicorn 起動定義
├── requirements.txt
├── .env.example
├── philosophers/                 各哲学者の人格ファイル
│   ├── socrates.md
│   ├── nietzsche.md
│   ├── kant.md
│   └── wittgenstein.md
└── templates/
    ├── gate.html                 合言葉ゲート
    └── index.html                対話画面
```
