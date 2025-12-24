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

# ✅ 挂载前端静态资源（/static/*）
# 你的前端引用是 /static/style.css /static/script.js /static/logo.jpg
# 所以这里挂载整个 frontend 目录为 /static 是合理的
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
  (B) give you a writing PROMPT / instruction (e.g., "Write a calm message to...").
- Your job is to produce a message the USER should SEND.

Strict rules:
- Write ONLY the message the USER could send.
- ALWAYS write in first person ("I", "Thanks", "Sure").
- Do NOT explain, analyse, or give advice.
- Do NOT roleplay the other person.
- Keep it short and natural.

JSON SAFETY RULES (IMPORTANT):
- Output MUST be valid JSON only, nothing else.
- Do NOT include double quotes " inside reply/options text.
  Use apostrophes (') instead if needed.
- Keep strings single-line (avoid line breaks).

Context:
- Tone = {tone}
- Scenario = {scenario}

Output format:
Return ONLY valid JSON.

JSON schema:
{{
  "reply": "string",
  "options": ["string", "string", "string"]
}}

Meaning:
- "reply": the best message the USER could send.
- "options": up to 3 alternative messages the USER could send.
""".strip()


def looks_like_prompt_instruction(message: str) -> bool:
    """
    Heuristic: if the message starts like a writing instruction, treat it as prompt (B).
    This matches your starter pills (B) design.
    """
    m = (message or "").strip().lower()
    starters = ("write a ", "rewrite ", "draft ", "generate ", "help me ")
    return m.startswith(starters)


def build_user_content(message: str, mode: str) -> str:
    message = (message or "").strip()

    if mode == "chat":
        if looks_like_prompt_instruction(message):
            # Mode B: user gave a prompt/instruction
            return f"""
Task: Follow the user's instruction and write a message the user can send.

User instruction:
{message}
""".strip()

        # Mode A: user pasted what they received
        return f"""
Task: Write what the user should send as a reply.

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

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

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
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3
                    }
                },
                "required": ["reply"]
            }
        },
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not reach Gemini: {repr(e)}")

    if r.status_code != 200:
        # 这里不要直接抛一大段 raw，避免前端爆炸；截断即可
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

    # 1) strict JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) extract { ... }
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        block = m.group(0).strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 3) salvage reply (more tolerant)
    # allow multiline match
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

    # ✅ MOCK 模式
    if USE_MOCK:
        return {
            "reply": "Just following up — let me know when you have a moment.",
            "options": [
                "Checking in — feel free to reply when you’re free.",
                "Just wanted to check in.",
                "Let me know when you get a chance."
            ]
        }

    system_text = build_system_instruction(tone, scenario)
    user_text = build_user_content(req.message, mode)

    # ✅ 任何异常都不让它 500（除非是你想调试）
    try:
        raw = call_gemini(system_text, user_text)
        obj = extract_json(raw)
    except Exception:
        # 降级响应：避免前端隔一条就炸
        return {"reply": "Sorry — something went wrong generating the reply. Please try again.", "options": []}

    reply = (obj.get("reply") or "").strip()
    if not reply:
        reply = "Sorry — I couldn’t generate a clean reply. Please try again."

    options = obj.get("options") or []
    options = [str(x).strip() for x in options if str(x).strip()][:3]

    return {"reply": reply, "options": options}
