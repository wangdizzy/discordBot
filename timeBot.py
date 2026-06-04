import os
import json
import asyncio
import datetime
import aiohttp
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
import threading
import uvicorn

# ── 設定檔路徑 ────────────────────────────────────────
SETTINGS_FILE = "settings.json"
HOLIDAY_CACHE_FILE = "holiday_cache.json"

# ── 預設設定 ──────────────────────────────────────────
DEFAULT_SETTINGS = {
    "skip_tw_holidays": True,
    "custom_skip_dates": [],
    "force_send_dates": [],
    "scheduled_messages": []
}

# ── 讀寫設定 ──────────────────────────────────────────
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

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
bot_loop = None

async def send_scheduled_message(message: str):
    settings = load_settings()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    await ensure_holidays(today.year)

    if today_str in settings.get("force_send_dates", []):
        pass  # 強制發送
    elif today_str in settings.get("custom_skip_dates", []):
        print(f"[跳過-自訂] {today_str}")
        return
    elif settings.get("skip_tw_holidays", True) and is_tw_holiday(today):
        print(f"[跳過-假日] {today_str}")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(message)
        print(f"[已發送] {today_str} {message}")
    else:
        print(f"[錯誤] 找不到頻道 {CHANNEL_ID}")

def rebuild_schedules():
    """重新載入排程（儲存設定後呼叫）"""
    # 移除舊排程（保留假日更新排程）
    for job in scheduler.get_jobs():
        if job.id != "refresh_holidays":
            job.remove()

    settings = load_settings()
    messages = settings.get("scheduled_messages", [])

    DAYS_EN = ['mon','tue','wed','thu','fri','sat','sun']

    for idx, item in enumerate(messages):
        message = item["message"]
        repeat  = item.get("repeat", False)
        days_list = item.get("days", [True,True,True,True,True,False,False])
        # days 可能是 list of bool 或字串
        if isinstance(days_list, list):
            day_str = ",".join(DAYS_EN[i] for i, v in enumerate(days_list) if v)
        else:
            day_str = days_list
        if not day_str:
            day_str = "mon-fri"

        if not repeat:
            h = item.get("h_start", item.get("hour_start", 9))
            m = item.get("m_start", item.get("minute_start", 0))
            scheduler.add_job(
                send_scheduled_message,
                trigger=CronTrigger(day_of_week=day_str, hour=h, minute=m, timezone="Asia/Taipei"),
                args=[message], id=f"msg_{idx}_{h:02d}_{m:02d}", replace_existing=True,
            )
            print(f"[排程-單次] {day_str} {h:02d}:{m:02d} → {message}")
        else:
            h_s = item.get("h_start", item.get("hour_start", 9))
            m_s = item.get("m_start", item.get("minute_start", 0))
            h_e = item.get("h_end",   item.get("hour_end",   18))
            m_e = item.get("m_end",   item.get("minute_end",  0))
            iv  = item.get("interval", item.get("interval_minutes", 60))
            cur = h_s * 60 + m_s
            end = h_e * 60 + m_e
            while cur <= end:
                h, m = cur // 60, cur % 60
                scheduler.add_job(
                    send_scheduled_message,
                    trigger=CronTrigger(day_of_week=day_str, hour=h, minute=m, timezone="Asia/Taipei"),
                    args=[message], id=f"msg_{idx}_{h:02d}_{m:02d}", replace_existing=True,
                )
                print(f"[排程-重複] {day_str} {h:02d}:{m:02d} → {message}")
                cur += iv

    print(f"[排程] 重新載入完成，共 {len(scheduler.get_jobs())-1} 個排程")

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
        await ctx.send("目前沒有排程。")
        return
    lines = ["**目前排程：**"]
    for job in jobs:
        lines.append(f"- `{job.id}` 下次：{job.next_run_time}")
    await ctx.send("\n".join(lines))

# ── FastAPI ───────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class Settings(BaseModel):
    skip_tw_holidays: bool = True
    custom_skip_dates: List[str] = []
    force_send_dates: List[str] = []
    scheduled_messages: List[dict] = []

@app.get("/api/settings")
def get_settings():
    return load_settings()

@app.post("/api/settings")
def post_settings(body: Settings):
    data = body.dict()
    save_settings(data)
    # 在 bot 的 event loop 重建排程
    if bot_loop and bot_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            asyncio.coroutine(lambda: rebuild_schedules())(),
            bot_loop
        )
    rebuild_schedules()
    return {"status": "ok", "message": "設定已儲存，排程已更新"}

@app.get("/api/status")
def get_status():
    jobs = [j for j in scheduler.get_jobs() if j.id != "refresh_holidays"]
    return {
        "bot_online": not bot.is_closed(),
        "schedule_count": len(jobs),
        "next_jobs": [
            {"id": j.id, "next_run": str(j.next_run_time)}
            for j in jobs[:5]
        ]
    }

# 提供 HTML 靜態頁面
@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")

# ── 啟動兩個服務 ──────────────────────────────────────
def run_fastapi():
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

async def run_bot():
    await bot.start(TOKEN)

if __name__ == "__main__":
    # FastAPI 跑在背景 thread，Bot 跑在主 event loop
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()
    asyncio.run(run_bot())