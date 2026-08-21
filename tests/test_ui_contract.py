from __future__ import annotations

import re
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML = PROJECT_ROOT / "ui" / "index.html"
JS = PROJECT_ROOT / "ui" / "app.js"


class UiContractTests(unittest.TestCase):
    def test_js_ids_subset_of_html_ids(self) -> None:
        html_text = HTML.read_text(encoding="utf-8")
        js_text = JS.read_text(encoding="utf-8")
        html_ids = set(re.findall(r'id="([^"]+)"', html_text))
        # also id=' single quote variant
        html_ids |= set(re.findall(r"id='([^']+)'", html_text))
        # JS: $("...") and $('...') and getElementById
        js_ids = set(re.findall(r'\$\("([^"]+)"\)', js_text))
        js_ids |= set(re.findall(r"\$\('([^']+)'\)", js_text))
        js_ids |= set(re.findall(r'getElementById\("([^"]+)"\)', js_text))
        js_ids |= set(re.findall(r"getElementById\('([^']+)'\)", js_text))
        # also direct querySelector #id
        js_ids |= set(re.findall(r'querySelector\("#([^"]+)"\)', js_text))
        js_ids |= set(re.findall(r"querySelector\('#([^']+)'\)", js_text))
        # filter out non-id-looking (allow only those that look like html ids)
        # Ignore empty and ensure subset
        missing = js_ids - html_ids
        # 允许 JS 中未在 HTML 出现的 id 如果是测试或动态生成？此处严格要求子集
        self.assertEqual(
            missing,
            set(),
            f"JS 引用但 HTML 缺失的 id: {missing}；HTML ids: {sorted(html_ids)}",
        )

    def test_no_currentStatus_regression(self) -> None:
        js_text = JS.read_text(encoding="utf-8")
        self.assertNotIn("currentStatus", js_text, "app.js 不應殘留 currentStatus，應為 state")

    def test_html_links_exist(self) -> None:
        self.assertTrue((PROJECT_ROOT / "ui" / "theme.css").is_file(), "theme.css 應存在")
        self.assertTrue(JS.is_file(), "app.js 應存在")
        html_text = HTML.read_text(encoding="utf-8")
        self.assertIn('href="theme.css"', html_text)
        self.assertIn('src="app.js"', html_text)
