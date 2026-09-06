from pydantic import BaseModel, Field
import copy
import os
import logging
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from typing import Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=key)
app = FastAPI()


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    logger.error(f"データディレクトリ作成失敗: {e}")

DATA_FILE = DATA_DIR / "token_data.json"


TOKEN_LOG_FILE = DATA_DIR / "token_usage.log"


TOKEN_LOG_PATTERN = re.compile(
    r"\[TOKEN USAGE\] input=(?P<input>\d+),\s*output=(?P<output>\d+)",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")

def get_current_month() -> str:
    return datetime.now().strftime("%Y-%m")

def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"データ読み込み失敗: {e}")
    return {
        "current_month": get_current_month(),
        "current": {
            "total_input": 0,
            "total_output": 0,
            "request_count": 0,
        },
        "history": {},
    }

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"データ保存失敗: {e}")

def ensure_current_month(data: dict) -> dict:
    current = get_current_month()
    if data.get("current_month") != current:
        prev_month = data.get("current_month")
        if prev_month and data.get("current"):
            data["history"][prev_month] = data["current"].copy()
            logger.info(f"月次リセット: {prev_month} を履歴に保存しました")
        data["current_month"] = current
        data["current"] = {
            "total_input": 0,
            "total_output": 0,
            "request_count": 0,
        }
        save_data(data)
    return data

token_data = load_data()
token_data = ensure_current_month(token_data)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fastapichat-mmm3.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=400)
    history: list = []
    custom_prompt: Optional[str] = Field(default="", max_length=60)

class RestoreRequest(BaseModel):
    logs: str
    reset_current: bool = False

def apply_token_matches_to_data(matches_with_month, reset_current: bool):
    global token_data
    monthly = defaultdict(lambda: {"total_input": 0, "total_output": 0, "request_count": 0})
    for month, inp, out in matches_with_month:
        monthly[month]["total_input"] += inp
        monthly[month]["total_output"] += out
        monthly[month]["request_count"] += 1

    token_data = ensure_current_month(token_data)
    current_month = get_current_month()

    for month, stats in monthly.items():
        if month == current_month:
            continue
        token_data["history"][month] = stats

    if current_month in monthly:
        if reset_current:
            token_data["current"] = monthly[current_month]
        else:
            cur = token_data["current"]
            src = monthly[current_month]
            cur["total_input"] += src["total_input"]
            cur["total_output"] += src["total_output"]
            cur["request_count"] += src["request_count"]

    save_data(token_data)
    return monthly


def append_token_log_line(input_tokens: int, output_tokens: int):
    """リクエストごとのトークン使用量を /data/token_usage.log に追記する"""
    try:
        line = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"[TOKEN USAGE] input={input_tokens}, output={output_tokens}\n"
        )
        with open(TOKEN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.error(f"トークンログ書き込み失敗: {e}")

def read_local_token_logs(days: int = 365) -> list:
    """
    ディスクに保存された token_usage.log から
    直近 days 日分の行を読み込んで返す（Render Logs API を叩かない）
    """
    if not TOKEN_LOG_FILE.exists():
        return []

    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = []
    try:
        with open(TOKEN_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                dm = DATE_PATTERN.search(line)
              
                if dm and dm.group("date") < cutoff_date:
                    continue
                lines.append(line)
    except Exception as e:
        logger.error(f"トークンログ読み込み失敗: {e}")
    return lines


@app.get("/api/ping")
async def ping_endpoint():
    return {"status": "ok"}

@app.get("/api/token-stats")
async def get_token_stats():
    global token_data
    token_data = ensure_current_month(token_data)
    return token_data["current"]

@app.get("/api/token-stats/history")
async def get_token_history():
    global token_data
    token_data = ensure_current_month(token_data)
    history_list = []
    for month, stats in token_data.get("history", {}).items():
        history_list.append(
            {
                "month": month,
                "total_input": stats.get("total_input", 0),
                "total_output": stats.get("total_output", 0),
                "request_count": stats.get("request_count", 0),
            }
        )
    history_list.sort(key=lambda x: x["month"], reverse=True)
    return history_list

@app.post("/api/token-stats/restore")
async def restore_from_logs(req: RestoreRequest):
    matches_with_month = []
    pattern = re.compile(
        r"(?P<date>\d{4}-\d{2}-\d{2}).*?\[TOKEN USAGE\] input=(?P<input>\d+),\s*output=(?P<output>\d+)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(req.logs):
        matches_with_month.append(
            (m.group("date")[:7], int(m.group("input")), int(m.group("output")))
        )
    if not matches_with_month:
        raise HTTPException(status_code=400, detail="TOKEN USAGE の行が見つかりませんでした")
    monthly = apply_token_matches_to_data(matches_with_month, reset_current=req.reset_current)
    return {
        "restored_months": sorted(list(monthly.keys()), reverse=True),
        "matched_lines": len(matches_with_month),
        "current": token_data["current"],
        "history_count": len(token_data.get("history", {})),
    }


@app.post("/api/token-stats/restore-auto")
async def restore_from_render_logs(reset_current: bool = True, days: int = 365):
    lines = read_local_token_logs(days=days)
    matches_with_month = []
    for line in lines:
        m = TOKEN_LOG_PATTERN.search(line)
        if not m:
            continue
        dm = DATE_PATTERN.search(line)
        month = dm.group("date")[:7] if dm else get_current_month()
        matches_with_month.append((month, int(m.group("input")), int(m.group("output"))))

    if not matches_with_month:
        raise HTTPException(status_code=404, detail="token_usage.log に TOKEN USAGE の記録が見つかりませんでした")

    monthly = apply_token_matches_to_data(matches_with_month, reset_current=reset_current)
    return {
        "matched_lines": len(matches_with_month),
        "restored_months": sorted(monthly.keys(), reverse=True),
        "current": token_data["current"],
        "history_count": len(token_data.get("history", {})),
    }


@app.post("/api/chat")
async def chat_endpoint(data: ChatRequest):
    full_reply = ""
    try:
        message = data.message
        full_history = copy.deepcopy(data.history)
        full_history.append({"role": "user", "parts": [{"text": message}]})
        talk = copy.deepcopy(data.history)
        talk.append({"role": "user", "parts": [{"text": message}]})
        MAX_HISTORY_TOKENS = 1999

        def count_approx_tokens(chat_history):
            total = 0
            for msg in chat_history:
                parts = msg.get("parts", [])
                if isinstance(parts, list) and parts:
                    text = "".join(
                        [p.get("text", "") for p in parts if isinstance(p, dict)]
                    )
                    total += len(text)
            return total

        while count_approx_tokens(talk) > MAX_HISTORY_TOKENS and len(talk) > 0:
            talk.pop(0)
        if talk and talk[0].get("role") == "model":
            talk.pop(0)

        s = (
            "返答は必ず250文字以内で生成する。検索ブラウジングの使用は1回リクエストごと必ず1回以下しか使用しないこと。"
            "文脈を読んで返答長さを調整する。挨拶や短文のリクエストに対してはある程度短く返答する。"
            "矛盾や嘘が無いよう不確かな情報は「わかりません」と答える会話をAI側か終わらせようとしない。"
            "むやみに全肯定せず正しい意見伝える。"
        )
        if data.custom_prompt and data.custom_prompt.strip():
            s += f"\n\n追加のプロンプト\n{data.custom_prompt.strip()}"

        ai_config = types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=300,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        async def event_generator():
            nonlocal full_reply
            usage_info = None
            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    contents=talk,
                    model="gemini-3.1-flash-lite",
                    config=ai_config,
                ):
                    if chunk.text:
                        full_reply += chunk.text
                        yield json.dumps({"text": chunk.text}, ensure_ascii=False) + "\n"
                    if chunk.usage_metadata:
                        usage_info = chunk.usage_metadata
                if usage_info:
                    global token_data
                    token_data = ensure_current_month(token_data)
                    input_tokens = usage_info.prompt_token_count or 0
                    output_tokens = usage_info.candidates_token_count or 0
                    token_data["current"]["total_input"] += input_tokens
                    token_data["current"]["total_output"] += output_tokens
                    token_data["current"]["request_count"] += 1
                    save_data(token_data)

            
                    append_token_log_line(input_tokens, output_tokens)
                   

                    logger.info(
                        f"[TOKEN USAGE] input={input_tokens}, "
                        f"output={output_tokens}, "
                        f"total={usage_info.total_token_count}"
                    )
                else:
                    logger.info("[TOKEN USAGE] usage_metadata not found in stream")
                full_history.append({"role": "model", "parts": [{"text": full_reply}]})
                yield json.dumps({"final_history": full_history}, ensure_ascii=False) + "\n"
            except Exception as e:
                logger.error(f"Stream Error: {e}")
                yield json.dumps({"error": "Stream interrupted"}) + "\n"
                full_history.append({"role": "model", "parts": [{"text": full_reply}]})
                yield json.dumps({"final_history": full_history}, ensure_ascii=False) + "\n"

        return StreamingResponse(event_generator(), media_type="application/x-ndjson")

    except APIError as e:
        logger.error(f"Gemini API Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "AIerror", "detail": str(e)},
        )
    except Exception as e:
        logger.error(f"Unexpected Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "error", "detail": str(e)},
        )

app.mount("/", StaticFiles(directory="static", html=True), name="static")
