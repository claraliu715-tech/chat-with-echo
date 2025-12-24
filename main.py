import os
from dotenv import load_dotenv

load_dotenv()

USE_MOCK = os.getenv("USE_MOCK", "false") == "true"

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Literal, Dict, Any
from pathlib import Path
import requests
import json
import re




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
# 你的设计：仓库根目录 /frontend
FRONTEND_DIR = BACKEND_DIR / "frontend"  # ✅ 根目录版（因为 main.py 在根目录）
# 如果你坚持 main.py 在 backend/ 文件夹里，就用：BACKEND_DIR.parent / "frontend"


# ✅ 只有当目录存在时才挂载静态资源（Render 上没有也不会崩）
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

    # ✅ Render 只跑后端时，给一个不会报 500 的提示
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
- The USER pastes a message they RECEIVED from someone else.
- Your job is to write what the USER should SEND BACK.

Strict rules:
- Write ONLY messages the USER could send.
- ALWAYS write in first person ("I", "Thanks", "Sure").
- NEVER write the other person's reply.
- NEVER continue the conversation as the other speaker.
- NEVER say things like "Of course, what do you need?" unless the USER is the one saying it.
- Do NOT explain, analyse, or give advice.
- Do NOT ask questions unless the user's reply should be a question.
- Each sentence should be short and natural.

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
- "options": 3 alternative messages the USER could send.
""".strip()


def build_user_content(message: str, mode: str) -> str:
    if mode == "chat":
        return f"""
Ttask = "Write what the user should send as a reply."

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
        raise HTTPException(status_code=500, detail=f"Gemini HTTP {r.status_code}: {r.text[:800]}")

    data = r.json()

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise ValueError("Empty Gemini response")
        return text
    except Exception:
        raise HTTPException(status_code=500, detail=f"Unexpected Gemini response structure: {data}")


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise HTTPException(status_code=500, detail=f"Model did not return JSON. Raw: {text[:500]}")
    try:
        return json.loads(m.group(0))
    except Exception:
        raise HTTPException(status_code=500, detail=f"Bad JSON from model. Raw: {text[:500]}")


@app.post("/chat")
def chat(req: ChatRequest):
    tone = req.tone or "Calm"
    scenario = req.scenario or "general"
    mode = req.mode or "chat"

    # ✅ MOCK 模式：不调用 Gemini
    if USE_MOCK:
        return {
            "reply": "Just following up — let me know when you have a moment.",
            "options": [
                "Checking in — feel free to reply when you’re free.",
                "Just wanted to check in.",
                "Let me know when you get a chance."
            ]
        }

    # ===== 真·Gemini 调用（等 quota 恢复再用）=====
    system_text = build_system_instruction(tone, scenario)
    user_text = build_user_content(req.message, mode)

    raw = call_gemini(system_text, user_text)
    obj = extract_json(raw)

    reply = (obj.get("reply") or "").strip()
    if not reply:
        raise HTTPException(status_code=500, detail="Empty reply")

    options = obj.get("options") or []
    options = [str(x).strip() for x in options][:3]

    return {"reply": reply, "options": options}
