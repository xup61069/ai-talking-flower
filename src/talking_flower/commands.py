from __future__ import annotations

import datetime
import re
from typing import NamedTuple



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
    """即時語音指令識別器：委派 skills registry，行為與舊版完全一致。

    舊版硬編碼的五組指令已遷移至 skills/builtin.py；之後新指令
    （番茄鐘、日曆、OBS…）都是獨立技能檔，不再改這裡。
    """

    def __init__(self, registry=None) -> None:
        if registry is None:
            from .skills import load_builtin_skills

            registry = load_builtin_skills()
        self._registry = registry

    @property
    def skill_names(self) -> list[str]:
        return self._registry.names

    def try_execute(self, user_text: str, controller) -> CommandResult:
        return self._registry.try_execute(user_text, controller)
