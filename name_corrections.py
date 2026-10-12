# -*- coding: utf-8 -*-
"""Load and apply the user-editable OCR filename correction dictionary."""

import json
import os
import re
from pathlib import Path


DEFAULT_CORRECTIONS = {
    "关关于": "关于",
    "关干": "关于",
    "Subject": "",
    "Subject:": "",
    "Subject：": "",
    "iSubject": "",
    "iSubject:": "",
    "iSubject：": "",
    "lSubject": "",
    "lSubject:": "",
    "lSubject：": "",
    "头关于": "",
}
DEFAULT_SUBJECT_KEYS = ["subject", "主题", "事由", "关于"]


def _config_directory():
    """Return the writable per-user application configuration directory."""
    if os.name == "nt":
        config_root = os.environ.get("APPDATA")
        if config_root:
            root = Path(config_root)
        else:
            root = Path.home() / "AppData" / "Roaming"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "PDF_Fax_Renamer"


def dictionary_path():
    """Return the writable per-user correction dictionary location."""
    return _config_directory() / "name_corrections.json"


def subject_keys_path():
    """Return the writable per-user subject keyword location."""
    return _config_directory() / "subject_keys.json"


def save_corrections(corrections, path=None):
    """Validate and save correction pairs as UTF-8 JSON."""
    target = Path(path) if path is not None else dictionary_path()
    validated = {}
    for wrong, right in corrections.items():
        if not isinstance(wrong, str) or not wrong.strip():
            raise ValueError("错误文本不能为空")
        if not isinstance(right, str):
            raise ValueError("正确文本必须是字符串")
        validated[wrong] = right

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(validated, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(target)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise
    return target


def load_corrections(path=None):
    """Load corrections, initializing the per-user file with built-in entries."""
    target = Path(path) if path is not None else dictionary_path()
    if not target.exists():
        save_corrections(DEFAULT_CORRECTIONS, target)

    try:
        with target.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 OCR 错误字典 {}：{}".format(target, exc)) from exc

    if not isinstance(data, dict):
        raise ValueError("OCR 错误字典必须是“错误文本:正确文本”的 JSON 对象")
    for wrong, right in data.items():
        if not isinstance(wrong, str) or not wrong.strip() or not isinstance(right, str):
            raise ValueError("OCR 错误字典包含无效条目：键必须非空，值必须为字符串")
    return data


def save_subject_keys(subject_keys, path=None):
    """Validate and save the user-configurable subject keywords."""
    if not isinstance(subject_keys, list):
        raise ValueError("SUBJECT_KEYS 必须是字符串列表")
    validated = []
    for key in subject_keys:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("SUBJECT_KEYS 不能包含空关键词")
        key = key.strip()
        if key not in validated:
            validated.append(key)
    if not validated:
        raise ValueError("SUBJECT_KEYS 至少需要一个关键词")

    target = Path(path) if path is not None else subject_keys_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(validated, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(target)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise
    return target


def load_subject_keys(path=None):
    """Load the user-configurable subject keywords."""
    target = Path(path) if path is not None else subject_keys_path()
    if not target.exists():
        save_subject_keys(DEFAULT_SUBJECT_KEYS, target)

    try:
        with target.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取主题关键词配置 {}：{}".format(target, exc)) from exc

    if not isinstance(data, list) or not data:
        raise ValueError("SUBJECT_KEYS 必须是非空字符串列表")
    if any(not isinstance(key, str) or not key.strip() for key in data):
        raise ValueError("SUBJECT_KEYS 不能包含空关键词或非字符串值")
    return list(dict.fromkeys(key.strip() for key in data))


def apply_corrections(text, corrections=None):
    """Apply longest-match-first literal replacements to OCR text."""
    if not text:
        return text
    mapping = corrections if corrections is not None else load_corrections()
    for wrong in sorted(mapping, key=len, reverse=True):
        if wrong.isascii():
            text = re.sub(re.escape(wrong), lambda _: mapping[wrong], text, flags=re.IGNORECASE)
        else:
            text = text.replace(wrong, mapping[wrong])
    return text
