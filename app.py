"""
哲学者アリーナ — ソクラテス / ニーチェ / カント / ウィトゲンシュタイン
- 応答生成はローカルの Claude Code CLI (claude -p) — API 課金なし・サブスク枠
- IP別レート制限 + 全体日次キャップ
- 合言葉ゲート
"""
import os
import json
import random
import shutil
import subprocess
import time
import secrets
from pathlib import Path
from collections import defaultdict, deque
from threading import Lock
from functools import wraps
from flask import (Flask, render_template, request, Response,
                   stream_with_context, jsonify, session, redirect, url_for)
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# セッションを 30 日保持
app.permanent_session_lifetime = 60 * 60 * 24 * 30

PASSPHRASE = os.environ.get("PASSPHRASE", "オヨヨ")

# ── Claude Code CLI ───────────────────────────────────────────
CLAUDE_BIN = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
if not CLAUDE_BIN:
    raise RuntimeError("claude CLI が見つかりません。PATH を確認するか CLAUDE_BIN を設定してください。")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "180"))
# CLAUDE.md やフックを拾わないよう、専用の空ディレクトリを cwd にして実行する
CLAUDE_CWD = BASE / "runtime"
CLAUDE_CWD.mkdir(exist_ok=True)


def _claude_cmd(persona_file: Path) -> list[str]:
    """persona を system prompt に、ツール・ユーザー設定(CLAUDE.md/フック)を全て無効化。
    複数行のプロンプトを argv に載せると Windows の .CMD シムで壊れるためファイル渡し。"""
    return [
        CLAUDE_BIN, "-p",
        "--system-prompt-file", str(persona_file),
        "--model", CLAUDE_MODEL,
        "--tools", "",
        "--setting-sources", "",
    ]


def _build_prompt(history: list[dict]) -> str:
    """メモリ上の対話履歴を 1 本のプロンプトに畳む（claude -p は毎回ステートレス）。"""
    if len(history) == 1:
        return history[0]["content"]
    lines = ["# これまでの対話（あなたと相手のやり取り）", ""]
    for m in history[:-1]:
        speaker = "相手" if m["role"] == "user" else "あなた"
        lines.append(f"【{speaker}】{m['content']}")
    lines += ["", "# 相手の新しい発言（これにあなたとして応答せよ）", history[-1]["content"]]
    return "\n".join(lines)


def _stream_claude(persona_file: Path, prompt: str):
    """claude -p の stream-json 出力からテキスト差分を逐次 yield する。"""
    cmd = _claude_cmd(persona_file) + [
        "--output-format", "stream-json", "--include-partial-messages", "--verbose",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(CLAUDE_CWD), text=True, encoding="utf-8", errors="replace",
    )
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "stream_event":
                ev = obj.get("event", {})
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
            elif obj.get("type") == "result" and obj.get("is_error"):
                raise RuntimeError(obj.get("result") or "claude CLI error")
        proc.wait(timeout=CLAUDE_TIMEOUT)
    finally:
        if proc.poll() is None:
            proc.kill()


def _run_claude(persona_file: Path, prompt: str) -> str:
    """claude -p を一括実行して最終テキストを返す（円卓会議用）。"""
    cmd = _claude_cmd(persona_file) + ["--output-format", "json"]
    res = subprocess.run(
        cmd, input=prompt, capture_output=True,
        cwd=str(CLAUDE_CWD), text=True, encoding="utf-8", errors="replace",
        timeout=CLAUDE_TIMEOUT,
    )
    data = json.loads(res.stdout)
    if data.get("is_error"):
        raise RuntimeError(data.get("result") or "claude CLI error")
    return data.get("result", "")


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

**他の賢者（ソクラテス・ニーチェ・カント・ウィトゲンシュタインのうち、あなた以外の誰か）の代弁・なりきりを絶対にしてはならない。**
**他の賢者の名前を自分の発言の中で使ってはならない。**
**複数の立場を並列して書いてはならない。** あなたはあなた自身としての返答だけを出力する。

### 絶対にやってはならない例（重要）
- NG: あなたがカントなのに「私はソクラテス。アテナイの石工の子だ」と名乗る
- NG: 「各々自己を明らかにせよ」「では君から」と司会進行する
- NG: 「# ソクラテス」等の見出しで別人物の発言を書く
- NG: 「ソクラテスならこう言うだろう、ニーチェなら…」と他人の意見を要約する
- **OK: 自分自身としてだけ、短く、自分の思想の核心を述べる**

### 形式の絶対禁止事項
- `#` `##` `###` などの markdown 見出しを **絶対に書くな**
- 水平線 `---` で区切るな
- 段落を複数に分けるな（1 パラグラフで収めよ）
- 箇条書き・番号列挙を使うな
- 司会進行の台詞（「歓迎する」「各々名乗れ」等）は禁止

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

# なりきり検出パターン（他哲学者になり切る典型的な表現）
_IMPERSONATION_PATTERNS = {
    "socrates":     [r"私はソクラテス", r"我はソクラテス", r"わたくしはソクラテス", r"吾はソクラテス"],
    "nietzsche":    [r"私はニーチェ", r"我はニーチェ", r"わたくしはニーチェ", r"吾はニーチェ"],
    "kant":         [r"私はカント", r"我はカント", r"わたくしはカント", r"吾はカント"],
    "wittgenstein": [r"私はウィトゲンシュタイン", r"我はウィトゲンシュタイン",
                     r"わたくしはウィトゲンシュタイン", r"吾はウィトゲンシュタイン"],
}


def load_persona(name: str) -> str:
    return (BASE / "philosophers" / f"{name}.md").read_text(encoding="utf-8")


_RAW_PERSONAS = {k: load_persona(k) for k in PHILOSOPHERS}
PERSONAS = {k: v + LENGTH_RULE for k, v in _RAW_PERSONAS.items()}
# 円卓会議モード: ルールを前と後ろの両方に置いて効かせる
GROUP_PERSONAS = {k: GROUP_RULE + "\n\n" + v + GROUP_RULE for k, v in _RAW_PERSONAS.items()}

# claude -p に --system-prompt-file で渡すため、起動時にファイルへ書き出す
_PERSONA_DIR = CLAUDE_CWD / "personas"
_PERSONA_DIR.mkdir(exist_ok=True)
PERSONA_FILES: dict[str, Path] = {}
GROUP_PERSONA_FILES: dict[str, Path] = {}
for _k in PHILOSOPHERS:
    _pf = _PERSONA_DIR / f"{_k}.txt"
    _pf.write_text(PERSONAS[_k], encoding="utf-8")
    PERSONA_FILES[_k] = _pf
    _gf = _PERSONA_DIR / f"{_k}_group.txt"
    _gf.write_text(GROUP_PERSONAS[_k], encoding="utf-8")
    GROUP_PERSONA_FILES[_k] = _gf


def _sanitize_group_reply(text: str, speaker_key: str = "") -> str:
    """円卓会議の応答から汚染（他哲学者の見出し・なりきり・markdown）を除去。"""
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
        if idx > 0:
            text = text[:idx]
    # 4) 他哲学者への「なりきり」（私はソクラテス 等）を検出して切り捨て
    if speaker_key:
        for other_key, patterns in _IMPERSONATION_PATTERNS.items():
            if other_key == speaker_key:
                continue
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    text = text[:m.start()]
    # 5) 水平線以降を切り捨て
    for sep in ("\n---", "\n\n---"):
        if sep in text:
            text = text.split(sep)[0]
    # 6) 残った行頭の markdown 見出しを除去
    text = re.sub(r"^\s*#{1,6}[^\n]*\n?", "", text, flags=re.MULTILINE)
    # 7) 改行を全て単一スペースに畳む（1 パラグラフで表示）
    text = re.sub(r"\s*\n+\s*", " ", text)
    # 8) 連続スペースを 1 つに
    text = re.sub(r"[ \t]{2,}", " ", text)
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

    persona_file = PERSONA_FILES[philosopher]

    def generate():
        full_text = ""
        try:
            for text in _stream_claude(persona_file, _build_prompt(history)):
                full_text += text
                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
        except Exception:
            yield f"data: {json.dumps({'error': '賢者との交信が途絶えた。時をおいて再び問いかけよ。'}, ensure_ascii=False)}\n\n"
            history.pop()  # 失敗したユーザ発言は履歴から取り除く
            return
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

            try:
                raw = _run_claude(GROUP_PERSONA_FILES[key], _build_prompt(history))
            except Exception:
                raw = ""
            cleaned = _sanitize_group_reply(raw, speaker_key=key)
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
