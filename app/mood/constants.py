"""心情常量：七项情绪、显示文案、注入方式（对齐上游 dsh-feiyu-mood 0.7.6）。"""
from __future__ import annotations

DIM_KEYS: tuple[str, ...] = (
    "happy", "calm", "sweet", "heartache", "worry", "sad", "jealous",
)
DIM_LABELS = {
    "happy": "开心", "calm": "踏实", "sweet": "甜蜜",
    "heartache": "心疼", "worry": "担心", "sad": "难过", "jealous": "吃醋",
}
DIM_PLAIN = {
    "happy": "心里挺高兴", "calm": "心里踏实", "sweet": "心里甜",
    "heartache": "心疼得不行", "worry": "放心不下",
    "sad": "有点难过", "jealous": "有点吃醋",
}
DIM_SHORT = {
    "happy": "挺高兴", "calm": "挺踏实", "sweet": "有点甜",
    "heartache": "有点心疼", "worry": "有点挂心",
    "sad": "有点难过", "jealous": "有点酸",
}
INJECT_MODES: tuple[str, ...] = ("off", "label", "event")
MOOD_STEP_DIVISOR = 3
DEFAULT_MOOD = 70.0
