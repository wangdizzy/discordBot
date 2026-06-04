import os
import io
import json
import asyncio
import datetime
import aiohttp
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import List, Optional
import threading
import uvicorn

# ── 設定檔路徑 ────────────────────────────────────────
SETTINGS_FILE = "settings.json"
HOLIDAY_CACHE_FILE = "holiday_cache.json"

DEFAULT_SETTINGS = {
    "skip_tw_holidays": True,
    "custom_skip_dates": [],
    "force_send_dates": [],
    "scheduled_messages": []
}

def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_SETTINGS.copy()

def save_settings(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── 假日快取 ──────────────────────────────────────────
holiday_cache = {}

def load_holiday_cache():
    global holiday_cache
    if os.path.exists(HOLIDAY_CACHE_FILE):
        with open(HOLIDAY_CACHE_FILE, "r", encoding="utf-8") as f:
            holiday_cache = json.load(f)

def save_holiday_cache():
    with open(HOLIDAY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(holiday_cache, f, ensure_ascii=False)

async def fetch_holidays(year: int):
    url = f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for entry in data:
                        if entry.get("date"):
                            holiday_cache[entry["date"]] = entry.get("isHoliday", False)
                    save_holiday_cache()
                    print(f"[假日] {year} 年資料載入完成")
    except Exception as e:
        print(f"[假日] {year} 年載入失敗：{e}")

async def ensure_holidays(year: int):
    if not any(k.startswith(str(year)) for k in holiday_cache):
        await fetch_holidays(year)

def is_tw_holiday(date: datetime.date) -> bool:
    return holiday_cache.get(date.strftime("%Y%m%d"), False)

# ── Discord Bot ───────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
IMAGE_CHANNEL_ID = int(os.getenv("IMAGE_CHANNEL_ID", "0"))  # 圖片存放頻道

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
bot_loop = None

# ── 上傳圖片到 Discord CDN ────────────────────────────
async def upload_image_to_discord(filename: str, data: bytes) -> str:
    """上傳圖片到 Discord 圖片頻道，回傳 CDN URL"""
    if IMAGE_CHANNEL_ID == 0:
        raise ValueError("IMAGE_CHANNEL_ID 尚未設定")
    channel = bot.get_channel(IMAGE_CHANNEL_ID)
    if not channel:
        raise ValueError(f"找不到圖片頻道 {IMAGE_CHANNEL_ID}")
    file = discord.File(fp=io.BytesIO(data), filename=filename)
    msg = await channel.send(file=file)
    url = msg.attachments[0].url
    print(f"[圖片] 上傳完成：{url}")
    return url

# ── 發送訊息 ──────────────────────────────────────────
async def send_scheduled_message(message: str, image_url: str = None):
    settings = load_settings()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    await ensure_holidays(today.year)

    if today_str in settings.get("force_send_dates", []):
        pass
    elif today_str in settings.get("custom_skip_dates", []):
        print(f"[跳過-自訂] {today_str}")
        return
    elif settings.get("skip_tw_holidays", True) and is_tw_holiday(today):
        print(f"[跳過-假日] {today_str}")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[錯誤] 找不到頻道 {CHANNEL_ID}")
        return

    # 有圖片：用 embed 同時發文字+圖片
    if image_url:
        if message:
            # 文字 + 圖片
            embed = discord.Embed(description=message)
            embed.set_image(url=image_url)
            await channel.send(embed=embed)
        else:
            # 只有圖片
            embed = discord.Embed()
            embed.set_image(url=image_url)
            await channel.send(embed=embed)
    else:
        # 只有文字
        await channel.send(message)

    print(f"[已發送] {today_str} | 訊息:{message} | 圖片:{image_url or '無'}")

# ── 排程設定 ─────────────────────────────────────────
def rebuild_schedules():
    for job in scheduler.get_jobs():
        if job.id != "refresh_holidays":
            job.remove()

    settings = load_settings()
    messages = settings.get("scheduled_messages", [])
    DAYS_EN = ['mon','tue','wed','thu','fri','sat','sun']

    for idx, item in enumerate(messages):
        message   = item.get("message", "")
        image_url = item.get("image_url", None)
        repeat    = item.get("repeat", False)
        days_list = item.get("days", [True,True,True,True,True,False,False])

        if isinstance(days_list, list):
            day_str = ",".join(DAYS_EN[i] for i, v in enumerate(days_list) if v)
        else:
            day_str = days_list
        if not day_str:
            day_str = "mon-fri"

        if not repeat:
            h = item.get("h_start", 9)
            m = item.get("m_start", 0)
            scheduler.add_job(
                send_scheduled_message,
                trigger=CronTrigger(day_of_week=day_str, hour=h, minute=m, timezone="Asia/Taipei"),
                args=[message, image_url], id=f"msg_{idx}_{h:02d}_{m:02d}", replace_existing=True,
            )
            print(f"[排程-單次] {day_str} {h:02d}:{m:02d} | 訊息:{message} | 圖片:{'有' if image_url else '無'}")
        else:
            h_s = item.get("h_start", 9);  m_s = item.get("m_start", 0)
            h_e = item.get("h_end",  18);  m_e = item.get("m_end",   0)
            iv  = item.get("interval", 60)
            cur = h_s * 60 + m_s
            end = h_e * 60 + m_e
            while cur <= end:
                h, m = cur // 60, cur % 60
                scheduler.add_job(
                    send_scheduled_message,
                    trigger=CronTrigger(day_of_week=day_str, hour=h, minute=m, timezone="Asia/Taipei"),
                    args=[message, image_url], id=f"msg_{idx}_{h:02d}_{m:02d}", replace_existing=True,
                )
                cur += iv
            print(f"[排程-重複] {day_str} {h_s:02d}:{m_s:02d}~{h_e:02d}:{m_e:02d} 每{iv}分 | 圖片:{'有' if image_url else '無'}")

    jobs = [j for j in scheduler.get_jobs() if j.id != "refresh_holidays"]
    print(f"[排程] 重新載入完成，共 {len(jobs)} 個")

# ── Bot 事件 ──────────────────────────────────────────
@bot.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_event_loop()
    print(f"[Bot] 上線：{bot.user}")
    load_holiday_cache()
    today = datetime.date.today()
    await ensure_holidays(today.year)
    await ensure_holidays(today.year + 1)
    rebuild_schedules()
    scheduler.add_job(
        lambda: asyncio.create_task(fetch_holidays(datetime.date.today().year + 1)),
        trigger=CronTrigger(month=1, day=1, hour=0, minute=5, timezone="Asia/Taipei"),
        id="refresh_holidays", replace_existing=True,
    )
    scheduler.start()
    print("[Bot] 排程器啟動")

@bot.command(name="test")
async def test_cmd(ctx):
    await ctx.send("✅ 機器人運作正常")

@bot.command(name="schedule")
async def schedule_cmd(ctx):
    jobs = [j for j in scheduler.get_jobs() if j.id != "refresh_holidays"]
    if not jobs:
        await ctx.send("目前沒有排程。"); return
    lines = ["**目前排程：**"]
    for job in jobs:
        lines.append(f"- `{job.id}` 下次：{job.next_run_time}")
    await ctx.send("\n".join(lines))

# ── FastAPI ───────────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Settings(BaseModel):
    skip_tw_holidays: bool = True
    custom_skip_dates: List[str] = []
    force_send_dates: List[str] = []
    scheduled_messages: List[dict] = []

@app.get("/")
def serve_ui():
    base = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(base, "timeBot.html"))

@app.head("/")
def head_ui():
    return Response()

@app.get("/api/settings")
def get_settings():
    return load_settings()

@app.post("/api/settings")
def post_settings(body: Settings):
    save_settings(body.dict())
    rebuild_schedules()
    return {"status": "ok", "message": "設定已儲存，排程已更新"}

@app.get("/api/status")
def get_status():
    jobs = [j for j in scheduler.get_jobs() if j.id != "refresh_holidays"]
    return {
        "bot_online": not bot.is_closed(),
        "schedule_count": len(jobs),
        "next_jobs": [{"id": j.id, "next_run": str(j.next_run_time)} for j in jobs[:5]]
    }

@app.post("/api/upload-image/{schedule_idx}")
async def upload_image(schedule_idx: int, file: UploadFile = File(...)):
    """上傳圖片到 Discord CDN，回傳 URL 並更新設定檔"""
    if IMAGE_CHANNEL_ID == 0:
        raise HTTPException(status_code=400, detail="IMAGE_CHANNEL_ID 尚未在環境變數中設定")

    # 檢查檔案類型
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只接受圖片檔案")

    data = await file.read()

    # 上傳到 Discord（需要在 bot event loop 執行）
    if not bot_loop or not bot_loop.is_running():
        raise HTTPException(status_code=500, detail="Bot 尚未上線")

    future = asyncio.run_coroutine_threadsafe(
        upload_image_to_discord(file.filename, data),
        bot_loop
    )
    try:
        url = future.result(timeout=15)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上傳失敗：{str(e)}")

    # 更新 settings.json 對應排程的 image_url
    settings = load_settings()
    msgs = settings.get("scheduled_messages", [])
    if 0 <= schedule_idx < len(msgs):
        msgs[schedule_idx]["image_url"] = url
        save_settings(settings)
        rebuild_schedules()

    return {"status": "ok", "url": url}

@app.delete("/api/upload-image/{schedule_idx}")
def delete_image(schedule_idx: int):
    """移除排程的圖片"""
    settings = load_settings()
    msgs = settings.get("scheduled_messages", [])
    if 0 <= schedule_idx < len(msgs):
        msgs[schedule_idx].pop("image_url", None)
        save_settings(settings)
        rebuild_schedules()
    return {"status": "ok"}

# ── 啟動 ─────────────────────────────────────────────
def run_fastapi():
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

async def run_bot():
    await bot.start(TOKEN)

if __name__ == "__main__":
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()
    asyncio.run(run_bot())