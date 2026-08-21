"""天氣技能：中央氣象署開放資料（F-C0032-001 一般天氣預報）。

設定（config.toml 或 settings.json）：
    [weather]
    api_key = "你的 CWA 授權碼"   # https://opendata.cwa.gov.tw/ 免費申請
    location = "臺北市"           # 預設地點

未設定 api_key 時本技能不攔截，語句交給 LLM 自由回應。
"""
from __future__ import annotations

import logging
import re

import httpx

from ..commands import CommandResult
from . import register_skill


LOGGER = logging.getLogger(__name__)

CWA_ENDPOINT = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"

# 觸發樣式：「明天會下雨嗎」「今天天氣如何」「臺北市氣溫」「週末天氣」
_WEATHER_RE = re.compile(
    r"(?:^|(?P<prefix>請問))?"
    r"(?:(今天|明天|後天|週末|周末)?"
    r"(?P<place>[^\s]{2,6}?)(?:的)?"
    r")?(天氣|下雨|下雪|氣溫|溫度|降雨|會不會雨|熱不熱|冷不熱)"
    r"(如何|怎麼樣|預報|呢|嗎|嘛)?[？?！!。]*$"
)

_WXI_LABEL = {
    "晴天": "☀️", "多雲": "⛅", "陰天": "☁️",
    "雨天": "🌧️", "陣雨": "🌧️", "雷雨": "⛈️",
}


def _describe_period(forecasts: dict[str, list[dict]], when: str) -> str | None:
    """從 CWA elements 取出指定時段的簡述；when: 今天/明天/後天。"""
    # CWA F-C0032-001 的 startTime 為 06:00 / 18:00 兩段
    day_shift = {"今天": 0, "明天": 1, "後天": 2}.get(when, 0)
    import datetime

    target_date = (datetime.datetime.now() + datetime.timedelta(days=day_shift)).date()
    for entry in forecasts.get("Wx", []):
        start = str(entry.get("startTime", ""))[:10]
        try:
            entry_date = datetime.date.fromisoformat(start)
        except ValueError:
            continue
        if entry_date == target_date:
            return str(entry.get("parameter", {}).get("parameterName", ""))
    return None


def _temperature_range(forecasts: dict[str, list[dict]], when: str) -> tuple[int, int] | None:
    import datetime

    day_shift = {"今天": 0, "明天": 1, "後天": 2}.get(when, 0)
    target_date = (datetime.datetime.now() + datetime.timedelta(days=day_shift)).date()
    lows, highs = [], []
    for key, bucket in (("MinT", lows), ("MaxT", highs)):
        for entry in forecasts.get(key, []):
            start = str(entry.get("startTime", ""))[:10]
            try:
                if datetime.date.fromisoformat(start) != target_date:
                    continue
                bucket.append(int(entry.get("parameter", {}).get("parameterName")))
            except (TypeError, ValueError):
                continue
    if not lows or not highs:
        return None
    return min(lows), max(highs)


def fetch_cwa_summary(api_key: str, location: str, when: str, *, client=None) -> str | None:
    """呼叫 CWA API 回傳口語摘要；失敗回傳 None。"""
    params = {
        "Authorization": api_key,
        "LocationName": location,
    }
    try:
        if client is not None:
            response = client.get(CWA_ENDPOINT, params=params)
        else:
            with httpx.Client(timeout=8.0) as own:
                response = own.get(CWA_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records", {})
        locations = records.get("location", [])
        if not locations:
            return None
        elements = {}
        for element in locations[0].get("weatherElement", []):
            elements[element.get("elementName", "")] = element.get("time", [])
        wx = _describe_period(elements, when)
        temp = _temperature_range(elements, when)
        parts = []
        if wx:
            icon = next((v for k, v in _WXI_LABEL.items() if k in wx), "")
            parts.append(f"{icon}{wx}" if icon else wx)
        if temp:
            parts.append(f"{temp[0]} 到 {temp[1]} 度")
        if not parts:
            return None
        rain_hint = ""
        if wx and ("雨" in wx):
            rain_hint = "記得帶傘喔！"
        elif temp and temp[1] >= 32:
            rain_hint = "天氣蠻熱的，多補充水分！"
        elif temp and temp[0] <= 15:
            rain_hint = "有點涼，穿暖一點！"
        return f"{location}{'明天' if when == '明天' else ''}的天氣：{'，'.join(parts)}。{rain_hint}"
    except Exception as error:
        LOGGER.warning("CWA 天氣查詢失敗：%s", error)
        return None


# CWA F-C0032-001 支援的縣市名（全名比對，避免「新竹」市/縣歧義）
_CWA_LOCATIONS = (
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "嘉義市", "新竹縣", "苗栗縣", "彰化縣",
    "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
)


def _detect_location(text: str, default: str) -> str:
    for name in _CWA_LOCATIONS:
        if name in text:
            return name
    return default


@register_skill("weather")
def weather_skill(text: str, controller) -> CommandResult | None:
    match = _WEATHER_RE.search(text)
    if not match:
        return None
    store = getattr(controller, "store", None)
    if store is None:
        return None
    api_key = str(store.value("weather.api_key") or "").strip()
    if not api_key:
        # 未設定金鑰：不攔截，讓 LLM 自由發揮
        return None
    location = str(store.value("weather.location") or "臺北市").strip() or "臺北市"
    target = _detect_location(text, location)
    when = match.group(2) or "今天"

    summary = fetch_cwa_summary(api_key, target, when)
    if summary is None:
        return CommandResult(handled=True, reply="我這邊連不上氣象資料，等一下再問我一次好嗎？", action="weather_failed")
    return CommandResult(handled=True, reply=summary, action="weather", details={"when": when})
