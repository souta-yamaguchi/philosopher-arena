"""
哲学者アリーナ — ソクラテス / ニーチェ / カント / ウィトゲンシュタイン
- プロンプトキャッシュ (90% OFF)
- IP別レート制限 + 全体日次キャップ
- 合言葉ゲート
"""
import os
import json
import random
import time
import secrets
from pathlib import Path
from collections import defaultdict, deque
from threading import Lock
from functools import wraps
from flask import (Flask, render_template, request, Response,
                   stream_with_context, jsonify, session, redirect, url_for)
from anthropic import Anthropic
from dotenv import load_dotenv

BASE = Path(__file__).parent
# ローカル開発: ai_news_app/.env からキーを読む
load_dotenv(BASE.parent.parent / "ai_news_app" / ".env")
# 本番: 同ディレクトリの .env もあれば読む
load_dotenv(BASE / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# セッションを 30 日保持
app.permanent_session_lifetime = 60 * 60 * 24 * 30

PASSPHRASE = os.environ.get("PASSPHRASE", "オヨヨ")
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


@app.after_request
def cache_policy(response):
    # 静的ファイル（画像・CSS等）は1日キャッシュ、それ以外（HTML/JSON）はno-cache
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ── Philosopher definitions ───────────────────────────────────
PHILOSOPHERS = {
    "socrates": {
        "name": "ソクラテス", "greek": "Σωκράτης",
        "years": "紀元前470年頃 – 紀元前399年 / アテナイ",
        "quote": "吟味されざる生は、生きるに値しない",
        "avatar": "🗣", "portrait": "socrates.jpg", "color": "bronze",
    },
    "nietzsche": {
        "name": "ニーチェ", "greek": "Friedrich Nietzsche",
        "years": "1844 – 1900 / プロイセン",
        "quote": "神は死んだ。我々が神を殺したのだ",
        "avatar": "⚡", "portrait": "nietzsche.jpg", "color": "crimson",
    },
    "kant": {
        "name": "カント", "greek": "Immanuel Kant",
        "years": "1724 – 1804 / ケーニヒスベルク",
        "quote": "わが上なる星輝く空と、わが内なる道徳法則",
        "avatar": "⚖", "portrait": "kant.jpg", "color": "navy",
    },
    "wittgenstein": {
        "name": "ウィトゲンシュタイン", "greek": "Ludwig Wittgenstein",
        "years": "1889 – 1951 / ウィーン〜ケンブリッジ",
        "quote": "語りえぬものについては、沈黙せねばならぬ",
        "avatar": "🪜", "portrait": "wittgenstein.jpg", "color": "slate",
    },
}

LENGTH_RULE = """

---

## 【最重要・応答長さと形式の絶対ルール】

あなたは対話をしている。独白・講義ではない。
- 原則 3〜5 文で応答を終える
- どんなに語りたくても最大でも 7 文を超えるな
- 段落はひとつか、多くてふたつ
- 箇条書きや番号列挙は禁止
- **markdown 見出し（#, ##, ###）は絶対に使うな**
- **複数セクションへの構造化は禁止**（ただの一続きの文章として応答せよ）
- 水平線（---）で区切るな
- 長大な自伝的説明、体系の展開、引用の羅列は禁物
- 詳しく語りたければ、相手の次の問いを誘う形で余白を残せ

全哲学者共通、破ってはならぬルール。
"""


GROUP_RULE_TEMPLATE = """

---

## 【円卓会議ルール】

あなたは今、アリーナの円卓で {others} と共に討論している。
ユーザが投げたテーマに対し、他の哲学者も順に発言する。

### ❌ 絶対にやってはならぬこと
- **他の哲学者の立場を代弁するな**（例：「## ソクラテス」「## カント」のような節を書くのは禁止）
- **markdown 見出し（#, ##, ###）を一切使うな**
- **複数人の見解を並べたドキュメントを書くな**（君は司会者ではない）
- **水平線（---）で区切るな**
- 長い前置き・自己紹介・テーマの言い換え

### ✅ やるべきこと
- **あなたは {self_name} である。他の誰でもない。{self_name} の声でのみ応答せよ**
- 直前までの発言を必ず踏まえよ（前の発言に反論・同意・深掘りせよ）
- 同意するなら短く頷き、反論するなら名前を呼んで鋭く切り返せ（例：「ソクラテスよ、君の問いは〜」）
- 新しい視点・角度を一つ持ち込むこと
- 3〜5 文、**一続きの散文**で打ち切れ
- 既に出た論点の繰り返しは禁物
"""


def load_persona(name: str) -> str:
    return (BASE / "philosophers" / f"{name}.md").read_text(encoding="utf-8")


PERSONAS = {k: load_persona(k) + LENGTH_RULE for k in PHILOSOPHERS}


def build_group_messages(history: list, speaker_name: str) -> list:
    """
    group-chat 用: 履歴を 1 本の user メッセージへ畳み込む。
    【name】 のような書式を避け、Claude が模倣しにくいナラティブ形式で伝える。
    """
    parts = ["【円卓会議のこれまで】"]
    user_topic = None
    prior = []  # (name, content)
    for m in history:
        if m["role"] == "user":
            user_topic = m["content"]
        else:
            # content は "【name】text" の形
            c = m["content"]
            if c.startswith("【"):
                end = c.find("】")
                if end > 0:
                    prior.append((c[1:end], c[end+1:]))
                    continue
            prior.append(("不明", c))

    if user_topic:
        parts.append(f"ユーザの問い：「{user_topic}」")
    if prior:
        parts.append("")
        parts.append("これまでに発言した哲学者：")
        for pname, pcontent in prior:
            parts.append(f"◆ {pname} の発言：")
            parts.append(pcontent)
            parts.append("")

    parts.append("────────────────────────")
    parts.append(f"いま発言するのは、{speaker_name} である。")
    parts.append("")
    parts.append("【厳守事項】")
    parts.append(f"・{speaker_name} 一人の声でのみ応答せよ。他の哲学者の発言を書き起こすな。")
    parts.append("・「◆ 〇〇の発言」のようなラベルや、「【名前】」の表記を自分の応答に含めるな。")
    parts.append("・markdown 見出し（#, ##, ###）、水平線、箇条書きは一切使うな。")
    parts.append("・前置きや『尊敬すべき問い手よ』的な挨拶なしで、いきなり本題に入れ。")
    parts.append("・3〜5 文、一続きの散文で。自分の名前を冒頭で宣言するな。")
    return [{"role": "user", "content": "\n".join(parts)}]


def group_stop_sequences(speaker_key: str) -> list:
    """他哲学者の名前ラベルが出力に現れたら即停止するための stop sequences."""
    seqs = []
    for k, p in PHILOSOPHERS.items():
        if k == speaker_key:
            continue
        n = p["name"]
        seqs.append(f"【{n}】")
        seqs.append(f"◆ {n}")
        seqs.append(f"## {n}")
    return seqs


def group_system_prompt(speaker_key: str) -> str:
    """円卓会議用の system prompt。speaker 以外の 3 名の名前を埋め込む。"""
    self_name = PHILOSOPHERS[speaker_key]["name"]
    others = "、".join(
        PHILOSOPHERS[k]["name"] for k in PHILOSOPHERS if k != speaker_key
    )
    return PERSONAS[speaker_key] + GROUP_RULE_TEMPLATE.format(
        self_name=self_name, others=others
    )


# ── Rate limiting ─────────────────────────────────────────────
GLOBAL_DAILY_CAP = 100  # 全員合算 / 1日

_global_day: deque = deque()
_lock = Lock()


def _prune(dq: deque, window_sec: float):
    now = time.time()
    while dq and now - dq[0] > window_sec:
        dq.popleft()


def check_rate_limit(ip: str) -> tuple[bool, str]:
    now = time.time()
    with _lock:
        _prune(_global_day, 86400)
        if len(_global_day) >= GLOBAL_DAILY_CAP:
            return False, "本日のサイト全体の上限に達しました。また明日お試しください。"
        _global_day.append(now)
        return True, ""


def client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ── Auth gate ─────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("gate"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/gate", methods=["GET", "POST"])
def gate():
    error = None
    if request.method == "POST":
        pw = (request.form.get("passphrase") or "").strip()
        if pw == PASSPHRASE:
            session.permanent = True
            session["authed"] = True
            return redirect(url_for("index"))
        error = "合言葉が違います"
    return render_template("gate.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("gate"))


# ── Conversation state ────────────────────────────────────────
conversations: dict[str, dict[str, list[dict]]] = {}


@app.route("/")
@login_required
def index():
    return render_template("index.html", philosophers=PHILOSOPHERS)


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    ip = client_ip()
    ok, msg = check_rate_limit(ip)
    if not ok:
        return jsonify({"error": msg}), 429

    data = request.get_json()
    session_id = data.get("session_id", "default")
    philosopher = data.get("philosopher", "socrates")
    user_message = data["message"]

    if philosopher not in PERSONAS:
        return jsonify({"error": "unknown philosopher"}), 400

    sess = conversations.setdefault(session_id, {})
    history = sess.setdefault(philosopher, [])
    history.append({"role": "user", "content": user_message})

    system_prompt = PERSONAS[philosopher]

    def generate():
        full_text = ""
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        with client.messages.stream(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=system_blocks,
            messages=history,
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
        history.append({"role": "assistant", "content": full_text})
        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/group-chat", methods=["POST"])
@login_required
def group_chat():
    """円卓会議：4哲学者がランダム順に、前の発言を踏まえて議論する"""
    ip = client_ip()
    # 4 人分の API 呼び出しなので、事前に 4 回分の空きがあるか確認
    with _lock:
        _prune(_global_day, 86400)
        if len(_global_day) + len(PHILOSOPHERS) > GLOBAL_DAILY_CAP:
            return jsonify({
                "error": "本日のサイト全体の上限に達しました。また明日お試しください。"
            }), 429
        # 枠を 4 人分先に予約
        now = time.time()
        for _ in range(len(PHILOSOPHERS)):
            _global_day.append(now)

    data = request.get_json()
    session_id = data.get("session_id", "default")
    topic = data.get("message", "").strip()
    if not topic:
        return jsonify({"error": "topic required"}), 400

    sess = conversations.setdefault(session_id, {})
    history = sess.setdefault("_group", [])
    history.append({"role": "user", "content": topic})

    # ランダム順に発言者を決める
    order = list(PHILOSOPHERS.keys())
    random.shuffle(order)

    def generate():
        # 1) 発言順をクライアントに通知
        yield f"data: {json.dumps({'order': order}, ensure_ascii=False)}\n\n"

        # 2) 各哲学者が順に発言
        for key in order:
            name = PHILOSOPHERS[key]["name"]
            # 発言者切り替え通知
            yield f"data: {json.dumps({'start': key}, ensure_ascii=False)}\n\n"

            system_blocks = [
                {
                    "type": "text",
                    "text": group_system_prompt(key),
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            # 履歴を単一 user メッセージへ畳み込む → assistant 連続問題を回避
            api_messages = build_group_messages(history, name)
            stop_seqs = group_stop_sequences(key)
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=700,
                system=system_blocks,
                messages=api_messages,
                stop_sequences=stop_seqs,
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield f"data: {json.dumps({'philosopher': key, 'text': text}, ensure_ascii=False)}\n\n"

            # 履歴に話者名プレフィックス付きで追記 → 次の哲学者が参照できる
            history.append({
                "role": "assistant",
                "content": f"【{name}】{full_text}",
            })

        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 系プロキシのバッファリングを切る
            "Connection": "keep-alive",
        },
    )


@app.route("/reset", methods=["POST"])
@login_required
def reset():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    philosopher = data.get("philosopher")
    sess = conversations.setdefault(session_id, {})
    if philosopher:
        sess[philosopher] = []
    else:
        conversations[session_id] = {}
    return {"status": "ok"}


@app.route("/status")
def status():
    with _lock:
        _prune(_global_day, 86400)
        return {
            "global_today": len(_global_day),
            "global_cap": GLOBAL_DAILY_CAP,
            "remaining": GLOBAL_DAILY_CAP - len(_global_day),
        }


if __name__ == "__main__":
    app.run(debug=True, port=5001)
