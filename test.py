from pydantic import BaseModel, Field
import copy
import os
import logging
import json
from fastapi import FastAPI,HTTPException,status,Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel
from typing import List, Optional 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
#key
load_dotenv()
key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=key)
app = FastAPI()
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=400, description="ユーザーからの入力メッセージ")
    history: list = []
    custom_prompt: Optional[str] = Field(default="", max_length=30)
#通信
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fastapichat-mmm3.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
#受取  #リクエスト
@app.get("/api/ping")
async def ping_endpoint():
    return {"status": "ok"}
@app.post("/api/chat")
async def chat_endpoint(data:ChatRequest):
    full_reply = ""
    try:
        message = data.message

        full_history = copy.deepcopy(data.history)
        full_history.append({"role":"user","parts":[{"text":message}]})

        talk = copy.deepcopy(data.history)
        talk.append({"role":"user","parts":[{"text":message}]})
        #記憶管理
        MAX_HISTORY_TOKENS = 1500
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
#会話履歴

#api設定
        s = ("返答は必ず250文字以内で生成する。検索ブラウジングの使用は1回リクエストごと必ず1回以下しか使用しないこと。文脈を読んで返答長さを調整する。挨拶や短文のリクエストに対してはある程度短く返答する。矛盾や嘘が無いよう不確かな情報は「わかりません」と答える会話をAI側か終わらせようとしない。むやみに全肯定せず正しい意見伝える。")
        if data.custom_prompt and data.custom_prompt.strip():
            s += f"\n\n追加のプロンプト\n{data.custom_prompt.strip()}"
        ai_config=types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=300,
            tools=[types.Tool(google_search=types.GoogleSearch())]
)
        async def event_generator():
            full_reply = ""
            usage_info = None  #
            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    contents=talk,
                    model="gemini-3.1-flash-lite",
                    config=ai_config
                ):
                    if chunk.text:
                        full_reply += chunk.text
                        yield json.dumps({"text":chunk.text},ensure_ascii=False) + "\n"

                    #
                    if chunk.usage_metadata:
                        usage_info = chunk.usage_metadata

                #
                if usage_info:
                    logger.info(
                        f"[TOKEN USAGE] input={usage_info.prompt_token_count}, "
                        f"output={usage_info.candidates_token_count}, "
                        f"total={usage_info.total_token_count}"
                    )
                else:
                    logger.info("[TOKEN USAGE] usage_metadata not found in stream")

                full_history.append({"role":"model","parts":[{"text":full_reply}]})
                yield json.dumps({"final_history":full_history}, ensure_ascii=False) + "\n"
            except Exception as e:
                logger.error(f"Stream Error:{e}")
                yield json.dumps({"error":"Stream interrupted"}) + "\n"

                full_history.append({"role":"model","parts":[{"text":full_reply}]})
                yield json.dumps({"final_history":full_history}, ensure_ascii=False) + "\n"

        return StreamingResponse(event_generator(),media_type="application/x-ndjson") 
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
app.mount("/",StaticFiles(directory="static",html=True),name="static")
