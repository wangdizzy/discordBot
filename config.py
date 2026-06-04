# ── 假日設定 ──────────────────────────────────────────
SKIP_TW_HOLIDAYS = True  # 台灣國定假日自動跳過
 
# 自訂跳過日期（格式：YYYY-MM-DD）
CUSTOM_SKIP_DATES = []
 
# 強制發送日期（即使假日也發送，優先於所有跳過設定）
FORCE_SEND_DATES = []

# ── 排程訊息 ──────────────────────────────────────────
SCHEDULED_MESSAGES = [
    {"hour": 8,  "minute": 30,  "message": "上班時間到了，記得打卡喔"},
    {"hour": 12, "minute": 0,  "message": "午餐時間到囉 🍱"},
    {"hour": 18, "minute": 0,  "message": "下班時間到了，記得打卡喔"},
]

SCHEDULED_MESSAGES = [
    {
        "hour_start": 8,
        "minute_start": 30,
        "hour_end": 8,
        "minute_end": 59,
        "interval_minutes": 15,
        "repeat": True,
        "message": "上班時間到了，記得打卡喔@everyone"
    },
    {
        "hour_start": 8,
        "minute_start": 55,
        "hour_end": 8,
        "minute_end": 55,
        "interval_minutes": 0,
        "repeat": False,
        "message": "最後提醒，記得打上班卡喔@everyone"
    },
    {
        "hour_start": 18,
        "minute_start": 0,
        "hour_end": 18,
        "minute_end": 29,
        "interval_minutes": 15,
        "repeat": True,
        "message": "下班時間到了，記得打卡喔@everyone"
    },
    {
        "hour_start": 18,
        "minute_start": 25,
        "hour_end": 18,
        "minute_end": 25,
        "interval_minutes": 0,
        "repeat": False,
        "message": "最後提醒，記得打下班卡喔@everyone"
    }
  
]