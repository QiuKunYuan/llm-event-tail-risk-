"""Strict raw JSON contract and explicitly permissive recovery parser."""
import json
import re
from typing import Any


def parse_json_output(text: str) -> dict[str, Any] | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if value.get("direction") not in {"改善", "恶化", "基本稳定", "混合", "不明确"}:
        return None
    if not isinstance(value.get("causes"), list) or not isinstance(value.get("evidence"), list):
        return None
    if not all(isinstance(item, str) for item in value["causes"] + value["evidence"]):
        return None
    return value


def strict_json_output(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != {"direction", "causes", "evidence"}:
        return None
    return value if parse_json_output(text) is not None else None
