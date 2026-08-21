"""內建技能：自 VoiceCommander 遷移的五組直達指令。

行為與遷移前完全一致；差異僅在於改為 register_skill 裝飾的獨立 handler，
之後新增指令（番茄鐘、日曆、OBS…）都是新檔案，不再動這裡。
"""
from __future__ import annotations

import datetime
import re

from ..commands import (
    CommandResult,
    parse_absolute_time,
    parse_duration_to_seconds,
)
from ..personas import get_persona_by_id, PERSONA_PRESETS
from . import register_skill


def _persist_setting(controller, path: str, value: object) -> None:
    store = getattr(controller, "store", None)
    if store is None:
        return
    try:
        store.set(path, value)
    except (KeyError, TypeError, ValueError):
        pass


@register_skill("time_query")
def _time_query(text: str, controller) -> CommandResult | None:
    if not re.search(r"^(現在幾點|現在時間|報時|幾點了|今天星期幾|今天幾號|現在日期)[？?！!。]*$", text):
        return None
    now = datetime.datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    time_desc = now.strftime("%H點%M分")
    date_desc = now.strftime(f"%m月%d日 {weekdays[now.weekday()]}")
    if "星期" in text or "幾號" in text or "日期" in text:
        reply = f"今天是{date_desc}，現在時間是{time_desc}喔！"
    else:
        reply = f"現在時間是{time_desc}喔！"
    return CommandResult(handled=True, reply=reply, action="time_query")


@register_skill("relative_reminder")
def _relative_reminder(text: str, controller) -> CommandResult | None:
    match = re.search(
        r"^(?:幫我)?(?:設定|設)?(?:在)?((?:半|\d+個半|\d+|[一二兩三四五六七八九十]+個半|[一二兩三四五六七八九十半]+)\s*(?:小時|分鐘?|秒))\s*(?:之)?後\s*(?:提醒我|叫我|通知我)\s*(.+?)[。！!？?]*$",
        text,
    )
    if not match or controller.reminders is None:
        return None
    time_part = match.group(1)
    task_part = match.group(2).strip()
    seconds = parse_duration_to_seconds(time_part)
    if not (seconds and seconds > 0 and task_part):
        return None
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


@register_skill("absolute_reminder")
def _absolute_reminder(text: str, controller) -> CommandResult | None:
    match = re.search(
        r"^(每\s*[天日]|今天|明天|後天)?\s*(早上|上午|中午|下午|傍晚|晚上|夜晚|凌晨|清晨)?\s*([0-9]{1,2}:[0-9]{2}|[一二兩三四五六七八九十半]+\s*點(?:半|\s*(?:[0-9]+|[一二兩三四五六七八九十]+)?\s*分?)?)\s*(?:的時候|左右)?\s*(?:提醒我|叫我|通知我)\s*(.+?)[。！!？?]*$",
        text,
    )
    if not match or controller.reminders is None:
        return None
    parsed = parse_absolute_time(text)
    if parsed is None:
        return None
    epoch, repeat_daily, hhmm = parsed
    task_part = match.group(4).strip()
    if not task_part:
        return None
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


_PERSONA_KEYWORDS = {
    "夜間": "night", "溫柔": "night",
    "工作": "work_buddy", "摸魚": "work_buddy", "上班": "work_buddy", "辦公": "work_buddy",
    "吐槽": "snarky", "搞笑": "snarky", "機智": "snarky",
    "元氣": "energetic", "預設": "energetic", "日常": "energetic",
}


@register_skill("switch_persona")
def _switch_persona(text: str, controller) -> CommandResult | None:
    match = re.search(
        r"^(?:切換(?:到|成)?|換(?:成|回)?)(?:模式)?\s*(夜間|溫柔|工作|摸魚|上班|辦公|吐槽|搞笑|機智|元氣|預設|日常)(?:模式|花花)?[。！!？?]*$",
        text,
    )
    if not match:
        return None
    target_id = _PERSONA_KEYWORDS.get(match.group(1), "energetic")
    preset = get_persona_by_id(target_id) or PERSONA_PRESETS[0]
    if controller.live is not None:
        controller.live.set("llm.persona", preset.persona)
        controller.live.set("llm.temperature", preset.temperature)
        controller.live.set("llm.top_p", preset.top_p)
        controller.live.set("tts.speed", preset.speed)
        if preset.idle_prompt:
            controller.live.set("idle_chat.prompt", preset.idle_prompt)
        controller.live.persona_preset = preset.id
    for path, value in (
        ("llm.persona", preset.persona),
        ("llm.temperature", preset.temperature),
        ("llm.top_p", preset.top_p),
        ("tts.speed", preset.speed),
        ("idle_chat.prompt", preset.idle_prompt or None),
        ("profile.persona_preset", preset.id),
    ):
        if value is not None:
            _persist_setting(controller, path, value)
    if controller.bus is not None:
        controller.bus.publish({"type": "persona_changed", "id": preset.id, "name": preset.name})
    reply = f"收到！已切換到「{preset.name}」模式囉～🌸"
    return CommandResult(handled=True, reply=reply, action="switch_persona", details={"id": target_id})


@register_skill("volume_speed")
def _volume_speed(text: str, controller) -> CommandResult | None:
    live = controller.live
    if live is None:
        return None

    if re.search(r"(大聲一點|音量調大|聲音太小|大點聲)", text):
        new_vol = min(100, getattr(live, "volume", 100) + 15)
        live.volume = new_vol
        _persist_setting(controller, "tts.volume", new_vol)
        return CommandResult(handled=True, reply=f"好的，音量已為你調大囉（目前 {new_vol}%）！", action="volume_up")
    if re.search(r"(小聲一點|音量調小|聲音太大|小點聲)", text):
        new_vol = max(10, getattr(live, "volume", 100) - 15)
        live.volume = new_vol
        _persist_setting(controller, "tts.volume", new_vol)
        return CommandResult(handled=True, reply=f"好的，音量已為你調小囉（目前 {new_vol}%）！", action="volume_down")
    if re.search(r"(講話快一點|說話快一點|語速調快|講快點)", text):
        new_spd = min(1.5, round(getattr(live, "speed", 0.9) + 0.1, 2))
        live.speed = new_spd
        _persist_setting(controller, "tts.speed", new_spd)
        return CommandResult(handled=True, reply=f"好喔！語速已加快到 {new_spd} 倍速～", action="speed_up")
    if re.search(r"(講話慢一點|說話慢一點|語速調慢|講慢點)", text):
        new_spd = max(0.5, round(getattr(live, "speed", 0.9) - 0.1, 2))
        live.speed = new_spd
        _persist_setting(controller, "tts.speed", new_spd)
        return CommandResult(handled=True, reply=f"好的～語速已放慢到 {new_spd} 倍速囉。", action="speed_down")
    return None
