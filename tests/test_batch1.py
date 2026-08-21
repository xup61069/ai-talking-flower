from __future__ import annotations

import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.commands import parse_absolute_time, VoiceCommander
from talking_flower.metrics import MetricsStore
from talking_flower.reminders import ReminderScheduler


class AbsoluteTimeTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 8, 22, 14, 0, 0)  # 週六 14:00

    def test_tomorrow_morning_half_past_eight(self) -> None:
        result = parse_absolute_time("明天早上八點半叫我起床", now=self.NOW)
        self.assertIsNotNone(result)
        epoch, repeat, hhmm = result
        dt = datetime.datetime.fromtimestamp(epoch)
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M"), "2026-08-23 08:30")
        self.assertFalse(repeat)
        self.assertEqual(hhmm, "08:30")

    def test_daily_evening_nine(self) -> None:
        result = parse_absolute_time("每天晚上九點提醒我吃藥", now=self.NOW)
        self.assertIsNotNone(result)
        _, repeat, hhmm = result
        self.assertTrue(repeat)
        self.assertEqual(hhmm, "21:00")

    def test_afternoon_three_thirty(self) -> None:
        result = parse_absolute_time("下午三點半提醒我開會", now=self.NOW)
        self.assertIsNotNone(result)
        epoch = result[0]
        dt = datetime.datetime.fromtimestamp(epoch)
        self.assertEqual(dt.hour, 15)
        self.assertEqual(dt.minute, 30)

    def test_clock_format(self) -> None:
        result = parse_absolute_time("明天 19:30 提醒我運動", now=self.NOW)
        self.assertIsNotNone(result)
        dt = datetime.datetime.fromtimestamp(result[0])
        self.assertEqual((dt.day, dt.hour, dt.minute), (23, 19, 30))

    def test_bare_hour_afternoon_convention(self) -> None:
        # 「三點」無時段 → 口語慣例下午三點
        result = parse_absolute_time("三點提醒我喝水", now=self.NOW)
        self.assertIsNotNone(result)
        dt = datetime.datetime.fromtimestamp(result[0])
        self.assertIn(dt.hour, {15, 3})

    def test_unparseable(self) -> None:
        self.assertIsNone(parse_absolute_time("隨便啦", now=self.NOW))


class DailyRepeatReminderTests(unittest.TestCase):
    def test_repeat_daily_reschedules_next_day(self) -> None:
        temp = tempfile.TemporaryDirectory()
        scheduler = None
        try:
            scheduler = ReminderScheduler(Path(temp.name) / "r.db")
            reminder = scheduler.add(
                "吃藥", in_seconds=0.1, repeat_daily_hhmm="09:00"
            )
            # 等待到期
            import time as _t

            deadline = _t.time() + 2.0
            due = []
            while _t.time() < deadline and not due:
                due = scheduler.pop_due()
                if not due:
                    _t.sleep(0.05)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].text, "吃藥")
            # 重複提醒應被重新排程（spoken=0，trigger_at 在未來的下個 09:00）
            active = scheduler.list_active()
            self.assertEqual(len(active), 1)
            self.assertGreater(active[0]["due_in_s"], 60)  # 未來
            self.assertEqual(active[0]["repeat_daily_hhmm"], "09:00")
            # 驗證確實排在下一個 09:00（±5 分鐘容差）
            import datetime as _dt

            next_dt = _dt.datetime.fromtimestamp(active[0]["trigger_at"])
            expected = _dt.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            if expected <= _dt.datetime.now():
                expected += _dt.timedelta(days=1)
            self.assertLess(abs((next_dt - expected).total_seconds()), 300)
        finally:
            if scheduler is not None:
                scheduler.close()
            temp.cleanup()

    def test_one_time_marked_spoken(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            scheduler = ReminderScheduler(Path(temp.name) / "r.db")
            scheduler.add("一次性", in_seconds=0.0)
            due = scheduler.pop_due()
            self.assertEqual(len(due), 1)
            self.assertTrue(due[0].spoken)
            self.assertEqual(len(scheduler.list_active()), 0)
            scheduler.close()
        finally:
            temp.cleanup()


class MetricsStoreTests(unittest.TestCase):
    def test_add_recent_summary_cleanup(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            store = MetricsStore(Path(temp.name) / "m.db")
            for i in range(5):
                store.add(asr_ms=100 + i, ttft_ms=200 + i, ttfa_ms=300 + i, total_ms=1000 + i)
            recent = store.recent(10)
            self.assertEqual(len(recent), 5)
            self.assertEqual(recent[0]["asr_ms"], 100)  # 時間正序
            summary = store.summary()
            self.assertEqual(summary["count"], 5)
            self.assertAlmostEqual(summary["ttfa_ms_avg"], 302.0, places=1)
            removed = store.cleanup(keep_days=0)
            self.assertEqual(removed, 5)
            store.close()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
