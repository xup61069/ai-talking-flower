from __future__ import annotations

import datetime
import re
from typing import NamedTuple

from .personas import get_persona_by_id, PERSONA_PRESETS


class CommandResult(NamedTuple):
    handled: bool
    reply: str = ""
    action: str = ""
    details: dict = {}


CHINESE_NUMS = {
    "半": 0.5,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十五": 15,
    "二十": 20,
    "三十": 30,
    "四十": 40,
    "五十": 50,
}

_DIGITS = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_NUM_CHARS = "一二兩三四五六七八九十"


def parse_duration_to_seconds(text: str) -> float | None:
    """將口語時間字串（如『5分鐘』、『半小時』、『1小時』、『30秒』、『二十五分鐘』、『一個半小時』）解析為秒數。"""
    text = text.strip()
    # 秒（含中文 1..99）
    m_sec = re.search(rf"(\d+|[{_CHINESE_NUM_CHARS}]+)\s*秒", text)
    if m_sec:
        val = _to_num(m_sec.group(1))
        return val if val else None

    # 小時：先處理「一個半小時 / 2個半小時」
    m_half_hr = re.search(rf"(\d+|[{_CHINESE_NUM_CHARS}]+)\s*個半\s*(?:小時|鐘)", text)
    if m_half_hr:
        val = _to_num(m_half_hr.group(1))
        if val:
            return (val + 0.5) * 3600

    # 小時一般（含「半小時」）
    m_hr = re.search(rf"(半|\d+|[{_CHINESE_NUM_CHARS}]+)\s*(?:個)?\s*小時", text)
    if m_hr:
        val = _to_num(m_hr.group(1))
        return (val * 3600) if val else None

    # 分鐘
    m_min = re.search(rf"(\d+|[{_CHINESE_NUM_CHARS}]+)\s*分鐘?", text)
    if m_min:
        val = _to_num(m_min.group(1))
        return (val * 60) if val else None

    return None


def _to_num(s: str) -> float:
    s = s.strip()
    if s in CHINESE_NUMS:
        return float(CHINESE_NUMS[s])
    try:
        # 純數字
        return float(s)
    except ValueError:
        pass
    # 中文 11-99 合成，如 二十五 / 十五 / 二十 / 十一
    if not s:
        return 0.0
    # 若含「十」
    if "十" in s:
        if s == "十":
            return 10.0
        if s.startswith("十"):
            # 十一 .. 十九
            suffix = s[1:]
            if not suffix:
                return 10.0
            # 僅取首字，避免「十一分鐘」被誤判多字
            return 10.0 + float(_DIGITS.get(suffix[0], 0))
        # 二十 / 二十一 / 三十五 等
        idx = s.index("十")
        tens_char = s[idx - 1] if idx >= 1 else ""
        tens = _DIGITS.get(tens_char, 0) * 10 if tens_char else 0
        after = s[idx + 1 :]
        if not after:
            return float(tens)
        # 如 二十五 取 五
        ones = _DIGITS.get(after[0], 0)
        return float(tens + ones)
    # 單字
    if s in _DIGITS:
        return float(_DIGITS[s])
    return 0.0


def parse_absolute_time(text: str, *, now: datetime.datetime | None = None):
    """將中文絕對時間（如「明天早上八點半」「每天晚上九點」「下午三點半」）解析為 (epoch, repeat_daily, hhmm)。

    回傳 None 表示無法解析。支援：
    - 相對日期詞：今天/明天/後天/每天/每日
    - 時段：早上/上午/中午/下午/傍晚/晚上
    - 「八點半」「八點三十分」19:30
    """
    now = now or datetime.datetime.now()
    text = text.strip()

    # 24 小時制數字
    m_clock = re.search(r"(\d{1,2}):(\d{2})", text)
    hh_mm: tuple[int, int] | None = None
    if m_clock:
        candidate = (int(m_clock.group(1)), int(m_clock.group(2)))
        if 0 <= candidate[0] < 24 and 0 <= candidate[1] < 60:
            hh_mm = candidate

    # 中文「八點半 / 八點三十分 / 八點」
    if hh_mm is None:
        m_cn = re.search(rf"([{_CHINESE_NUM_CHARS}]+|\d+)\s*點(?:\s*(半|(\d+|[{_CHINESE_NUM_CHARS}]+))\s*分?)?", text)
        if not m_cn:
            return None
        hour_val = _to_num(m_cn.group(1))
        if hour_val <= 0 or hour_val >= 24:
            return None
        minute = 0
        if m_cn.group(2):
            if m_cn.group(2) == "半":
                minute = 30
            elif m_cn.group(3):
                minute = int(_to_num(m_cn.group(3)))
        hh_mm = (int(hour_val), minute)

    # 日期詞
    day_offset = 0
    repeat_daily = False
    if re.search(r"每\s*[天日]", text):
        repeat_daily = True
    elif "後天" in text:
        day_offset = 2
    elif "明天" in text:
        day_offset = 1

    # 時段偏移
    period = ""
    for p in ("凌晨", "清晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜晚"):
        if p in text:
            period = p
            break

    hh, mm = hh_mm
    if period in ("下午", "傍晚", "晚上", "夜晚") and hh < 12:
        hh += 12
    elif period == "中午" and hh <= 2:
        hh += 12
    elif not period and hh < 8:
        # 口語慣例：無時段的「三點」多指下午三點
        hh += 12

    target_date = (now + datetime.timedelta(days=day_offset)).date()
    try:
        target_dt = datetime.datetime.combine(target_date, datetime.time(hour=hh % 24, minute=mm))
    except ValueError:
        return None

    if repeat_daily and target_dt <= now:
        target_dt += datetime.timedelta(days=1)

    return target_dt.timestamp(), repeat_daily, f"{hh % 24:02d}:{mm:02d}"


class VoiceCommander:
    """即時語音指令識別器：秒級攔截時間查詢、定時提醒、性格切換、音量調整等明確指令。"""

    def try_execute(self, user_text: str, controller) -> CommandResult:
        text = user_text.strip()
        if not text:
            return CommandResult(handled=False)

        # 1. 時間 / 日期查詢
        if re.search(r"^(現在幾點|現在時間|報時|幾點了|今天星期幾|今天幾號|現在日期)[？?！!。]*$", text):
            now = datetime.datetime.now()
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            time_desc = now.strftime("%H點%M分")
            date_desc = now.strftime(f"%m月%d日 {weekdays[now.weekday()]}")
            if "星期" in text or "幾號" in text or "日期" in text:
                reply = f"今天是{date_desc}，現在時間是{time_desc}喔！"
            else:
                reply = f"現在時間是{time_desc}喔！"
            return CommandResult(handled=True, reply=reply, action="time_query")

        # 2. 定時提醒指令（相對時間：「5分鐘後提醒我喝水」）
        remind_match = re.search(
            r"^(?:幫我)?(?:設定|設)?(?:在)?((?:半|\d+個半|\d+|[一二兩三四五六七八九十]+個半|[一二兩三四五六七八九十半]+)\s*(?:小時|分鐘?|秒))\s*(?:之)?後\s*(?:提醒我|叫我|通知我)\s*(.+?)[。！!？?]*$",
            text,
        )
        if remind_match:
            time_part = remind_match.group(1)
            task_part = remind_match.group(2).strip()
            seconds = parse_duration_to_seconds(time_part)
            if seconds and seconds > 0 and task_part and controller.reminders:
                reminder = controller.reminders.add(task_part, seconds)
                if controller.bus is not None:
                    controller.bus.publish({"type": "reminder_added", "reminder": reminder.to_dict()})
                reply = f"沒問題！已幫你設定 {time_part} 後提醒：{task_part} ⏰"
                return CommandResult(
                    handled=True,
                    reply=reply,
                    action="add_reminder",
                    details={"text": task_part, "seconds": seconds},
                )

        # 2b. 絕對時間提醒（「明天早上八點半叫我起床」「每天晚上九點提醒我吃藥」）
        absolute_match = re.search(
            r"^(每\s*[天日]|今天|明天|後天)?\s*(早上|上午|中午|下午|傍晚|晚上|夜晚|凌晨|清晨)?\s*([0-9]{1,2}:[0-9]{2}|[一二兩三四五六七八九十半]+\s*點(?:半|\s*(?:[0-9]+|[一二兩三四五六七八九十]+)?\s*分?)?)\s*(?:的時候|左右)?\s*(?:提醒我|叫我|通知我)\s*(.+?)[。！!？?]*$",
            text,
        )
        if absolute_match and controller.reminders:
            parsed = parse_absolute_time(text)
            if parsed is not None:
                epoch, repeat_daily, hhmm = parsed
                task_part = absolute_match.group(4).strip()
                if task_part:
                    reminder = controller.reminders.add_absolute(
                        task_part, epoch,
                        repeat_daily_hhmm=hhmm if repeat_daily else "",
                    )
                    if controller.bus is not None:
                        controller.bus.publish({"type": "reminder_added", "reminder": reminder.to_dict()})
                    prefix = "每天" if repeat_daily else "已排程"
                    reply = f"好喔！{prefix}{hhmm.replace(':', '點')}分{'會' if repeat_daily else ''}提醒你：{task_part} ⏰"
                    return CommandResult(
                        handled=True,
                        reply=reply,
                        action="add_reminder",
                        details={"text": task_part, "at": hhmm, "repeat": repeat_daily},
                    )

        # 3. 性格切換指令
        persona_match = re.search(
            r"^(?:切換(?:到|成)?|換(?:成|回)?)(?:模式)?\s*(夜間|溫柔|工作|摸魚|上班|辦公|吐槽|搞笑|機智|元氣|預設|日常)(?:模式|花花)?[。！!？?]*$",
            text,
        )
        if persona_match:
            keyword = persona_match.group(1)
            target_id = "energetic"
            if keyword in ("夜間", "溫柔"):
                target_id = "night"
            elif keyword in ("工作", "摸魚", "上班", "辦公"):
                target_id = "work_buddy"
            elif keyword in ("吐槽", "搞笑", "機智"):
                target_id = "snarky"
            elif keyword in ("元氣", "預設", "日常"):
                target_id = "energetic"

            preset = get_persona_by_id(target_id) or PERSONA_PRESETS[0]
            if controller.live is not None:
                controller.live.set("llm.persona", preset.persona)
                controller.live.set("llm.temperature", preset.temperature)
                controller.live.set("llm.top_p", preset.top_p)
                controller.live.set("tts.speed", preset.speed)
                if preset.idle_prompt:
                    controller.live.set("idle_chat.prompt", preset.idle_prompt)
                controller.live.persona_preset = preset.id
            # 同步持久化，避免重啟後人設回溯（統一走 profile.persona_preset）
            store = getattr(controller, "store", None)
            if store is not None:
                try:
                    store.set("llm.persona", preset.persona)
                    store.set("llm.temperature", preset.temperature)
                    store.set("llm.top_p", preset.top_p)
                    store.set("tts.speed", preset.speed)
                    if preset.idle_prompt:
                        store.set("idle_chat.prompt", preset.idle_prompt)
                    store.set("profile.persona_preset", preset.id)
                except (KeyError, TypeError, ValueError):
                    pass
            if controller.bus is not None:
                controller.bus.publish({"type": "persona_changed", "id": preset.id, "name": preset.name})
            reply = f"收到！已切換到「{preset.name}」模式囉～🌸"
            return CommandResult(handled=True, reply=reply, action="switch_persona", details={"id": target_id})

        # 4. 音量調整指令
        if re.search(r"(大聲一點|音量調大|聲音太小|大點聲)", text):
            if controller.live is not None:
                current_vol = getattr(controller.live, "volume", 100)
                new_vol = min(100, current_vol + 15)
                controller.live.volume = new_vol
                store = getattr(controller, "store", None)
                if store is not None:
                    try:
                        store.set("tts.volume", new_vol)
                    except (KeyError, TypeError, ValueError):
                        pass
                reply = f"好的，音量已為你調大囉（目前 {new_vol}%）！"
                return CommandResult(handled=True, reply=reply, action="volume_up")
        elif re.search(r"(小聲一點|音量調小|聲音太大|小點聲)", text):
            if controller.live is not None:
                current_vol = getattr(controller.live, "volume", 100)
                new_vol = max(10, current_vol - 15)
                controller.live.volume = new_vol
                store = getattr(controller, "store", None)
                if store is not None:
                    try:
                        store.set("tts.volume", new_vol)
                    except (KeyError, TypeError, ValueError):
                        pass
                reply = f"好的，音量已為你調小囉（目前 {new_vol}%）！"
                return CommandResult(handled=True, reply=reply, action="volume_down")

        # 5. 語速調整指令
        if re.search(r"(講話快一點|說話快一點|語速調快|講快點)", text):
            if controller.live is not None:
                current_spd = getattr(controller.live, "speed", 0.9)
                new_spd = min(1.5, round(current_spd + 0.1, 2))
                controller.live.speed = new_spd
                store = getattr(controller, "store", None)
                if store is not None:
                    try:
                        store.set("tts.speed", new_spd)
                    except (KeyError, TypeError, ValueError):
                        pass
                reply = f"好喔！語速已加快到 {new_spd} 倍速～"
                return CommandResult(handled=True, reply=reply, action="speed_up")
        elif re.search(r"(講話慢一點|說話慢一點|語速調慢|講慢點)", text):
            if controller.live is not None:
                current_spd = getattr(controller.live, "speed", 0.9)
                new_spd = max(0.5, round(current_spd - 0.1, 2))
                controller.live.speed = new_spd
                store = getattr(controller, "store", None)
                if store is not None:
                    try:
                        store.set("tts.speed", new_spd)
                    except (KeyError, TypeError, ValueError):
                        pass
                reply = f"好的～語速已放慢到 {new_spd} 倍速囉。"
                return CommandResult(handled=True, reply=reply, action="speed_down")

        return CommandResult(handled=False)
