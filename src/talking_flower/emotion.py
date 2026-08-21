from __future__ import annotations


# 輕量關鍵詞情緒辨識：無模型，零依賴，適合本機即時
# 若未來要更準，可在此替換為 LLM 小模型或 bge 情感分類

EMOTION_KEYWORDS: dict[str, list[str]] = {
    "happy": [
        "哈哈", "呵呵", "開心", "快樂", "太棒", "太好了", "讚", "喜歡", "愛你", "開朗",
        "興奮", "期待", "好耶", "棒棒", "可愛", "甜", "溫暖",
    ],
    "sad": [
        "難過", "傷心", "寂寞", "孤單", "想哭", "沮喪", "低落", "失落", "抱歉", "對不起",
        "心疼", "心碎", "唉", "嗚", "哭",
    ],
    "angry": [
        "生氣", "憤怒", "討厭", "煩", "氣死", "可惡", "白癡", "笨蛋",
    ],
    "calm": [
        "放鬆", "平靜", "安靜", "深呼吸", "慢慢來", "別急", "輕鬆", "寧靜",
    ],
    "excited": [
        "哇", "天啊", "超", "超級", "竟然", "真的嗎", "太神", "厲害",
    ],
}

# CosyVoice instruct2 動態風格映射（對應 voice/style.txt 語句風格）
EMOTION_TO_STYLE: dict[str, str] = {
    "happy": "用開心活潑、帶點俏皮的語氣說",
    "sad": "用溫柔帶點感傷、低聲安慰的語氣說",
    "angry": "用有點小脾氣但不刻薄的語氣說",
    "calm": "用溫柔平靜、輕聲細語的語氣說",
    "excited": "用充滿驚喜、語調上揚的語氣說",
    "neutral": "",
}

DEFAULT_EMOTION = "neutral"


def detect_emotion(text: str) -> str:
    """回傳最匹配的情緒標籤，預設 neutral。"""
    if not text or not text.strip():
        return DEFAULT_EMOTION
    scores: dict[str, int] = {k: 0 for k in EMOTION_KEYWORDS}
    for emo, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[emo] += 1
                # 嘆號/問號加權
    # 標點加權
    if "！" in text or "!" in text:
        # 依上下文：有開心詞則 happy，否則 excited
        if scores["happy"] > 0:
            scores["happy"] += 1
        else:
            scores["excited"] += 1
    if "…" in text or "..." in text:
        scores["sad"] += 1

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else DEFAULT_EMOTION


def emotion_to_style(emotion: str) -> str:
    return EMOTION_TO_STYLE.get(emotion, "")


def detect_emotion_with_style(text: str) -> tuple[str, str]:
    emo = detect_emotion(text)
    return emo, emotion_to_style(emo)
