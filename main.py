import os
import json
import re
from pathlib import Path
from typing import Optional, Literal, Dict, Any, List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo 最省事
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== Paths ======
BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR / "frontend"  # main.py 在根目录时

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

Mode = Literal["chat", "rewrite_shorter", "rewrite_politer", "rewrite_confident"]


class ChatRequest(BaseModel):
    message: str
    tone: Optional[str] = "Calm"
    scenario: Optional[str] = "general"
    mode: Optional[Mode] = "chat"


@app.get("/ping")
def ping():
    return {"ok": True}


@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))

    return JSONResponse(
        {
            "ok": True,
            "message": "Backend is running. Frontend not found in this service.",
            "hint": "If you want to serve frontend here, add a /frontend folder with index.html and assets.",
            "try": ["/docs", "/ping", "/chat (POST)"],
        }
    )


# =========================
# Helpers: tone & scenario -> style
# =========================
def scenario_label(scenario: str) -> str:
    s = (scenario or "general").strip().lower()
    # 你前端的 scenario 值如果是句子，这里也能兜住
    if "professor" in s:
        return "talking to a professor"
    if "friend" in s:
        return "messaging a friend"
    if "stranger" in s:
        return "replying to a stranger"
    return "general"


def tone_label(tone: str) -> str:
    t = (tone or "Calm").strip().lower()
    if "friendly" in t:
        return "Friendly"
    if "polite" in t:
        return "Polite"
    if "direct" in t:
        return "Direct"
    return "Calm"


# =========================
# Stable offline fallback generator (ALWAYS returns usable drafts)
# =========================
def make_fallback_drafts(intent: str, scenario: str, tone: str) -> Dict[str, Any]:
    """
    Return ready-to-send messages. This is used when Gemini fails,
    so your demo never breaks.
    """
    sc = scenario_label(scenario)
    tl = tone_label(tone)

    def greet_prof():
        return "Hello Professor, "
    def greet_stranger():
        return "Hi, "
    def greet_friend():
        return "Hey, "

    # greeting by scenario
    if sc == "talking to a professor":
        greet = greet_prof()
    elif sc == "replying to a stranger":
        greet = greet_stranger()
    elif sc == "messaging a friend":
        greet = greet_friend()
    else:
        greet = "Hi, "

    # tone tweaks
    if tl == "Direct":
        please = ""
        soften = ""
    elif tl == "Polite":
        please = " please"
        soften = " Thank you!"
    elif tl == "Friendly":
        please = ""
        soften = " Thanks!"
    else:  # Calm
        please = ""
        soften = ""

    it = (intent or "").strip().lower()

    # classify intent
    if it.startswith(("follow up", "follow-up", "followup")):
        reply = f"{greet}just following up on [what I'm following up on]. No rush—whenever you have a moment.{soften}".strip()
        options = [
            f"{greet}quick follow-up on [what I'm following up on]. Whenever you're free is totally fine.{soften}".strip(),
            f"{greet}checking in about [what I'm following up on]. Let me know when you get a chance.{soften}".strip(),
            f"{greet}just wanted to follow up on [what I'm following up on]. Thanks! ".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    if it.startswith(("say no", "decline", "turn down")):
        reply = f"{greet}thanks for asking, but I can't [request] right now. Could we do [alternative] instead?{soften}".strip()
        options = [
            f"{greet}I really appreciate it, but I won't be able to [request]. Maybe [alternative]?{soften}".strip(),
            f"{greet}I can't commit to [request] at the moment. Happy to help with [alternative].{soften}".strip(),
            f"{greet}thanks—I'll have to pass on [request]. Hope that's okay.{soften}".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    if it.startswith(("apologise", "apologize", "clarify")):
        reply = f"{greet}sorry—could you clarify [confusing part]{please}?{soften}".strip()
        options = [
            f"{greet}sorry, I might have misunderstood. Could you clarify [confusing part]{please}?{soften}".strip(),
            f"{greet}just to make sure I've got it right—what did you mean by [confusing part]?{soften}".strip(),
            f"{greet}could you clarify [confusing part]{please}?{soften}".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    if it.startswith(("start friendly",)):
        reply = f"{greet}hope you're doing well! About [main point]—could you help me with [details]{please}?{soften}".strip()
        options = [
            f"{greet}hope you're having a good day. Quick question about [main point]{please}.{soften}".strip(),
            f"{greet}just wanted to reach out about [main point]. Could you share [details]{please}?{soften}".strip(),
            f"{greet}hope all's good! Can I ask about [main point]{please}?{soften}".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    # "ask about <topic>" smart parse
    m = re.search(r"\bask\b.*\babout\b\s+(.+)$", intent, flags=re.IGNORECASE)
    if m:
        topic = m.group(1).strip().rstrip(".!?")
        reply = f"{greet}I had a quick question about {topic}. Could you clarify [your question]{please}?{soften}".strip()
        options = [
            f"{greet}could I ask about {topic}? I'm unsure about [your question].{soften}".strip(),
            f"{greet}quick question about {topic}: [your question].{soften}".strip(),
            f"{greet}I was reviewing {topic} and wanted to check [your question]{please}.{soften}".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    # generic "ask" intent
    if it.startswith("ask"):
        reply = f"{greet}I had a quick question: [your question].{soften}".strip()
        options = [
            f"{greet}quick question—[your question].{soften}".strip(),
            f"{greet}could I ask [your question]{please}?{soften}".strip(),
            f"{greet}just wanted to check: [your question].{soften}".strip(),
        ]
        return {"reply": reply, "options": options[:3]}

    # default
    reply = f"{greet}[your message]{soften}".strip()
    options = [
        f"{greet}[your message]{soften}".strip(),
        f"{greet}[your message].{soften}".strip(),
        f"{greet}[your message]{soften}".strip(),
    ]
    return {"reply": reply, "options": options[:3]}


# ========== Prompt pieces ==========
def build_system_instruction(tone: str, scenario: str) -> str:
    # 重点：强制它“给可发送消息”，不要当 assistant 回答用户
    return f"""
You are Echo — a message drafting assistant.

ROLE:
- The user either pastes what they RECEIVED, OR types an intention like "ask about lecture".
- Your job: draft what the user should SEND to the other person.

CRITICAL:
- Output ONLY the message the user should send (first person).
- Do NOT ask the user questions like "What do you want to ask?"
- If details are missing, use exactly ONE placeholder like [your question].
- Do NOT sound like a service assistant ("Happy to help", "Go ahead and ask" are NOT allowed).

STYLE:
- Tone = {tone}
- Scenario = {scenario}
- Follow tone/scenario, keep it short and natural.

OUTPUT:
Return ONLY valid JSON matching this schema:
{{
  "reply": "string",
  "options": ["string", "string", "string"]
}}
""".strip()


def looks_like_prompt_instruction(message: str) -> bool:
    """
    prompt-mode: short intent phrases OR explicit "write/draft" instructions
    """
    m = (message or "").strip().lower()
    starters = (
        "write a ", "rewrite ", "draft ", "generate ", "help me ",
        "ask ", "follow up", "follow-up", "followup",
        "say no", "decline", "turn down",
        "apologise", "apologize",
        "start friendly", "clarify",
        "please write", "can you write",
    )
    return m.startswith(starters) or len(m.split()) <= 6  # ✅ 允许像 "ask about ddl"


def normalize_prompt_intent(message: str, scenario: str, tone: str) -> str:
    m = (message or "").strip()

    match = re.search(r"\bask\b.*\babout\b\s+(.+)$", m, flags=re.IGNORECASE)
    if match:
        topic = match.group(1).strip().rstrip(".!?")
        return (
            f"Draft a ready-to-send message the user will send. "
            f"Ask about {topic}. "
            f"If details are missing, use ONE placeholder [your question]. "
            f"Do NOT ask the user what they want."
        )

    if m.lower().startswith(("follow up", "follow-up", "followup")):
        return (
            f"Draft a calm follow-up message the user will send. "
            f"Use ONE placeholder [what I'm following up on]. "
            f"Do NOT ask the user questions."
        )

    if m.lower().startswith(("say no", "decline", "turn down")):
        return (
            f"Draft a polite decline message the user will send. "
            f"Use ONE placeholder [request]. Optionally add one short alternative. "
            f"Do NOT ask the user questions."
        )

    if m.lower().startswith(("apologise", "apologize", "clarify")):
        return (
            f"Draft a brief apology + clarification request message. "
            f"Use ONE placeholder [confusing part]. "
            f"Do NOT ask the user questions."
        )

    if m.lower().startswith("start friendly"):
        return (
            f"Draft a friendly opener then lead into [main point]. "
            f"Do NOT ask the user questions."
        )

    return (
        f"Draft a ready-to-send message. "
        f"If details are missing, use ONE placeholder [details]. "
        f"Do NOT ask the user questions."
    )


def build_user_content(message: str, mode: str, scenario: str, tone: str) -> str:
    message = (message or "").strip()

    if mode == "chat":
        if looks_like_prompt_instruction(message):
            normalized = normalize_prompt_intent(message, scenario, tone)
            return f"""
Task:
Write the exact message the user should SEND to the other person.

User intention / instruction:
{normalized}
""".strip()

        return f"""
Task:
Write what the user should send as a reply to the message below.

Message the user RECEIVED:
{message}
""".strip()

    if mode == "rewrite_shorter":
        task = "Rewrite the user's draft to be shorter without changing meaning."
    elif mode == "rewrite_politer":
        task = "Rewrite the user's draft to be more polite (not overly apologetic)."
    elif mode == "rewrite_confident":
        task = "Rewrite the user's draft to sound more confident and clear."
    else:
        task = "Rewrite the user's draft."

    return f"""{task}

User's DRAFT message to rewrite:
{message}
""".strip()


# ========== Gemini caller ==========
def call_gemini(system_text: str, user_text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Missing GEMINI_API_KEY environment variable.")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    payload = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 400,  # ✅ demo 够用，越短越稳
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "reply": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["reply"],
            },
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=45)
    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Gemini HTTP {r.status_code}: {r.text[:240]}")

    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise HTTPException(status_code=500, detail="Empty Gemini response")
    return text


def extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        block = m.group(0).strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    reply_match = re.search(r'"reply"\s*:\s*"(.+?)"', text, flags=re.DOTALL)
    reply = reply_match.group(1).strip() if reply_match else ""
    reply = reply.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"').strip()

    if not reply:
        return {"reply": "", "options": []}

    return {"reply": reply, "options": []}


@app.post("/chat")
def chat(req: ChatRequest):
    tone = req.tone or "Calm"
    scenario = req.scenario or "general"
    mode = req.mode or "chat"
    msg = (req.message or "").strip()

    # demo mock
    if USE_MOCK:
        return {
            "reply": "Hi, just following up on [what I'm following up on]. No rush—whenever you have a moment.",
            "options": [
                "Hi, quick follow-up on [what I'm following up on]. Whenever you're free is totally fine.",
                "Hi, checking in about [what I'm following up on]. Let me know when you get a chance.",
                "Hi, just wanted to follow up on [what I'm following up on]. Thanks!",
            ],
        }

    system_text = build_system_instruction(tone, scenario)
    user_text = build_user_content(msg, mode, scenario, tone)

    # ✅ 关键：Gemini 挂了也要有“好用的输出”
    try:
        raw = call_gemini(system_text, user_text)
        obj = extract_json(raw)
        reply = (obj.get("reply") or "").strip()
        options = obj.get("options") or []
        options = [str(x).strip() for x in options if str(x).strip()][:3]

        # 如果 Gemini 输出像 "Happy to help..." 这种 assistant 话术，直接降级
        bad_phrases = ("happy to help", "go ahead and ask", "what would you like", "what are your questions")
        if (not reply) or any(p in reply.lower() for p in bad_phrases):
            fb = make_fallback_drafts(msg, scenario, tone)
            return {"reply": fb["reply"], "options": fb["options"]}

        #  options 不够也补齐（demo 更好看）
        if len(options) < 3:
            fb = make_fallback_drafts(msg, scenario, tone)
            merged = options + [x for x in fb["options"] if x not in options]
            options = merged[:3]

        return {"reply": reply, "options": options}

    except Exception:
        # 直接本地生成一套可发的
        fb = make_fallback_drafts(msg, scenario, tone)
        return {"reply": fb["reply"], "options": fb["options"]}
