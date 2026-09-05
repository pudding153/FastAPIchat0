from pydantic import BaseModel, Field
import copy
import os
import logging
import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

from google import genai
from google.genai import types
from google.genai.errors import APIError
from typing import List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=key)
app = FastAPI()

# ======================
# トークン統計の永続化
# ======================
DATA_FILE = Path("token_data.json")

def get_current_month() -> str:
    """YYYY-MM 形式で現在の月を返す"""
    return datetime.now().strftime("%Y-%m")

def load_data() -> dict:
    """ファイルからデータを読み込む。存在しない場合は初期化"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"データ読み込み失敗: {e}")

    # 初期データ
    return {
        "current_month": get_current_month(),
        "current": {
            "total_input": 0,
            "total_output": 0,
            "request_count": 0
        },
        "history": {}  # {"2026-08": {...}, "2026-07": {...}}
    }

def save_data(data: dict):
    """データをファイルに保存"""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"データ保存失敗: {e}")

def ensure_current_month(data: dict) -> dict:
    """
    月が変わっていたら前月を履歴に移し、当月をリセットする
    """
    current = get_current_month()
    if data.get("current_month") != current:
        # 前月データを履歴へ
        prev_month = data.get("current_month")
        if prev_month and data.get("current"):
            data["history"][prev_month] = data["current"].copy()
            logger.info(f"月次リセット: {prev_month} を履歴に保存しました")

        # 当月をリセット
        data["current_month"] = current
        data["current"] = {
            "total_input": 0,
            "total_output": 0,
            "request_count": 0
        }
        save_data(data)
    return data

# 起動時にロード
token_data = load_data()
token_data = ensure_current_month(token_data)

# ======================
# CORS
# ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fastapichat-mmm3.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# ======================
# モデル
# ======================
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=400, description="ユーザーからの入力メッセージ")
    history: list = []
    custom_prompt: Optional[str] = Field(default="", max_length=30)

# ======================
# エンドポイント
# ======================
@app.get("/api/ping")
async def ping_endpoint():
    return {"status": "ok"}

@app.get("/api/token-stats")
async def get_token_stats():
    """当月の統計を返す（必要なら月次リセットも実行）"""
    global token_data
    token_data = ensure_current_month(token_data)
    return token_data["current"]

@app.get("/api/token-stats/history")
async def get_token_history():
    """
    過去月の履歴を新しい順で返す
    フロントエンドが期待する形式:
    [
      {"month": "2026-08", "total_input": ..., "total_output": ..., "request_count": ...},
      ...
    ]
    """
    global token_data
    token_data = ensure_current_month(token_data)

    history_list = []
    for month, stats in token_data.get("history", {}).items():
        history_list.append({
            "month": month,
            "total_input": stats.get("total_input", 0),
            "total_output": stats.get("total_output", 0),
            "request_count": stats.get("request_count", 0)
        })

    # 新しい月が上に来るようにソート
    history_list.sort(key=lambda x: x["month"], reverse=True)
    return history_list

@app.post("/api/chat")
async def chat_endpoint(data: ChatRequest):
    full_reply = ""
    try:
        message = data.message

        full_history = copy.deepcopy(data.history)
        full_history.append({"role": "user", "parts": [{"text": message}]})

        talk = copy.deepcopy(data.history)
        talk.append({"role": "user", "parts": [{"text": message}]})

        # 記憶管理
        MAX_HISTORY_TOKENS = 1900

        def count_approx_tokens(chat_history):
            total = 0
            for msg in chat_history:
                parts = msg.get("parts", [])
                if isinstance(parts, list) and parts:
                    text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)])
                    total += len(text)
            return total

        while count_approx_tokens(talk) > MAX_HISTORY_TOKENS and len(talk) > 0:
            talk.pop(0)
        if talk and talk[0].get("role") == "model":
            talk.pop(0)

        # システムプロンプト
        s = ("返答は必ず250文字以内で生成する。検索ブラウジングの使用は1回リクエストごと必ず1回以下しか使用しないこと。文脈を読んで返答長さを調整する。挨拶や短文のリクエストに対してはある程度短く返答する。矛盾や嘘が無いよう不確かな情報は「わかりません」と答える会話をAI側か終わらせようとしない。むやみに全肯定せず正しい意見伝える。")
        if data.custom_prompt and data.custom_prompt.strip():
            s += f"\n\n追加のプロンプト\n{data.custom_prompt.strip()}"

        ai_config = types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=300,
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )

        async def event_generator():
            nonlocal full_reply
            usage_info = None
            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    contents=talk,
                    model="gemini-3.1-flash-lite",
                    config=ai_config
                ):
                    if chunk.text:
                        full_reply += chunk.text
                        yield json.dumps({"text": chunk.text}, ensure_ascii=False) + "\n"

                    if chunk.usage_metadata:
                        usage_info = chunk.usage_metadata

                # ===== トークン集計 =====
                if usage_info:
                    global token_data
                    token_data = ensure_current_month(token_data)

                    token_data["current"]["total_input"] += usage_info.prompt_token_count or 0
                    token_data["current"]["total_output"] += usage_info.candidates_token_count or 0
                    token_data["current"]["request_count"] += 1

                    save_data(token_data)

                    logger.info(
                        f"[TOKEN USAGE] input={usage_info.prompt_token_count}, "
                        f"output={usage_info.candidates_token_count}, "
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
            detail={"error": "AIerror", "detail": str(e)}
        )
    except Exception as e:
        logger.error(f"Unexpected Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "error", "detail": str(e)}
        )

# 静的ファイル
app.mount("/", StaticFiles(directory="static", html=True), name="static")
