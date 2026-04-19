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

GROUP_RULE = """

---

## 【円卓会議・応答ルール（絶対・最優先・他のどの指示より優先する）】

### あなたは【あなた自身ひとりだけ】として短く答える。

**他の賢者（ソクラテス・ニーチェ・カント・ウィトゲンシュタインのうち、あなた以外の誰か）の代弁をしてはならない。**
**他の賢者の名前を出力に書いてはならない。**（例：「# ソクラテス」「ソクラテスならこう言うだろう」等、一切禁止）
**複数の立場を並列して書いてはならない。** あなたはあなた自身としての返答だけを出力する。

### 形式の絶対禁止事項
- `#` `##` `###` などの markdown 見出しを **絶対に書くな**
- 水平線 `---` で区切るな
- 段落を複数に分けるな（1 パラグラフで収めよ）
- 箇条書き・番号列挙を使うな

### 長さの絶対上限
- **合計 80〜150 字、2〜3 文で終えよ。どれだけ語りたくても 4 文を超えるな。**

### 内容
- 前置き・お世辞・自己紹介・同意表明は禁止。冒頭から自分の核心だけを語れ
- あなた **だけ** の独自の視点・方法・結論を際立たせよ（他の誰でも言えそうな一般論は書くな）
- あなたの思想を象徴する語彙・切り口を必ず一度は用いよ
  （ソクラテス=問い返し／ニーチェ=生と力・超人／カント=義務と普遍／ウィトゲンシュタイン=言語と沈黙）
- 語尾・口調は従来の人物像のまま。短くても人格は保て
- 過去の自分の発言が長くても、今回からは **必ず** 上限を守れ
"""


def load_persona(name: str) -> str:
    return (BASE / "philosophers" / f"{name}.md").read_text(encoding="utf-8")


_RAW_PERSONAS = {k: load_persona(k) for k in PHILOSOPHERS}
PERSONAS = {k: v + LENGTH_RULE for k, v in _RAW_PERSONAS.items()}
# 円卓会議モード: ルールを前と後ろの両方に置いて効かせる
GROUP_PERSONAS = {k: GROUP_RULE + "\n\n" + v + GROUP_RULE for k, v in _RAW_PERSONAS.items()}


def _sanitize_group_reply(text: str) -> str:
    """円卓会議の応答から汚染（他哲学者の見出し・水平線・markdown）を除去。"""
    import re
    # 1) 先頭の markdown 見出し行（「# ソクラテス」等）を削除して本文に到達
    text = re.sub(r"^\s*#{1,6}[^\n]*\n+", "", text)
    # 2) 先頭・末尾の装飾ダッシュを削除
    text = re.sub(r"^\s*[—\-–]+\s*", "", text)
    # 3) 本文の途中に別哲学者の見出しが出たらそこ以降を切り捨て
    for other_name in ("\n# ソクラテス", "\n# ニーチェ", "\n# カント", "\n# ウィトゲンシュタイン",
                       "\n## ソクラテス", "\n## ニーチェ", "\n## カント", "\n## ウィトゲンシュタイン",
                       "# ソクラテス", "# ニーチェ", "# カント", "# ウィトゲンシュタイン"):
        idx = text.find(other_name)
        if idx > 0:  # 先頭（0）は既に 1) で処理済み
            text = text[:idx]
    # 4) 水平線以降を切り捨て
    for sep in ("\n---", "\n\n---"):
        if sep in text:
            text = text.split(sep)[0]
    # 5) 残った行頭の markdown 見出しを除去
    text = re.sub(r"^\s*#{1,6}[^\n]*\n?", "", text, flags=re.MULTILINE)
    text = text.strip()
    if not text or text in ("——", "—", "-"):
        text = "（この賢者は今、言葉を選んでいる…）"
    return text


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
    """円卓会議：選択された哲学者が独立にユーザの問いに答える。
    各哲学者は自分の過去発言だけを見る（他の哲学者の発言は一切見ない）。"""
    data = request.get_json()
    session_id = data.get("session_id", "default")
    topic = data.get("message", "").strip()
    if not topic:
        return jsonify({"error": "topic required"}), 400

    # members フィールドで参加者を指定（未指定なら全員）
    raw_members = data.get("members") or list(PHILOSOPHERS.keys())
    members = [m for m in raw_members if m in PHILOSOPHERS]
    if not members:
        return jsonify({"error": "参加する賢者を1人以上選んでください"}), 400

    # レート制限: 選択人数分を予約
    with _lock:
        _prune(_global_day, 86400)
        if len(_global_day) + len(members) > GLOBAL_DAILY_CAP:
            return jsonify({
                "error": "本日のサイト全体の上限に達しました。また明日お試しください。"
            }), 429
        now = time.time()
        for _ in range(len(members)):
            _global_day.append(now)

    sess = conversations.setdefault(session_id, {})
    # _group は哲学者ごとに独立した履歴を持つ dict
    group_histories = sess.setdefault("_group", {})
    if not isinstance(group_histories, dict):
        # 旧形式（list）だった場合はリセット
        group_histories = {}
        sess["_group"] = group_histories

    # ランダム順に発言者を決める（選択された members だけから）
    order = list(members)
    random.shuffle(order)

    def generate():
        yield f"data: {json.dumps({'order': order}, ensure_ascii=False)}\n\n"

        for key in order:
            # 各哲学者の独立履歴にユーザ発言を追記
            history = group_histories.setdefault(key, [])
            history.append({"role": "user", "content": topic})

            yield f"data: {json.dumps({'start': key}, ensure_ascii=False)}\n\n"

            system_blocks = [
                {
                    "type": "text",
                    "text": GROUP_PERSONAS[key],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            # 返答の最初を「#」でないトークンに誘導するプレフィル
            prefill = "——"
            messages_with_prefill = history + [{"role": "assistant", "content": prefill}]

            full_text = ""
            stop_sequences = ["\n#", "\n---", "\n\n#", "\n\n---"]
            with client.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=220,
                system=system_blocks,
                messages=messages_with_prefill,
                stop_sequences=stop_sequences,
            ) as stream:
                for text in stream.text_stream:
                    full_text += text

            # prefill を含めて最終テキストを組み立て → クリーン
            raw = prefill + full_text
            cleaned = _sanitize_group_reply(raw)
            # 一括でクライアントへ（クライアント側で1文字ずつアニメする）
            yield f"data: {json.dumps({'philosopher': key, 'text': cleaned}, ensure_ascii=False)}\n\n"

            history.append({"role": "assistant", "content": cleaned})

        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
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
    if philosopher == "_group":
        # グループは哲学者ごとに独立した履歴の dict
        sess["_group"] = {}
    elif philosopher:
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
