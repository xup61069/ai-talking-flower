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


def parse_duration_to_seconds(text: str) -> float | None:
    """將口語時間字串（如『5分鐘』、『半小時』、『1小時』、『30秒』）解析為秒數。"""
    text = text.strip()
    # 秒
    m_sec = re.search(r"(\d+|[一二兩三四五六七八九十]+)\s*秒", text)
    if m_sec:
        val = _to_num(m_sec.group(1))
        return val if val else None

    # 小時
    m_hr = re.search(r"(半|\d+|[一二兩三四五六七八九十]+)\s*(?:個)?小時", text)
    if m_hr:
        val = _to_num(m_hr.group(1))
        return (val * 3600) if val else None

    # 分鐘
    m_min = re.search(r"(\d+|[一二兩三四五六七八九十]+|十五|二十|三十|四十|五十)\s*分鐘?", text)
    if m_min:
        val = _to_num(m_min.group(1))
        return (val * 60) if val else None

    return None


def _to_num(s: str) -> float:
    if s in CHINESE_NUMS:
        return float(CHINESE_NUMS[s])
    try:
        return float(s)
    except ValueError:
        return 0.0


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
            time_desc = now.strftime(f"%H點%M分")
            date_desc = now.strftime(f"%m月%d日 {weekdays[now.weekday()]}")
            if "星期" in text or "幾號" in text or "日期" in text:
                reply = f"今天是{date_desc}，現在時間是{time_desc}喔！"
            else:
                reply = f"現在時間是{time_desc}喔！"
            return CommandResult(handled=True, reply=reply, action="time_query")

        # 2. 定時提醒指令（如「5分鐘後提醒我喝水」、「半小時後叫我開會」）
        remind_match = re.search(
            r"^(?:幫我)?(?:設定|設)?(?:在)?((?:\d+|[一二兩三四五六七八九十半]+)\s*(?:(?:個)?小時|分鐘?|秒))\s*(?:之)?後\s*(?:提醒我|叫我|通知我)\s*(.+?)[。！!？?]*$",
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
                reply = f"好的，音量已為你調大囉（目前 {new_vol}%）！"
                return CommandResult(handled=True, reply=reply, action="volume_up")
        elif re.search(r"(小聲一點|音量調小|聲音太大|小點聲)", text):
            if controller.live is not None:
                current_vol = getattr(controller.live, "volume", 100)
                new_vol = max(10, current_vol - 15)
                controller.live.volume = new_vol
                reply = f"好的，音量已為你調小囉（目前 {new_vol}%）！"
                return CommandResult(handled=True, reply=reply, action="volume_down")

        # 5. 語速調整指令
        if re.search(r"(講話快一點|說話快一點|語速調快|講快點)", text):
            if controller.live is not None:
                current_spd = getattr(controller.live, "speed", 0.9)
                new_spd = min(1.5, round(current_spd + 0.1, 2))
                controller.live.speed = new_spd
                reply = f"好喔！語速已加快到 {new_spd} 倍速～"
                return CommandResult(handled=True, reply=reply, action="speed_up")
        elif re.search(r"(講話慢一點|說話慢一點|語速調慢|講慢點)", text):
            if controller.live is not None:
                current_spd = getattr(controller.live, "speed", 0.9)
                new_spd = max(0.5, round(current_spd - 0.1, 2))
                controller.live.speed = new_spd
                reply = f"好的～語速已放慢到 {new_spd} 倍速囉。"
                return CommandResult(handled=True, reply=reply, action="speed_down")

        return CommandResult(handled=False)
