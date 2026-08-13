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
from typing import List

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
        talk = copy.deepcopy(data.history)
        #記憶管理
        talk.append({"role":"user","parts":[{"text":message}]})
        MAX_HISTORY_TOKENS = 3500
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
        s = ("返答はすべて必ず270文字以内で生成してください、Google Searchの使用は必ず１つのリクエストに対して一回までに制限してください、AI側から会話を終わらせようとしないでください、返答は必ず正しい情報だけを伝えて不確かな確証のない情報にたいしては正直にわかりませんと応えてください")
        ai_config=types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=300,
            tools=[types.Tool(google_search=types.GoogleSearch())]
)
        async def event_generator():
            full_reply = ""
            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    contents=talk,
                    model="gemini-2.5-flash-lite",
                    config=ai_config
                ):
                    if chunk.text:
                        full_reply += chunk.text
                        yield json.dumps({"text":chunk.text},ensure_ascii=False) + "\n"
                talk.append({"role":"model","parts":[{"text":full_reply}]})
                yield json.dumps({"final_history":talk}, ensure_ascii=False) + "\n"
            except Exception as e:
                logger.error(f"Stream Error:{e}")
                yield json.dumps({"error":"Stream interrupted"}) + "\n"
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
