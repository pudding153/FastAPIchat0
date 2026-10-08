from pydantic import BaseModel, Field
import copy
import os
import time
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


_LATENCY_PART = (
    r"(?:[^\n]*?latency=(?P<latency>\d+(?:\.\d+)?),\s*ttft=(?P<ttft>\d+(?:\.\d+)?)"
    r"(?:,\s*search=(?P<search>[01]))?)?"
)
TOKEN_LOG_PATTERN = re.compile(
    r"\[TOKEN USAGE\] input=(?P<input>\d+),\s*output=(?P<output>\d+)" + _LATENCY_PART,
    re.IGNORECASE,
)
TOKEN_LOG_WITH_DATE_PATTERN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[^\n]*?\[TOKEN USAGE\] input=(?P<input>\d+),\s*output=(?P<output>\d+)"
    + _LATENCY_PART,
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


def get_current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def new_stats() -> dict:
    """1か月分の統計の初期値。
    *_latency / *_ttft は秒の合計値。平均 = 合計 / *_count (または latency_count)。
    """
    return {
        "total_input": 0,
        "total_output": 0,
        "request_count": 0,

        "total_latency": 0.0,
        "total_ttft": 0.0,
        "latency_count": 0,

        "search_latency": 0.0,
        "search_ttft": 0.0,
        "search_count": 0,

        "nosearch_latency": 0.0,
        "nosearch_ttft": 0.0,
        "nosearch_count": 0,
    }


def fill_missing(stats: dict) -> dict:
    for k, v in new_stats().items():
        stats.setdefault(k, v)
    return stats


def add_sample(
    stats: dict,
    input_tokens: int,
    output_tokens: int,
    latency: Optional[float] = None,
    ttft: Optional[float] = None,
    searched: Optional[bool] = None,
):
    fill_missing(stats)
    stats["total_input"] += input_tokens
    stats["total_output"] += output_tokens
    stats["request_count"] += 1

    if latency is None:
        return
    ttft = latency if ttft is None else ttft

    stats["total_latency"] += latency
    stats["total_ttft"] += ttft
    stats["latency_count"] += 1

    if searched is True:
        stats["search_latency"] += latency
        stats["search_ttft"] += ttft
        stats["search_count"] += 1
    elif searched is False:
        stats["nosearch_latency"] += latency
        stats["nosearch_ttft"] += ttft
        stats["nosearch_count"] += 1


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"データ読み込み失敗: {e}")
    return {
        "current_month": get_current_month(),
        "current": new_stats(),
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
            data.setdefault("history", {})
            data["history"][prev_month] = data["current"].copy()
            logger.info(f"月次リセット: {prev_month} を履歴に保存しました")
        data["current_month"] = current
        data["current"] = new_stats()
        save_data(data)
    data.setdefault("current", new_stats())
    data.setdefault("history", {})
    fill_missing(data["current"])
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


def match_to_sample(month: str, m: "re.Match") -> tuple:
    lat = m.group("latency")
    ttft = m.group("ttft")
    s = m.group("search")
    return (
        month,
        int(m.group("input")),
        int(m.group("output")),
        float(lat) if lat is not None else None,
        float(ttft) if ttft is not None else None,
        (s == "1") if s is not None else None,
    )


def apply_token_matches_to_data(matches_with_month, reset_current: bool):
    global token_data
    monthly = defaultdict(new_stats)
    for month, inp, out, lat, ttft, searched in matches_with_month:
        add_sample(monthly[month], inp, out, lat, ttft, searched)

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
            cur = fill_missing(token_data["current"])
            src = monthly[current_month]
            for k, v in src.items():
                cur[k] = cur.get(k, 0) + v

    save_data(token_data)
    return monthly


def append_token_log_line(
    input_tokens: int,
    output_tokens: int,
    latency: Optional[float] = None,
    ttft: Optional[float] = None,
    searched: Optional[bool] = None,
):
    try:
        line = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"[TOKEN USAGE] input={input_tokens}, output={output_tokens}"
        )
        if latency is not None:
            line += f", latency={latency:.3f}, ttft={(ttft if ttft is not None else latency):.3f}"
            if searched is not None:
                line += f", search={1 if searched else 0}"
        with open(TOKEN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error(f"トークンログ書き込み失敗: {e}")


def read_local_token_logs(days: int = 365) -> list:
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


def append_matched_lines_to_disk(raw_lines: list) -> int:
    if not raw_lines:
        return 0
    try:
        existing = set()
        if TOKEN_LOG_FILE.exists():
            with open(TOKEN_LOG_FILE, "r", encoding="utf-8") as f:
                existing = set(f.read().splitlines())

        new_lines = [line.strip() for line in raw_lines if line.strip() not in existing]
        if not new_lines:
            return 0

        with open(TOKEN_LOG_FILE, "a", encoding="utf-8") as f:
            for line in new_lines:
                f.write(line + "\n")
        return len(new_lines)
    except Exception as e:
        logger.error(f"過去ログのディスク書き込み失敗: {e}")
        return 0


def get_search_queries(chunk) -> Optional[list]:
    try:
        for cand in (chunk.candidates or []):
            gm = getattr(cand, "grounding_metadata", None)
            if not gm:
                continue
            queries = list(getattr(gm, "web_search_queries", None) or [])
            if queries:
                return queries
            if getattr(gm, "grounding_chunks", None):
                return []
    except Exception:
        pass
    return None


def chunk_used_search(chunk) -> bool:
    """このチャンクにGoogle検索(グラウンディング)の痕跡があるか"""
    return get_search_queries(chunk) is not None


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
        item = {**new_stats(), **stats}
        item["month"] = month
        history_list.append(item)
    history_list.sort(key=lambda x: x["month"], reverse=True)
    return history_list


@app.post("/api/token-stats/restore")
async def restore_from_logs(req: RestoreRequest):
    matches_with_month = []
    raw_lines = []

    for m in TOKEN_LOG_WITH_DATE_PATTERN.finditer(req.logs):
        matches_with_month.append(match_to_sample(m.group("date")[:7], m))
        raw_lines.append(m.group(0))
    if not matches_with_month:
        raise HTTPException(status_code=400, detail="TOKEN USAGE の行が見つかりませんでした")
    monthly = apply_token_matches_to_data(matches_with_month, reset_current=req.reset_current)

    written = append_matched_lines_to_disk(raw_lines)

    return {
        "restored_months": sorted(list(monthly.keys()), reverse=True),
        "matched_lines": len(matches_with_month),
        "written_to_disk": written,
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
        matches_with_month.append(match_to_sample(month, m))

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
            "長時間の推論を行わず素早く正確に返答する 返答は必ず250文字以内で生成する 検索ブラウジングの使用は1回リクエストごとに1回以下しか使用しない 知識に無い単語やコンテンツは類似単語を知っていても必ず検索して再試飲情報を確認する"
            "挨拶や短文のリクエストに対しては短く返答する"
            "矛盾や嘘を無くし不確かな情報はわかりませんと答える、会話をAI側か終わらせようとしない"
            "むやみに全肯定せず正しい意見伝える"
        )
        if data.custom_prompt and data.custom_prompt.strip():
            s += f"\n\n追加のプロンプト\n{data.custom_prompt.strip()}"
        ai_config = types.GenerateContentConfig(
            system_instruction=s,
            max_output_tokens=450,
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        def ndjson(obj: dict) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"

        async def event_generator():
            global token_data
            nonlocal full_reply
            usage_info = None
            used_search = False
            start = time.perf_counter()
            first_token_at = None
            try:

                yield ndjson({"status": "thinking"})

                async for chunk in await client.aio.models.generate_content_stream(
                    contents=talk,
                    model="gemini-3.1-flash-lite",
                    config=ai_config,
                ):

                    if not used_search:
                        queries = get_search_queries(chunk)
                        if queries is not None:
                            used_search = True
                            yield ndjson({"status": "searching", "queries": queries})

                    if chunk.text:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        full_reply += chunk.text
                        yield ndjson({"text": chunk.text})
                    if chunk.usage_metadata:
                        usage_info = chunk.usage_metadata

                end = time.perf_counter()
                latency = end - start
                ttft = (first_token_at - start) if first_token_at is not None else latency

                if usage_info:
                    token_data = ensure_current_month(token_data)
                    input_tokens = usage_info.prompt_token_count or 0
                    output_tokens = usage_info.candidates_token_count or 0

                    add_sample(
                        token_data["current"],
                        input_tokens,
                        output_tokens,
                        latency,
                        ttft,
                        used_search,
                    )
                    save_data(token_data)

                    append_token_log_line(input_tokens, output_tokens, latency, ttft, used_search)

                    logger.info(
                        f"[TOKEN USAGE] input={input_tokens}, "
                        f"output={output_tokens}, "
                        f"total={usage_info.total_token_count}, "
                        f"latency={latency:.3f}, ttft={ttft:.3f}, "
                        f"search={1 if used_search else 0}"
                    )
                else:
                    logger.info("[TOKEN USAGE] usage_metadata not found in stream")
                full_history.append({"role": "model", "parts": [{"text": full_reply}]})
                yield ndjson({"final_history": full_history})
            except Exception as e:
                logger.error(f"Stream Error: {e}")
                yield ndjson({"error": "Stream interrupted"})
                full_history.append({"role": "model", "parts": [{"text": full_reply}]})
                yield ndjson({"final_history": full_history})

        return StreamingResponse(
            event_generator(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  
            },
        )

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
