import os
import json
import re
from pathlib import Path
from typing import Optional, Literal, Dict, Any

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


# ========== Prompt pieces ==========
def build_system_instruction(tone: str, scenario: str) -> str:
    return f"""
You are Echo — a message drafting assistant.

IMPORTANT ROLE RULE:
- The user will either:
  (A) paste a message they RECEIVED from someone else, OR
  (B) type a writing PROMPT / intention (e.g., "ask about lecture", "follow up", "say no"),
      and you must draft what the user should SEND.

Strict rules:
- Write ONLY the message the USER could send.
- ALWAYS write in first person ("I", "Thanks", "Sure").
- Do NOT explain, analyse, or give advice.
- Do NOT roleplay the other person.
- Keep it short and natural.
- IMPORTANT: Do NOT ask the user what they want. If details are missing, use ONE placeholder like [your question].

JSON SAFETY RULES (IMPORTANT):
- Output MUST be valid JSON only, nothing else.
- Do NOT include double quotes " inside reply/options text.
  Use apostrophes (') instead if needed.
- Keep strings single-line (avoid line breaks).

Context:
- Tone = {tone}
- Scenario = {scenario}

TONE & SCENARIO BEHAVIOUR (MUST FOLLOW):
- Scenario "replying to a stranger":
  Start with a short greeting (Hi or Hello),
  be polite and respectful,
  avoid emojis,
  do NOT sound like a service assistant.

- Scenario "talking to a professor":
  Start with Hi Professor or Hello Professor,
  be formal and clear,
  no emojis.

- Scenario "messaging a friend":
  Casual and warm,
  light emoji is acceptable.

- Scenario "general":
  Neutral and flexible.

- Tone "Friendly":
  Warm and approachable,
  but NOT overly casual or chatty.

- Tone "Polite":
  Use please or thank you when appropriate,
  not apologetic.

- Tone "Direct":
  Short and to the point,
  no filler phrases.

- Tone "Calm":
  Neutral, steady, not pushy.

Output format:
Return ONLY valid JSON.

JSON schema:
{{
  "reply": "string",
  "options": ["string", "string", "string"]
}}
""".strip()


def looks_like_prompt_instruction(message: str) -> bool:
    """
    Treat short intents (ask/follow up/say no...) as prompt-mode too.
    This matches your UI where users may type quick intents.
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
    return m.startswith(starters)


def normalize_prompt_intent(message: str, scenario: str, tone: str) -> str:
    """
    Convert vague short intents into explicit drafting instructions so the model
    outputs a ready-to-send message (not a question back to the user).
    """
    m = (message or "").strip()

    # ask ... about <topic>
    match = re.search(r"\bask\b.*\babout\b\s+(.+)$", m, flags=re.IGNORECASE)
    if match:
        topic = match.group(1).strip().rstrip(".!?")
        return (
            f"Write a {tone.lower()} message to {scenario} to ask about {topic}. "
            f"Include ONE placeholder like [your question]. "
            f"Do NOT ask the user what they want to ask. "
            f"Return a ready-to-send message."
        )

    # follow up ...
    if m.lower().startswith(("follow up", "follow-up", "followup")):
        return (
            f"Write a {tone.lower()} follow-up message to {scenario}. "
            f"Include ONE placeholder like [what I’m following up on]. "
            f"Sound calm and not pushy. "
            f"Return a ready-to-send message."
        )

    # say no / decline ...
    if m.lower().startswith(("say no", "decline", "turn down")):
        return (
            f"Write a {tone.lower()} message to {scenario} to politely decline. "
            f"Include ONE placeholder like [request]. "
            f"Optionally offer an alternative in one short sentence. "
            f"Return a ready-to-send message."
        )

    # apologise + clarify ...
    if m.lower().startswith(("apologise", "apologize", "clarify")):
        return (
            f"Write a {tone.lower()} message to {scenario} to briefly apologise and ask for clarification. "
            f"Include ONE placeholder like [confusing part]. "
            f"Return a ready-to-send message."
        )

    # start friendly ...
    if m.lower().startswith("start friendly"):
        return (
            f"Write a {tone.lower()} friendly opening line to {scenario}, then smoothly lead into [main point]. "
            f"Return a ready-to-send message."
        )

    # generic prompt-mode fallback
    return (
        f"Write a {tone.lower()} message to {scenario}. "
        f"If details are missing, include ONE placeholder like [details]. "
        f"Return a ready-to-send message. Do NOT ask the user questions."
    )


def build_user_content(message: str, mode: str, scenario: str, tone: str) -> str:
    message = (message or "").strip()

    if mode == "chat":
        if looks_like_prompt_instruction(message):
            normalized = normalize_prompt_intent(message, scenario, tone)

            return f"""
Task:
You are NOT replying to the user.
You are writing a message ON BEHALF OF the user,
which the user will SEND TO ANOTHER PERSON.

Write the exact message the user should send.
If details are missing, use ONE placeholder like [your question].
Do NOT ask the user for more details.
Do NOT act as a service assistant.

User instruction:
{normalized}
""".strip()

        # Mode A: user pasted what they received
        return f"""
Task: Write what the user should send as a reply.

Message the user RECEIVED:
{message}
""".strip()

    # rewrite modes
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
            "maxOutputTokens": 800,
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

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not reach Gemini: {repr(e)}")

    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Gemini HTTP {r.status_code}: {r.text[:400]}")

    data = r.json()

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise ValueError("Empty Gemini response")
        return text
    except Exception:
        raise HTTPException(status_code=500, detail=f"Unexpected Gemini response structure: {str(data)[:400]}")


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
    reply = reply.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')

    if not reply:
        return {"reply": "Sorry — I couldn’t format a clean JSON response. Please try again.", "options": []}

    return {"reply": reply, "options": []}


@app.post("/chat")
def chat(req: ChatRequest):
    tone = req.tone or "Calm"
    scenario = req.scenario or "general"
    mode = req.mode or "chat"

    if USE_MOCK:
        return {
            "reply": "Just following up — let me know when you have a moment.",
            "options": [
                "Checking in — feel free to reply when you’re free.",
                "Just wanted to check in.",
                "Let me know when you get a chance.",
            ],
        }

    system_text = build_system_instruction(tone, scenario)
    user_text = build_user_content(req.message, mode, scenario, tone)

    try:
        raw = call_gemini(system_text, user_text)
        obj = extract_json(raw)
    except Exception:
        return {"reply": "Sorry — something went wrong generating the reply. Please try again.", "options": []}

    reply = (obj.get("reply") or "").strip()
    if not reply:
        reply = "Sorry — I couldn’t generate a clean reply. Please try again."

    options = obj.get("options") or []
    options = [str(x).strip() for x in options if str(x).strip()][:3]

    return {"reply": reply, "options": options}

