"""
哲学者アリーナ — ソクラテス / ニーチェ / カント / ウィトゲンシュタイン
- プロンプトキャッシュ (90% OFF)
- IP別レート制限 + 全体日次キャップ
- 合言葉ゲート
"""
import os
import json
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
def no_cache(response):
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

## 【最重要・応答長さの絶対ルール】

あなたは対話をしている。独白・講義ではない。
- 原則 3〜5 文で応答を終える
- どんなに語りたくても最大でも 7 文を超えるな
- 段落はひとつか、多くてふたつ
- 箇条書きや番号列挙は禁止
- 長大な自伝的説明、体系の展開、引用の羅列は禁物
- 詳しく語りたければ、相手の次の問いを誘う形で余白を残せ

全哲学者共通、破ってはならぬルール。
"""


def load_persona(name: str) -> str:
    return (BASE / "philosophers" / f"{name}.md").read_text(encoding="utf-8")


PERSONAS = {k: load_persona(k) + LENGTH_RULE for k in PHILOSOPHERS}


# ── Rate limiting ─────────────────────────────────────────────
IP_LIMIT_PER_MIN = 6
IP_LIMIT_PER_DAY = 40
GLOBAL_DAILY_CAP = 500

_ip_minute: dict[str, deque] = defaultdict(deque)
_ip_day:    dict[str, deque] = defaultdict(deque)
_global_day: deque = deque()
_lock = Lock()


def _prune(dq: deque, window_sec: float):
    now = time.time()
    while dq and now - dq[0] > window_sec:
        dq.popleft()


def check_rate_limit(ip: str) -> tuple[bool, str]:
    now = time.time()
    with _lock:
        _prune(_ip_minute[ip], 60)
        _prune(_ip_day[ip], 86400)
        _prune(_global_day, 86400)
        if len(_global_day) >= GLOBAL_DAILY_CAP:
            return False, "本日のサイト全体の上限に達しました。また明日お試しください。"
        if len(_ip_minute[ip]) >= IP_LIMIT_PER_MIN:
            return False, "短時間にリクエストが多すぎます。1分ほどお待ちください。"
        if len(_ip_day[ip]) >= IP_LIMIT_PER_DAY:
            return False, "本日の利用上限に達しました。明日また問いを持ってきてください。"
        _ip_minute[ip].append(now)
        _ip_day[ip].append(now)
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
