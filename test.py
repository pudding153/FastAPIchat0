import os
import logging
from fastapi import FastAPI,HTTPException,status,Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
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
    message:str
    history:list=[]
#通信
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
#受取  #リクエスト
@app.post("/api/chat")
async def chat_endpoint(data:ChatRequest):
    try:
        message = data.message
        talk = data.history
        #記憶管理
        if len(talk)>49:
            talk = talk[-49:]
            if talk and talk[0].get("role")=="model":
                talk.pop(0)
#会話履歴
        talk.append({"role":"user","parts":[{"text":message}]})
#api設定
        s = ("setting")
        ai_config=types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=200
)
        response = client.models.generate_content(
            contents=talk,
            model="gemini-3.1-flash-lite",
            config=ai_config
)
        talk.append({"role":"model","parts":[{"text":response.text}]})
    #送り返す
        return{
            "success":True,
            "reply":response.text,
            "history":talk
    }
    except APIError as e:
        logger.error(f"Gemini API Error:{e}")
        return{
            "success":False,
            "error":"AIerror",
            "detail":str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected Error:{e}")
        return{
            "success":False,
            "error":"error",
            "detail":str(e)
        }
app.mount("/",StaticFiles(directory="static",html=True),name="static")
