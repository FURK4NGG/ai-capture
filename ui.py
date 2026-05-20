#!/usr/bin/env python3
import gi
import gc
import os
import sys
import json
import subprocess
import threading
import codecs
import time
import select
from pathlib import Path
import base64
import tempfile
from datetime import datetime
import signal
import shutil

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Gio, Gdk, Pango, Adw, GdkPixbuf
Adw.init()

CONFIG_PATH = Path.home() / ".config" / "capture-ai" / "config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

BASE_DIR = Path.home() / ".cache" / "capture-ai"
CHAT_DIR = BASE_DIR / "chats"
LANG_DIR = Path.home() / "capture-ai" / "language"
AI_SCRIPT = str(Path.home() / "capture-ai/ai.py")
GENERATED_DIR = BASE_DIR / "generated_images"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_FILES_DIR = BASE_DIR / "generated_files"
GENERATED_FILES_DIR.mkdir(parents=True, exist_ok=True)

CHAT_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_CUSTOM_COLORS_DARK = {
    "window_bg": "#111111",
    "input_bg": "#1e1e1e",
    "user_bg": "#303643",
    "user_text": "#ffffff",
    "bot_bg": "#222222",
    "bot_text": "#ffffff",
    "sidebar_text": "#dddddd",
    "local_model_highlight": "#ff9800",
}

DEFAULT_CUSTOM_COLORS_LIGHT = {
    "window_bg": "#f5f5f5",
    "input_bg": "#ffffff",
    "user_bg": "#dbe7ff",
    "user_text": "#111111",
    "bot_bg": "#ececec",
    "bot_text": "#111111",
    "sidebar_text": "#111111",
    "local_model_highlight": "#ff9800",
}


def ensure_bool_strict(value, default):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False

    return default


def ensure_base_config(config: dict) -> dict:
    defaults = {
        "ui_language": "en",
        "last_chat": "default.json",
        "dark_mode": True,
        "open_router_key": "",
        "tavily_api_key": "",
        "ask_for_web_search": True,
        "prompt_chooser_blocks": ["copyable"],
        "response_style": "normal",
        "show_usage": True,
        "show_token_value": False,
        "token_value": "2.0",
        "force_ui_language": False,
        "is_mic_online": True,
        "use_desktop_voice": False,
        "stt_model_online": "openai/gpt-audio-mini",
        "whisper_cpp_bin": "",
        "whisper_cpp_model": "",
        "use_stt_timeout": False,
        "stt_timeout": "10",
        "use_stt_silence": False,
        "stt_silence_duration": "2",
        "pinned_chats": [],
        "custom_colors_dark": dict(DEFAULT_CUSTOM_COLORS_DARK),
        "custom_colors_light": dict(DEFAULT_CUSTOM_COLORS_LIGHT),
        "chat_bg_image": "",
        "rag_settings": {
            "recent_message_count": 10,
            "retrieved_chunk_count": 5,
            "summary_update_every": 20,
            "memory_chunk_max_chars": 1200,
            "summary_max_chars": 6000,
            "code_context_max_chars": 8000,
            "use_summary": True,
            "use_recent_messages": True,
            "use_retrieval": True,
            "use_code_context": True,
            "include_recent_attachments": False
        },
        "chat_rag_switch": {
            "default.json": True
        },
        "ai_models": [{
            "id": "google/gemini-2.5-flash-image",
            "local": False
        }],
        "chat_models": {
            "default.json": {
                "id": "google/gemini-2.5-flash-image",
                "local": False
            }
        },
        "local_providers": {
            "ollama": {
                "enabled": False,
                "base_url": "http://127.0.0.1:11434",
                "run_startup": "ollama serve",
                "stop_command": "pkill -f 'ollama serve'",
                "system_error": "Ollama bağlantı hatası.\nBeklenen adres: {base_url}\n'ollama serve' çalıştır.",
                "temperature": "",
                "top_p": "",
                "top_k": "",
                "repeat_penalty": "",
                "num_ctx": "",
                "num_predict": "",
                "keep_alive": "",
                "system_prompt": ""
            }
        },
    }

    bool_keys = [
        "dark_mode",
        "ask_for_web_search",
        "show_usage",
        "show_token_value",
        "force_ui_language",
        "is_mic_online",
        "use_desktop_voice",
        "use_stt_timeout",
        "use_stt_silence",
    ]

    changed = False

    for key, default in defaults.items():
        if key not in config:
            config[key] = default
            changed = True
            continue

        value = config.get(key)

        if key in bool_keys:
            fixed = ensure_bool_strict(value, default)
            if fixed != value:
                config[key] = fixed
                changed = True
            continue

        if isinstance(default, str):
            if not isinstance(value, str) or not value.strip():
                config[key] = default
                changed = True
            continue

        if isinstance(default, list):
            if not isinstance(value, list):
                config[key] = default
                changed = True
            continue

        if isinstance(default, dict):
            if not isinstance(value, dict):
                config[key] = default
                changed = True
            continue

    # ai_models normalize
    if isinstance(config["ai_models"], list):
        cleaned_models = []
        for item in config["ai_models"]:
            if isinstance(item, dict):
                mid = str(item.get("id") or "").strip()
                if not mid:
                    continue
                cleaned_models.append({
                    "id": mid,
                    "local": ensure_bool_strict(item.get("local"), False)
                })
            elif isinstance(item, str):
                mid = item.strip()
                if not mid:
                    continue
                cleaned_models.append({
                    "id": mid,
                    "local": False
                })

        if not cleaned_models:
            cleaned_models = [dict(defaults["ai_models"][0])]

        if cleaned_models != config["ai_models"]:
            config["ai_models"] = cleaned_models
            changed = True

    # chat_models normalize
    if isinstance(config["chat_models"], dict):
        fixed_chat_models = {}

        for chat_name, model in config["chat_models"].items():
            chat_key = str(chat_name or "").strip()
            if not chat_key:
                continue

            if isinstance(model, dict):
                mid = str(model.get("id") or "").strip()
                if not mid:
                    mid = defaults["chat_models"]["default.json"]["id"]

                fixed_chat_models[chat_key] = {
                    "id": mid,
                    "local": ensure_bool_strict(model.get("local"), False)
                }

            elif isinstance(model, str):
                mid = model.strip()
                if not mid:
                    mid = defaults["chat_models"]["default.json"]["id"]

                fixed_chat_models[chat_key] = {
                    "id": mid,
                    "local": False
                }

        if not fixed_chat_models:
            fixed_chat_models = dict(defaults["chat_models"])

        if fixed_chat_models != config["chat_models"]:
            config["chat_models"] = fixed_chat_models
            changed = True
    
    # rag_settings normalize
    rag_defaults = defaults["rag_settings"]

    if not isinstance(config.get("rag_settings"), dict):
        config["rag_settings"] = dict(rag_defaults)
        changed = True
    else:
        fixed_rag = dict(rag_defaults)
        raw_rag = config.get("rag_settings", {})

        int_keys = [
            "recent_message_count",
            "retrieved_chunk_count",
            "summary_update_every",
            "memory_chunk_max_chars",
            "summary_max_chars",
            "code_context_max_chars",
        ]

        bool_rag_keys = [
            "use_summary",
            "use_recent_messages",
            "use_retrieval",
            "use_code_context",
            "include_recent_attachments",
        ]

        for key in int_keys:
            try:
                val = int(raw_rag.get(key, rag_defaults[key]))
            except Exception:
                val = rag_defaults[key]

            if key in ("recent_message_count", "retrieved_chunk_count"):
                val = max(0, min(val, 50))
            elif key == "summary_update_every":
                val = max(1, min(val, 200))
            else:
                val = max(100, min(val, 100_000))

            fixed_rag[key] = val

        for key in bool_rag_keys:
            fixed_rag[key] = ensure_bool_strict(
                raw_rag.get(key, rag_defaults[key]),
                rag_defaults[key]
            )

        if fixed_rag != config.get("rag_settings"):
            config["rag_settings"] = fixed_rag
            changed = True


    # local_providers normalize
    provider_defaults = {
        "enabled": False,
        "base_url": "http://127.0.0.1:11434",
        "run_startup": "",
        "stop_command": "",
        "system_error": "",
        "temperature": "",
        "top_p": "",
        "top_k": "",
        "repeat_penalty": "",
        "num_ctx": "",
        "num_predict": "",
        "keep_alive": "",
        "system_prompt": ""
    }

    if not isinstance(config.get("local_providers"), dict):
        config["local_providers"] = {
            "ollama": dict(provider_defaults)
        }
        changed = True
    else:
        fixed_local_providers = {}

        for provider_name, provider_cfg in config["local_providers"].items():
            pname = str(provider_name or "").strip()
            if not pname:
                continue

            if not isinstance(provider_cfg, dict):
                provider_cfg = {}

            fixed = dict(provider_defaults)

            fixed["enabled"] = ensure_bool_strict(
                provider_cfg.get("enabled"),
                provider_defaults["enabled"]
            )

            for key in [
                "base_url",
                "run_startup",
                "stop_command",
                "system_error",
                "temperature",
                "top_p",
                "top_k",
                "repeat_penalty",
                "num_ctx",
                "num_predict",
                "keep_alive",
                "system_prompt",
            ]:
                val = provider_cfg.get(key, provider_defaults[key])
                if not isinstance(val, str):
                    val = str(provider_defaults[key])
                if key == "base_url" and not val.strip():
                    val = provider_defaults["base_url"]
                fixed[key] = val

            fixed_local_providers[pname] = fixed

        if not fixed_local_providers:
            fixed_local_providers = {
                "ollama": dict(provider_defaults)
            }

        if fixed_local_providers != config.get("local_providers"):
            config["local_providers"] = fixed_local_providers
            changed = True

    return config

# Config dosyasından son açılan chat'i al, yoksa default.json kullan
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f) or {}
    except json.JSONDecodeError:
        try:
            bad_path = CONFIG_PATH.with_suffix(".broken.json")
            shutil.copy2(CONFIG_PATH, bad_path)
        except Exception:
            pass
        config = {}

    old_config = json.loads(json.dumps(config))
    config = ensure_base_config(config)

    if config != old_config:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
else:
    config = ensure_base_config({})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

last_chat_name = config["last_chat"]
dark_mode = config["dark_mode"]

DEFAULT_CHAT = CHAT_DIR / last_chat_name
if not DEFAULT_CHAT.exists():
    DEFAULT_CHAT.write_text(
        json.dumps({
            "summary": "",
            "messages": [],
            "code_context": {},
            "memory_chunks": []
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fix_mojibake(s: str) -> str:
    if not s:
        return s
    # Mojibake tipik işaretleri
    if ("Ã" not in s) and ("Å" not in s) and ("â" not in s):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s



class ChatApp(Gtk.Application):
    def __init__(self, image_path=None):
        super().__init__()
        self.connect("activate", self.on_activate)

        self.loaded_message_start = None
        self.lazy_chat_initial_count = 10
        self.lazy_chat_batch_count = 30
        self._loading_older_messages = False

        self.current_chat = DEFAULT_CHAT
        self.image_path = image_path

        self.pending_images = []
        self.pending_files = []

        self._lang_cache = None
        self._lang_cache_lang = None

        if image_path:
            try:
                from urllib.parse import urlparse, unquote
                p = str(image_path).strip()

                # file://... uri gelirse path’e çevir
                if p.startswith("file://"):
                    p = unquote(urlparse(p).path)

                rp = str(Path(p).expanduser().resolve())
                if Path(rp).exists():
                    self.pending_images = [rp]
            except Exception:
                pass

        self.sidebar_expanded = True
        self.chats_list_open = True
        self.models_list_open = True

        self.ai_proc = None
        self.ai_stop_requested = False
        self.is_generating = False

        self.ai_procs = {}
        self.ai_stop_requested_chats = set()
        self.generating_chats = set()
        self.stream_ui_ready_chats = set()
        self.stream_active_chats = set()

        self.auto_focus_enabled = True
        self.auto_scroll_enabled = True
        self.selected_indexes = set()
        self.typing_active = False
        self.selection_mode = False

        # aktif chat’in modeli (chat başına)
        self.active_model = None
        self.streaming_row = None

    # ----------------- OPTIMIZATION -----------------
    def clear_closed_chat_memory(self):
        try:
            self.streaming_row = None
            self.streaming_label = None
            self._streaming_bot_idx = None

            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass

            while True:
                child = self.chat_box.get_first_child()
                if not child:
                    break
                self.chat_box.remove(child)

            try:
                self.selection_label.set_text("")
                self.selection_row.set_opacity(0.0)
                self.selection_row.set_sensitive(False)
            except Exception:
                pass

            try:
                self.pending_images.clear()
                self.pending_files.clear()
            except Exception:
                pass

            try:
                while True:
                    c = self.preview_hbox.get_first_child()
                    if not c:
                        break
                    self.preview_hbox.remove(c)
                self.preview_row.set_visible(False)
            except Exception:
                pass

            try:
                buf = self.textview.get_buffer()
                buf.set_text("")
            except Exception:
                pass

            gc.collect()
            return False

        except Exception:
            return False

    # ---------------- CONFIG HELPERS ----------------
    def load_config(self):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception as e:
            print("config read error:", e)
        return {}

    def save_config(self, cfg: dict):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("config save error:", e)

    def load_chat_data_ui(self, path=None):
        path = Path(path or self.current_chat)

        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or []
        except Exception:
            raw = []

        # Eski format: direkt liste
        if isinstance(raw, list):
            return {
                "summary": "",
                "messages": raw,
                "code_context": {},
                "memory_chunks": []
            }

        # Yeni format: dict
        if not isinstance(raw, dict):
            raw = {}

        raw.setdefault("summary", "")
        raw.setdefault("messages", [])
        raw.setdefault("code_context", {})
        raw.setdefault("memory_chunks", [])

        if not isinstance(raw["messages"], list):
            raw["messages"] = []

        return raw


    def save_chat_data_ui(self, data, path=None):
        path = Path(path or self.current_chat)

        if not isinstance(data, dict):
            data = {
                "summary": "",
                "messages": [],
                "code_context": {},
                "memory_chunks": []
            }

        data.setdefault("summary", "")
        data.setdefault("messages", [])
        data.setdefault("code_context", {})
        data.setdefault("memory_chunks", [])

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


    def get_context_mode(self, chat_path=None) -> str:
        chat_path = Path(chat_path or self.current_chat)

        cfg = ensure_base_config(self.load_config())
        chat_rag_switch = cfg.setdefault("chat_rag_switch", {})

        direct_mode = ensure_bool_strict(
            chat_rag_switch.get(chat_path.name, True),
            True
        )

        return "direct" if direct_mode else "rag"

    def set_context_mode(self, mode: str, chat_path=None):
        mode = str(mode or "").strip().lower()
        if mode not in ("rag", "direct"):
            mode = "direct"

        chat_path = Path(chat_path or self.current_chat)

        cfg = ensure_base_config(self.load_config())
        cfg.setdefault("chat_rag_switch", {})

        cfg["chat_rag_switch"][chat_path.name] = (mode == "direct")

        self.save_config(cfg)
        self.refresh_context_mode_switch()

    def get_ui_strings_map(self) -> dict:
        current_lang = self.get_ui_language()

        if (
            self._lang_cache is not None and
            self._lang_cache_lang == current_lang
        ):
            return self._lang_cache

        lang_path = LANG_DIR / f"{current_lang}.json"
        fallback_path = LANG_DIR / "en.json"

        data = {}

        try:
            if lang_path.exists():
                raw = json.loads(lang_path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    data = raw
        except Exception as e:
            print(f"language file read error ({lang_path.name}):", e)

        if not data:
            try:
                if fallback_path.exists():
                    raw = json.loads(fallback_path.read_text(encoding="utf-8")) or {}
                    if isinstance(raw, dict):
                        data = raw
            except Exception as e:
                print(f"fallback language file read error ({fallback_path.name}):", e)

        if not isinstance(data, dict):
            data = {}

        self._lang_cache = data
        self._lang_cache_lang = current_lang
        return self._lang_cache

    def get_ui_lang_map(self, lang: str | None = None) -> dict:
        if lang is None or lang == self.get_ui_language():
            return self.get_ui_strings_map()

        # farklı bir dil özellikle istenirse bir kerelik oku
        lang = str(lang or "").strip().lower() or "en"
        lang_path = LANG_DIR / f"{lang}.json"
        fallback_path = LANG_DIR / "en.json"

        data = {}

        try:
            if lang_path.exists():
                raw = json.loads(lang_path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    data = raw
        except Exception as e:
            print(f"language file read error ({lang_path.name}):", e)

        if not data:
            try:
                if fallback_path.exists():
                    raw = json.loads(fallback_path.read_text(encoding="utf-8")) or {}
                    if isinstance(raw, dict):
                        data = raw
            except Exception as e:
                print(f"fallback language file read error ({fallback_path.name}):", e)

        return data if isinstance(data, dict) else {}

    def __call__(self, key: str, **kwargs) -> str:
        lang_map = self.get_ui_lang_map()
        text = str(lang_map.get(key, key))

        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def render_message_text(self, msg: dict) -> str:
        if not isinstance(msg, dict):
            return ""

        if msg.get("error_key"):
            params = msg.get("error_params") or {}
            if not isinstance(params, dict):
                params = {}

            error_key = str(msg.get("error_key"))

            # Zaten genel error wrapper ise tekrar "Error:" ekleme
            if error_key == "o_Error_With_Detail":
                return self(error_key, **params)

            inner = self(error_key, **params)
            return self("o_Error_With_Detail", error=inner)

        if msg.get("status_key"):
            params = msg.get("status_params") or {}
            if not isinstance(params, dict):
                params = {}
            return self(str(msg.get("status_key")), **params)

        if msg.get("i18n_key"):
            params = msg.get("i18n_params") or {}
            if not isinstance(params, dict):
                params = {}
            return self(str(msg.get("i18n_key")), **params)

        return str(msg.get("content") or "")

    def append_text_with_links(self, parent, text: str):
        import re

        text = str(text or "")
        url_re = re.compile(r"(https?://[^\s\]\)\"'>]+)")

        parts = url_re.split(text)

        for part in parts:
            if not part:
                continue

            if url_re.fullmatch(part):
                clean_url = part.rstrip(".,;:")
                trailing = part[len(clean_url):]

                link = Gtk.LinkButton.new_with_label(clean_url, clean_url)
                link.set_halign(Gtk.Align.START)
                link.set_uri(clean_url)
                self._consume_click(link)
                parent.append(link)

                if trailing:
                    lab = Gtk.Label(label=trailing)
                    lab.set_xalign(0)
                    parent.append(lab)

            else:
                lab = Gtk.Label(label=part)
                lab.set_xalign(0)
                lab.set_wrap(True)
                lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lab.set_halign(Gtk.Align.START)
                parent.append(lab)


    def get_ui_language(self) -> str:
        cfg = ensure_base_config(self.load_config())
        lang = str(cfg["ui_language"]).strip().lower()
        return lang if lang else "en"

    def set_ui_language(self, lang: str):
        lang = str(lang or "").strip().lower()
        if not lang:
            return

        cfg = self.load_config()
        cfg["ui_language"] = lang
        self.save_config(cfg)

        self._lang_cache = None
        self._lang_cache_lang = None

    def get_available_ui_languages(self) -> list[str]:
        out = []

        try:
            if LANG_DIR.exists():
                for p in LANG_DIR.glob("*.json"):
                    name = p.stem.strip().lower()
                    if name:
                        out.append(name)
        except Exception as e:
            print("language directory read error:", e)

        return sorted(set(out))



    def _get_active_colors_key(self) -> str:
        cfg = ensure_base_config(self.load_config())
        is_dark = cfg["dark_mode"]
        return "custom_colors_dark" if is_dark else "custom_colors_light"

    def _get_default_theme_colors(self, is_dark: bool | None = None) -> dict:
        if is_dark is None:
            cfg = ensure_base_config(self.load_config())
            is_dark = cfg["dark_mode"]

        base = DEFAULT_CUSTOM_COLORS_DARK if is_dark else DEFAULT_CUSTOM_COLORS_LIGHT
        return dict(base)

    def _get_theme_colors(self) -> dict:
        cfg = ensure_base_config(self.load_config())
        is_dark = cfg["dark_mode"]
        key = "custom_colors_dark" if is_dark else "custom_colors_light"

        raw = cfg[key]
        defaults = self._get_default_theme_colors(is_dark)

        out = {}
        for k, v in defaults.items():
            out[k] = str(raw.get(k, v))
        return out

    def _save_theme_colors(self, colors: dict):
        cfg = self.load_config()
        key = self._get_active_colors_key()
        cfg[key] = dict(colors)
        self.save_config(cfg)

    def _reset_active_theme_colors_to_default(self):
        cfg = ensure_base_config(self.load_config())
        is_dark = cfg["dark_mode"]
        key = "custom_colors_dark" if is_dark else "custom_colors_light"
        cfg[key] = self._get_default_theme_colors(is_dark)
        self.save_config(cfg)



    def _normalize_model_entry(self, item) -> dict | None:
        if isinstance(item, str):
            mid = item.strip()
            if not mid:
                return None
            return {
                "id": mid,
                "local": False
            }

        if not isinstance(item, dict):
            return None

        mid = str(item.get("id", "")).strip()
        if not mid:
            return None

        return {
            "id": mid,
            "local": bool(item.get("local", False))
        }

    def _get_models_list(self, cfg: dict) -> list[dict]:
        raw = cfg.get("ai_models", [])
        if not isinstance(raw, list):
            raw = []

        out = []
        seen = set()

        for item in raw:
            norm = self._normalize_model_entry(item)
            if not norm:
                continue

            mid = norm["id"]
            if mid in seen:
                continue

            seen.add(mid)
            out.append(norm)

        return out

    def _get_model_ids(self, cfg: dict) -> list[str]:
        return [m["id"] for m in self._get_models_list(cfg)]

    def _find_model_entry(self, cfg: dict, model_id: str) -> dict | None:
        model_id = str(model_id or "").strip()
        if not model_id:
            return None

        for item in self._get_models_list(cfg):
            if item["id"] == model_id:
                return item

        return None

    def _ensure_min_one_model(self, cfg: dict):
        models = self._get_models_list(cfg)
        if not models:
            models = [{
                "id": "google/gemini-2.5-flash-image",
                "local": False
            }]
            cfg["ai_models"] = models
            self.save_config(cfg)
        return models

    def get_default_model(self, cfg: dict) -> str:
        models = self._ensure_min_one_model(cfg)
        return str(models[0]["id"])

    def get_chat_model(self, chat_path: Path) -> str:
        cfg = ensure_base_config(self.load_config())
        models = self._ensure_min_one_model(cfg)
        model_ids = [m["id"] for m in models]

        chat_models = cfg["chat_models"]
        key = chat_path.name

        selected = chat_models.get(key)
        model_id = ""

        if isinstance(selected, dict):
            model_id = str(selected.get("id") or "").strip()
        elif isinstance(selected, str):
            model_id = selected.strip()

        if (not model_id) or (model_id not in model_ids):
            model_id = str(models[0]["id"])
            chat_models[key] = {
                "id": model_id,
                "local": bool(models[0].get("local", False))
            }
            cfg["chat_models"] = chat_models
            self.save_config(cfg)

        return model_id

    def set_chat_model(self, chat_path: Path, model_id: str, is_local: bool | None = None):
        model_id = str(model_id).strip()
        if not model_id:
            return

        cfg = ensure_base_config(self.load_config())
        cfg.setdefault("chat_models", {})
        models = self._ensure_min_one_model(cfg)

        found = None
        rest = []

        for item in models:
            if item["id"] == model_id and found is None:
                # mevcut model → local bilgisini koru
                current_local = bool(item.get("local", False))

                found = {
                    "id": model_id,
                    "local": current_local if is_local is None else bool(is_local)
                }
            else:
                rest.append(item)

        # model hiç yoksa → yeni ekle
        if found is None:
            found = {
                "id": model_id,
                "local": bool(is_local) if is_local is not None else False
            }

        # LRU: en üste al
        cfg["ai_models"] = [found] + rest

        # chat mapping
        cfg["chat_models"][chat_path.name] = {
            "id": found["id"],
            "local": bool(found.get("local", False))
        }

        self.save_config(cfg)

        # UI refresh
        self.active_model = {
            "id": found["id"],
            "local": bool(found.get("local", False))
        }
        self.load_models_list()
        self.apply_models_visibility()

    # ---------------- UI BUILD ----------------

    def on_activate(self, app):
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_title("Capture AI")
        self.win.set_default_size(1100, 750)
        self.apply_theme(dark_mode)
        self.apply_css()

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.add_css_class("app-root")
        self.win.set_child(root)
        self.setup_file_drop_target(root)

        # SIDEBAR
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sidebar.add_css_class("sidebar")
        self.sidebar.set_size_request(250, -1)
        self.sidebar.set_hexpand(False)
        self.sidebar.set_vexpand(True)
        self.sidebar.set_valign(Gtk.Align.FILL)
        root.append(self.sidebar)

        self.toggle_btn = Gtk.Button(label="☰")
        self.bind_i18n(self.toggle_btn, "tooltip", "o_Toggle_Sidebar")
        self.toggle_btn.connect("clicked", self.toggle_sidebar)
        self.sidebar.append(self.toggle_btn)

        self.new_btn_sidebar = Gtk.Button(label="+")
        self.new_btn_sidebar.set_visible(False)
        self.bind_i18n(self.new_btn_sidebar, "tooltip", "o_New_Chat")
        self.new_btn_sidebar.connect("clicked", self.create_chat)
        self.sidebar.append(self.new_btn_sidebar)


        # SIDEBAR SCROLL
        self.sidebar_scroll = Gtk.ScrolledWindow()
        self.sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar_scroll.set_hexpand(True)
        self.sidebar_scroll.set_vexpand(True)
        self.sidebar_scroll.set_valign(Gtk.Align.FILL)
        self.sidebar.append(self.sidebar_scroll)

        self.sidebar_scroll_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sidebar_scroll_content.set_hexpand(True)
        self.sidebar_scroll_content.set_vexpand(False)
        self.sidebar_scroll.set_child(self.sidebar_scroll_content)
        self.sidebar_scroll_content.set_valign(Gtk.Align.START)

        # ---------------- CONTEXT MODE SWITCH ----------------
        self.context_mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.context_mode_row.set_halign(Gtk.Align.CENTER)
        self.context_mode_row.set_hexpand(True)
        self.context_mode_row.set_margin_top(4)
        self.context_mode_row.set_margin_bottom(4)

        self.context_rag_label = Gtk.Label()
        self.bind_i18n(self.context_rag_label, "label", "o_Context_RAG")
        self.context_rag_label.set_xalign(0)
        self.context_rag_label.set_halign(Gtk.Align.START)

        self.context_mode_switch = Gtk.Switch()
        self.context_mode_switch.set_halign(Gtk.Align.CENTER)
        self.context_mode_switch.set_valign(Gtk.Align.CENTER)

 
        self.context_direct_label = Gtk.Label()
        self.bind_i18n(self.context_direct_label, "label", "o_Context_Direct")
        self.context_direct_label.set_xalign(0)
        self.context_direct_label.set_halign(Gtk.Align.START)

        self.context_mode_row.append(self.context_rag_label)
        self.context_mode_row.append(self.context_mode_switch)
        self.context_mode_row.append(self.context_direct_label)

        self.bind_i18n(self.context_mode_row, "tooltip", "o_Context_Mode")


        self.context_mode_switch.connect("notify::active", self.on_context_mode_switch_changed)

        self.sidebar_scroll_content.append(self.context_mode_row)
        self.refresh_context_mode_switch()

        # AI MODELS HEADER + LIST
        self.models_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.models_header.set_hexpand(True)

        self.models_toggle_btn = Gtk.Button(label=f"{self('o_AI_Models')} ▾")
        self.models_toggle_btn.connect("clicked", self.toggle_models_list)
        self.models_header.append(self.models_toggle_btn)

        models_spacer = Gtk.Box()
        self.models_header.append(models_spacer)
        self.add_model_btn = Gtk.Button(label="+")
        self.bind_i18n(self.add_model_btn, "tooltip", "o_Add_Model")
        self.add_model_btn.connect("clicked", self.open_add_model_dialog)
        self.models_header.append(self.add_model_btn)

        self.sidebar_scroll_content.append(self.models_header)

        self.models_list = Gtk.ListBox()
        self.models_list.connect("row-activated", self.on_model_row_activated)
        self.models_list.set_activate_on_single_click(False)
        self.sidebar_scroll_content.append(self.models_list)

        # CHATS HEADER
        self.chats_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.chats_toggle_btn = Gtk.Button(label=f"{self('o_Chats')} ▾")
        self.chats_toggle_btn.connect("clicked", self.toggle_chats_list)
        self.chats_header.append(self.chats_toggle_btn)

        self.new_btn = Gtk.Button(label="+")
        self.bind_i18n(self.new_btn, "tooltip", "o_New_Chat")
        self.new_btn.connect("clicked", self.create_chat)
        self.chats_header.append(self.new_btn)


        self.sidebar_scroll_content.append(self.chats_header)

        self.chat_list = Gtk.ListBox()
        self.chat_list.set_activate_on_single_click(True)
        self.chat_list.connect("row-activated", self.switch_chat)
        self.sidebar_scroll_content.append(self.chat_list)

        # SPACER
        self.sidebar_spacer = Gtk.Box()
        self.sidebar_spacer.set_vexpand(True)
        self.sidebar_spacer.set_visible(False)
        self.sidebar.append(self.sidebar_spacer)

        # SIDEBAR BOTTOM
        self.sidebar_bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sidebar_bottom.set_hexpand(True)
        self.sidebar_bottom.set_vexpand(False)
        self.sidebar_bottom.set_valign(Gtk.Align.END)
        self.sidebar.append(self.sidebar_bottom)

        # Dark mode
        dark_mode_btn = Gtk.Button(label="🌓")
        self.bind_i18n(dark_mode_btn, "tooltip", "o_Toggle_Dark_Mode")
        dark_mode_btn.connect("clicked", self.toggle_theme)
        self.sidebar_bottom.append(dark_mode_btn)

        # Settings
        self.settings_btn = Gtk.Button(label="⚙")
        self.bind_i18n(self.settings_btn, "tooltip", "o_Settings")
        self.settings_btn.connect("clicked", self.open_settings_menu)
        self.sidebar_bottom.append(self.settings_btn)

        # MAIN
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.set_hexpand(True)
        main.set_vexpand(True)
        main.set_halign(Gtk.Align.FILL)
        root.append(main)

        # OVERLAY
        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        main.append(overlay)
        self.setup_file_drop_target(overlay)

        # Scroll content
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.content_box.set_hexpand(True)
        self.content_box.set_vexpand(True)
        self.content_box.add_css_class("chat-area")

        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.chat_box.set_hexpand(True)
        self.content_box.append(self.chat_box)
        # Selection row: "X mesaj referans seçildi" + [X]
        self.selection_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.selection_row.set_margin_start(10)
        self.selection_row.set_halign(Gtk.Align.START)

        # --- TYPING INDICATOR (chat_box dışında, load_chat silmesin) ---
        self.typing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.typing_row.set_halign(Gtk.Align.START)
        typing_bubble = Gtk.Box()
        typing_bubble.add_css_class("bot-bubble")
        self.typing_label = Gtk.Label()
        self.bind_i18n(self.typing_label, "label", "o_Thinking")
        self.typing_label.set_wrap(True)
        self.typing_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.typing_label.set_xalign(0)
        self.typing_label.set_max_width_chars(60)
        typing_bubble.append(self.typing_label)
        self.typing_row.append(typing_bubble)
        # Başta gizli ama yerini korusun istersen:
        self.typing_row.set_visible(False)
        # KRİTİK: chat_box'a değil, content_box'a ekle
        self.content_box.append(self.typing_row)
        self.typing_dots = 0
        self.typing_active = False


        self.selection_label = Gtk.Label(label="")
        self.selection_label.set_xalign(0)
        self.selection_label.set_halign(Gtk.Align.START)
        self.selection_label.set_hexpand(False)

        self.selection_clear_btn = Gtk.Button(label="✕")
        self.selection_clear_btn.add_css_class("flat")
        self.bind_i18n(self.selection_clear_btn, "tooltip", "o_Clear_Selection")
        self.selection_clear_btn.connect("clicked", self.clear_selection)

        # X'e basınca bubble click falan tetiklenmesin (sende _consume_click varsa)
        self._consume_click(self.selection_clear_btn)

        # Başta görünmez ama yer kaplasın (chat zıplamasın)
        self.selection_row.set_opacity(0.0)
        self.selection_row.set_sensitive(False)
        self.selection_row.set_size_request(-1, 34)  # istersen 26-40 arası ayarla

        self.selection_row.append(self.selection_label)
        self.selection_row.append(self.selection_clear_btn)

        # ÖNEMLİ: tanımladıktan sonra append
        self.content_box.append(self.selection_row)

        # --- ATTACHMENTS PREVIEW STRIP (horizontal + scroll) ---
        self.preview_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.preview_row.set_hexpand(True)
        self.preview_row.set_halign(Gtk.Align.FILL)
        self.preview_row.set_margin_start(10)

        self.preview_prev_btn = Gtk.Button(label="<")
        self.preview_prev_btn.set_valign(Gtk.Align.CENTER)
        self.preview_prev_btn.set_vexpand(False)
        self.preview_prev_btn.set_size_request(28, 32)
        self.preview_prev_btn.add_css_class("flat")
        self.bind_i18n(self.preview_prev_btn, "tooltip", "o_Scroll_Left")
        self.preview_prev_btn.connect("clicked", self.scroll_preview_left)
        self.preview_prev_btn.set_visible(False)
        self.preview_row.append(self.preview_prev_btn)

        self.preview_scroller = Gtk.ScrolledWindow()
        self.preview_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.preview_scroller.set_hexpand(True)
        self.preview_scroller.set_propagate_natural_height(False)
        self.preview_scroller.set_max_content_height(90)
        self.preview_scroller.set_min_content_height(90)
        self.preview_scroller.set_vexpand(False)
        self.preview_scroller.set_valign(Gtk.Align.END)

        hadj = self.preview_scroller.get_hadjustment()
        if hadj:
            hadj.connect("value-changed", lambda *_: GLib.idle_add(self._update_preview_overflow))

        self.preview_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.preview_hbox.set_halign(Gtk.Align.START)
        self.preview_hbox.set_hexpand(False)
        self.preview_scroller.set_child(self.preview_hbox)
        self.preview_hbox.set_homogeneous(False)

        self.preview_row.append(self.preview_scroller)

        self.preview_next_btn = Gtk.Button(label=">")
        self.preview_next_btn.set_valign(Gtk.Align.CENTER)
        self.preview_next_btn.set_vexpand(False)
        self.preview_next_btn.set_size_request(28, 32)
        self.preview_next_btn.add_css_class("flat")
        self.bind_i18n(self.preview_next_btn, "tooltip", "o_Scroll_Right")
        self.preview_next_btn.connect("clicked", self.scroll_preview_right)
        self.preview_next_btn.set_visible(False)
        self.preview_row.append(self.preview_next_btn)

        self.preview_row.set_visible(False)

        # başta gizli (chat zıplamasın istersen opacity yaklaşımı da kullanabilirsin)
        self.preview_row.set_visible(False)

        self.bottom_spacer = Gtk.Box()
        self.bottom_spacer.set_size_request(-1, 80)
        self.content_box.append(self.bottom_spacer)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_hexpand(True)
        self.scroll.set_vexpand(True)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.content_box)
        overlay.set_child(self.scroll)
        vadj = self.scroll.get_vadjustment()
        vadj.connect("value-changed", self.on_scroll_changed)

        # ---------------- BOTTOM BAR (PREVIEW + INPUT) ----------------
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bottom_bar.set_hexpand(True)
        bottom_bar.set_halign(Gtk.Align.FILL)
        bottom_bar.set_vexpand(False)
        bottom_bar.set_margin_bottom(12)

        bottom_bar.set_valign(Gtk.Align.END)
        bottom_bar.set_vexpand(False)
        overlay.add_overlay(bottom_bar)

        self.setup_file_drop_target(bottom_bar)

        bottom_clamp = Adw.Clamp()
        bottom_clamp.set_hexpand(True)
        bottom_clamp.set_halign(Gtk.Align.FILL)
        bottom_clamp.set_vexpand(False)
        bottom_clamp.props.maximum_size = 800
        bottom_clamp.props.tightening_threshold = 800
        bottom_bar.append(bottom_clamp)

        bottom_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bottom_inner.set_hexpand(True)
        bottom_inner.set_halign(Gtk.Align.FILL)
        bottom_inner.set_vexpand(False)
        bottom_clamp.set_child(bottom_inner)
        self.bottom_inner = bottom_inner

        # Preview satırı (input'un üstünde) + input aynı genişlik/paddingte dursun
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pad.set_hexpand(True)
        pad.set_halign(Gtk.Align.FILL)
        pad.set_vexpand(False)

        # input ile aynı "iç boşluk" hissi için
        pad.set_margin_start(8)
        pad.set_margin_end(8)

        self.preview_parent = pad
        bottom_inner.append(pad)

        # Görünmez holder: preview burada duracak (border/back yok)
        self.preview_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.preview_holder.set_hexpand(True)
        self.preview_holder.set_halign(Gtk.Align.FILL)
        self.preview_holder.set_vexpand(False)

        # input'a çok yakın dursun
        self.preview_holder.set_margin_top(0)
        self.preview_holder.set_margin_bottom(0)

        pad.append(self.preview_holder)

        # Preview satırı (holder içinde)
        self.preview_row.set_valign(Gtk.Align.END)
        self.preview_row.set_vexpand(False)
        self.preview_row.set_hexpand(True)
        self.preview_row.set_halign(Gtk.Align.FILL)

        # ÖNEMLİ: preview_row üzerindeki marginleri 0 tut
        self.preview_row.set_margin_start(0)
        self.preview_row.set_margin_end(0)

        self.preview_holder.append(self.preview_row)

        # Input satırı (preview'un altında)
        input_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        input_wrapper.set_hexpand(True)
        input_wrapper.set_halign(Gtk.Align.FILL)
        input_wrapper.set_valign(Gtk.Align.END)
        input_wrapper.set_vexpand(False)

        pad.append(input_wrapper)

        # refresh'i pad + preview yerleştikten sonra çağır (senin yaptığın doğru)
        self.refresh_attachments_preview()

        # ---------------- SINGLE INPUT CARD ----------------
        input_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        input_card.set_hexpand(True)
        input_card.set_halign(Gtk.Align.FILL)
        input_card.set_vexpand(False)
        input_card.add_css_class("chat-input-box")
        input_wrapper.append(input_card)

        self.input_card = input_card

        # --- TOP: TEXT AREA ---
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_hexpand(True)
        self.textview.set_vexpand(False)
        self.textview.set_size_request(260, -1)
        self.textview.add_css_class("chat-textview")
        self.setup_file_drop_target(self.textview)
        self.textview.set_top_margin(4)
        self.textview.set_bottom_margin(4)
        self.textview.set_left_margin(2)
        self.textview.set_right_margin(2)

        buf = self.textview.get_buffer()
        buf.connect("changed", self.on_input_buffer_changed)

        self.text_scroll = Gtk.ScrolledWindow()
        self.text_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.text_scroll.set_propagate_natural_height(True)
        self.text_scroll.set_min_content_height(35)
        self.text_scroll.set_max_content_height(180)
        self.text_scroll.set_min_content_width(260)
        self.text_scroll.set_max_content_width(9999)
        self.text_scroll.set_vexpand(False)
        self.text_scroll.set_hexpand(True)
        self.text_scroll.set_child(self.textview)

        input_card.append(self.text_scroll)

        # --- BOTTOM: CONTROLS ROW ---
        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls_row.set_hexpand(True)
        controls_row.set_halign(Gtk.Align.FILL)
        controls_row.set_vexpand(False)
        input_card.append(controls_row)

        # sol: attachments
        attach_btn = Gtk.Button(label="📎")
        attach_btn.set_valign(Gtk.Align.CENTER)
        self.bind_i18n(attach_btn, "tooltip", "o_Attachments")
        attach_btn.add_css_class("flat")
        attach_btn.connect("clicked", self.open_attach_menu)
        controls_row.append(attach_btn)

        self.attach_btn = attach_btn

        prompt_btn = Gtk.Button(label="🖍️")
        prompt_btn.set_valign(Gtk.Align.CENTER)
        self.bind_i18n(prompt_btn, "tooltip", "o_Prompt_Chooser")
        prompt_btn.add_css_class("flat")
        prompt_btn.connect("clicked", self.open_prompt_chooser_menu)
        controls_row.append(prompt_btn)

        self.prompt_btn = prompt_btn

        # orta boşluk
        controls_spacer = Gtk.Box()
        controls_spacer.set_hexpand(True)
        controls_row.append(controls_spacer)

        # sağ: mic sonra send
        mic_btn = Gtk.Button(label="🎙️")
        mic_btn.set_valign(Gtk.Align.CENTER)
        self.bind_i18n(mic_btn, "tooltip", "o_Microphone")
        mic_btn.connect("clicked", self.toggle_voice_input)
        mic_btn.add_css_class("flat")
        controls_row.append(mic_btn)

        self.mic_btn = mic_btn
        self._voice_proc = None
        self._last_wav = None

        send_btn = Gtk.Button(label="↑")
        send_btn.set_valign(Gtk.Align.CENTER)
        self.bind_i18n(send_btn, "tooltip", "o_Send_Button")
        send_btn.connect("clicked", self.on_send_button_clicked)
        send_btn.add_css_class("flat")
        controls_row.append(send_btn)

        self.send_btn = send_btn

        # key controller
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_textview_key_pressed)
        self.textview.add_controller(key_controller)

        # (Scroll-to-bottom butonu overlay’de kalabilir; margin_bottom’u input yüksekliğine göre ayarla)
        self.scroll_btn = Gtk.Button(label="↓")
        self.scroll_btn.set_size_request(36, 36)
        self.scroll_btn.set_valign(Gtk.Align.END)
        self.scroll_btn.set_halign(Gtk.Align.END)
        self.scroll_btn.set_margin_end(20)
        self.scroll_btn.add_css_class("scroll-btn")
        self.bind_i18n(self.scroll_btn, "tooltip", "o_Scroll_Down")
        self.scroll_btn.connect("clicked", self.on_scroll_button_clicked)
        self.scroll_btn.set_visible(False)
        overlay.add_overlay(self.scroll_btn)

        # aktif chat modeli yükle
        self.active_model = self.get_chat_model(self.current_chat)

        # load initial
        self.load_chat_list()
        self.load_chat()
        self.apply_chats_visibility()
        self.load_models_list()
        self.apply_models_visibility()

        self.setup_file_drop_target(input_card)
        self.setup_file_drop_target(self.text_scroll)
        self.setup_file_drop_target(self.textview)
        self.setup_file_drop_target(self.content_box)
        self.setup_file_drop_target(self.scroll)
        self.win.present()
        GLib.idle_add(self.force_focus_entry)
        GLib.idle_add(self._update_bottom_spacer_height)

    # ---------------- CSS / THEME ----------------

    def apply_css(self):
        colors = self._get_theme_colors()

        window_bg = colors["window_bg"]
        input_bg = colors["input_bg"]
        user_bg = colors["user_bg"]
        user_text = colors["user_text"]
        bot_bg = colors["bot_bg"]
        bot_text = colors["bot_text"]
        sidebar_text = colors["sidebar_text"]
        local_model_highlight = colors["local_model_highlight"]

        bg_path = self._get_chat_bg_image_path().strip()
        bg_css = ""
        if bg_path:
            try:
                p = Path(bg_path).expanduser().resolve()
                if p.exists():
                    uri = p.as_uri()
                    bg_css = f"""
                    .chat-area {{
                        background-image: url("{uri}");
                        background-repeat: no-repeat;
                        background-position: center center;
                        background-size: cover;
                    }}
                    """
                else:
                    bg_css = """
                    .chat-area {
                        background-image: none;
                    }
                    """
            except Exception:
                bg_css = """
                .chat-area {
                    background-image: none;
                }
                """
        else:
            bg_css = """
            .chat-area {
                background-image: none;
            }
            """


        css = f"""
        .app-root {{
            background: {window_bg};
        }}

        {bg_css}

        .chat-input-box {{
            background: {input_bg};
            border-radius: 22px;
            margin-bottom: 8px;
            padding: 12px 14px 10px 14px;
        }}

        .scroll-btn {{
            background: {input_bg};
            outline: 1px solid rgba(169,169,169,1);
        }}

        window.dark .chat-input-box {{
            background: {input_bg};
            outline: 1px solid rgba(255,255,255,0.08);
        }}

        window.light .chat-input-box {{
            background: {input_bg};
            outline: 1px solid rgba(0,0,0,0.06);
        }}

        .chat-textview {{
            background: transparent;
        }}

        .attach-chip {{
            padding: 6px 10px;
            margin: 3px;
            border-radius: 10px;
            background: rgba(255,255,255,0.06);       
        }}

        window.light .attach-chip {{
            background: rgba(0,0,0,0.06);
        }}

        .attach-x {{
            padding: 0px;
            margin: 0px;
            border-radius: 999px;
            min-width: 18px;
            min-height: 18px;
            font-size: 12px;
        }}

        .attach-edit-on {{
            padding:6px 10px;
            margin: 3px;
            outline: 3px solid #2ecc71;
            border-radius: 10px;
        }}


        .chat-textview {{
            background: transparent;
        }}

        .selected-chat {{
            background: #d0e0ff;
            border-radius: 6px;
        }}

        .sidebar {{
            background: {window_bg};
            color: {sidebar_text};
            padding: 8px;
        }}

        .model-local-label {{
            color: {local_model_highlight};
        }}

        .user-bubble {{
            background: {user_bg};
            color: {user_text};
            border-radius: 12px 5px 12px 12px;
            padding: 10px;
            margin-right: 10px;
        }}

        .bot-bubble {{
            background: {bot_bg};
            color: {bot_text};
            border-radius: 5px 12px 12px;
            padding: 10px;
            margin-left: 10px;
        }}

        .suggestion-wrap {{
            margin-top: 120px;
        }}

        .suggestion-bubble {{
            padding: 10px 14px;
            border-radius: 999px;
            background: rgba(255,255,255,0.08);
        }}

        window.light .suggestion-bubble {{
            background: rgba(0,0,0,0.06);
        }}

        .image-zoom-btn {{
            min-width: 26px;
            min-height: 26px;
            padding: 0;
            border-radius: 999px;
            background: rgba(0,0,0,0.45);
            color: white;
        }}

        window.light .image-zoom-btn {{
            background: rgba(255,255,255,0.85);
            color: black;
        }}

        .user-bubble-ref {{
            background: #237227;   /* koyu yeşil gibi (dark mode) */
            border: 1px solid rgba(255,255,255,0.08);
        }}

        window.light .user-bubble-ref {{
            background: #84B179;   /* koyu yeşil gibi (dark mode) */
            color: #111;
            border: 1px solid rgba(0,0,0,0.08);
        }}

        .action-dim {{
            background: rgba(0,0,0,0.58);
            border-radius: 12px;
        }}

        window.light .action-dim {{
            background: rgba(255,255,255,0.45);
        }}

        .action-center-btn {{
            min-width: 34px;
            min-height: 34px;
            border-radius: 999px;
            background: rgba(0,0,0,0.72);
            color: white;
        }}

        window.light .action-center-btn {{
            background: rgba(255,255,255,0.92);
            color: black;
        }}

        .user-bubble-regen {{
            background: #5a3a8a;  /* koyu mor (dark mode) */
            border: 1px solid rgba(255,255,255,0.08);
        }}

        window.light .user-bubble-regen {{
            background: #e9dcff;  /* açık mor (light mode) */
            color: #111;
            border: 1px solid rgba(0,0,0,0.08);
        }}

        .refs-preview {{
            padding: 6px 8px;
            border-radius: 10px;
            margin-bottom: 6px;
            background: rgba(255,255,255,0.06);
        }}

        .refs-preview-line {{
            opacity: 0.55;
            font-size: 0.90em;
        }}

        window.light .refs-preview {{
            background: rgba(0,0,0,0.06);
        }}

        window.light .refs-preview-line {{
            opacity: 0.65;
        }}

        .refs-groups {{
            margin-bottom: 6px;
        }}

        .hovered {{
            box-shadow: 0 0 6px rgba(0,0,0,0.25);
        }}

        .selected {{
            outline: none;
            box-shadow: inset 0 0 0 2px #FFD54F;
            border-radius: 12px;
        }}


        .code-block {{
            background: #1e1e1e;
            border-radius: 8px;
            padding: 12px;
            font-family: monospace;
        }}

        window.light .code-block {{
            background: #F9F9F9;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
        }}

        .code-copy-btn {{
            color: white;
        }}

        window.light .code-copy-btn {{
            color: #1e1e1e;
        }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )

    def apply_theme(self, is_dark: bool):
        is_dark = bool(is_dark)
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(
                Adw.ColorScheme.FORCE_DARK if is_dark else Adw.ColorScheme.FORCE_LIGHT
                )
        self.win.remove_css_class("dark")
        self.win.remove_css_class("light")
        self.win.add_css_class("dark" if is_dark else "light")

    def toggle_theme(self, *_):
        cfg = ensure_base_config(self.load_config())
        current = cfg["dark_mode"]
        cfg["dark_mode"] = not current
        self.save_config(cfg)

        self.apply_theme(cfg["dark_mode"])
        self.refresh_visual_theme_only()

    # ---------------- INPUT ----------------

    def on_input_buffer_changed(self, *_):
        # Özellikle Ctrl+A + Delete gibi toplu silmelerde
        # layout iki turda oturabiliyor.
        GLib.idle_add(self._update_input_layout_after_text_change)
        GLib.timeout_add(20, self._update_input_layout_after_text_change)

    def _update_input_layout_after_text_change(self):
        try:
            # layout bir tur daha otursun diye spacer hesaplamasını idle'da yap
            self._update_bottom_spacer_height()
        except Exception:
            pass
        return False

    def _update_bottom_spacer_height(self, *_):
        try:
            card = getattr(self, "input_card", None)
            spacer = getattr(self, "bottom_spacer", None)

            if card is None or spacer is None:
                return False

            input_height = int(card.get_allocated_height() or 0)

            # ilk açılış / geçici layout anlarında 0 gelirse
            # minimum güvenli değere dön
            if input_height <= 0:
                input_height = 80

            spacer.set_size_request(-1, input_height + 20)

            # scroll butonu da aynı mantıkla yeniden yerleşsin
            GLib.idle_add(self._sync_scroll_btn_margin)

        except Exception:
            pass

        return False

    def force_focus_entry(self):
        self.textview.grab_focus()
        return False

    def on_textview_key_pressed(self, controller, keyval, keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        buffer = self.textview.get_buffer()

        # Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+A
        if ctrl:
            # Ctrl+A: hepsini seç
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                start, end = buffer.get_bounds()
                buffer.select_range(start, end)
                return True

            # Ctrl+C: seçileni kopyala
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                if buffer.get_has_selection():
                    s, e = buffer.get_selection_bounds()
                    text = buffer.get_text(s, e, True) or ""
                    display = Gdk.Display.get_default()
                    if display:
                        display.get_clipboard().set(text)
                return True

            # Ctrl+X: kes
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                if buffer.get_has_selection():
                    s, e = buffer.get_selection_bounds()
                    text = buffer.get_text(s, e, True) or ""
                    display = Gdk.Display.get_default()
                    if display:
                        display.get_clipboard().set(text)
                    buffer.delete(s, e)
                return True

            # Ctrl+V (ve Ctrl+Shift+V): yapıştır
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                display = Gdk.Display.get_default()
                if not display:
                    return True
                clipboard = display.get_clipboard()

                def on_text(cb, res):
                    try:
                        text = cb.read_text_finish(res) or ""
                    except Exception:
                        text = ""
                    if not text:
                        return

                    # seçili alan varsa üstüne yaz
                    if buffer.get_has_selection():
                        s, e = buffer.get_selection_bounds()
                        buffer.delete(s, e)

                    it = buffer.get_iter_at_mark(buffer.get_insert())
                    buffer.insert(it, text)

                clipboard.read_text_async(None, on_text)
                return True

        # Enter davranışın (sende vardı)
        if keyval == Gdk.KEY_Return:
            if shift:
                return False

            if getattr(self, "is_generating", False):
                GLib.idle_add(self.stop_ai_generation)
            else:
                GLib.idle_add(self.send_message, None)
            return True

        return False

    # ------------------------- AI IMAGES --------------------------------

    def save_base64_image(self, b64_data: str, ext: str = "png") -> str | None:
        try:
            raw = base64.b64decode(b64_data)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            out = GENERATED_DIR / f"gen-{stamp}.{ext}"
            out.write_bytes(raw)
            return str(out.resolve())
        except Exception as e:
            print("save_base64_image error:", e)
            return None


    def download_image_to_cache(self, url: str) -> str | None:
        try:
            import urllib.request

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")

            ext = "png"
            low = url.lower()
            if ".jpg" in low or ".jpeg" in low:
                ext = "jpg"
            elif ".webp" in low:
                ext = "webp"

            out = GENERATED_DIR / f"gen-{stamp}.{ext}"
            urllib.request.urlretrieve(url, str(out))
            return str(out.resolve())
        except Exception as e:
            print("download_image_to_cache error:", e)
            return None

    def update_stream_text(self, text, chat_path_for_ai=None):
        chat_path_for_ai = Path(chat_path_for_ai or self.current_chat)
        text = str(text or "")

        if not text:
            return False

        key = self._chat_key(chat_path_for_ai)
        self.stream_active_chats.add(key)

        if Path(self.current_chat).resolve() == chat_path_for_ai.resolve():
            self.typing_active = False
            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass

        try:
            chat_data = self.load_chat_data_ui(chat_path_for_ai)
            messages = chat_data["messages"]
        except Exception:
            return False

        bot_idx = None

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if (
                isinstance(msg, dict)
                and msg.get("role") in ("bot", "assistant")
                and msg.get("streaming")
            ):
                bot_idx = i
                break

        if bot_idx is None:
            messages.append({
                "role": "bot",
                "content": "",
                "streaming": True
            })
            bot_idx = len(messages) - 1

        messages[bot_idx]["content"] = str(messages[bot_idx].get("content") or "") + text

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data, chat_path_for_ai)
        except Exception:
            pass

        # Ekrandaki aktif chat bu değilse sadece dosyaya yaz, UI'yi elleme
        if Path(self.current_chat) != chat_path_for_ai:
            return False

        # İlk stream parçasında ekranda bubble yoksa sadece bir kere load et
        if key not in self.stream_ui_ready_chats:
            self.stream_ui_ready_chats.add(key)
            self.load_chat()
            self.scroll_to_bottom(force=True)
            return False

        # Sonraki parçalarda tüm chat'i yeniden yükleme.
        # Bu, paralel chatlerde UI callback'lerinin birbirini beklemesine sebep olur.
        if Path(self.current_chat).resolve() == chat_path_for_ai.resolve():
            self.refresh_visible_streaming_message(chat_path_for_ai)
            self.scroll_to_bottom(force=True)

        return False

    def refresh_visible_streaming_message(self, chat_path_for_ai):
        try:
            if Path(self.current_chat).resolve() != Path(chat_path_for_ai).resolve():
                return False

            # Şimdilik güvenli minimal çözüm:
            # load_chat'i her chunk'ta değil, daha seyrek çalıştır.
            now = datetime.now().timestamp()
            last = getattr(self, "_last_stream_ui_refresh", 0)

            if now - last < 0.12:
                return False

            self._last_stream_ui_refresh = now
            self.load_chat()
            return False

        except Exception:
            return False

    def finalize_ai_response(self, raw_text: str, chat_path_for_ai=None):
        chat_path_for_ai = Path(chat_path_for_ai or self.current_chat)
        raw_text = (raw_text or "").strip()

        try:
            data = json.loads(raw_text)
        except Exception:
            data = None

            # stdout içine yanlışlıkla debug satırı karışırsa son JSON objesini yakala
            start = raw_text.rfind("\n{")
            if start != -1:
                candidate = raw_text[start + 1:].strip()
            else:
                start = raw_text.find("{")
                candidate = raw_text[start:].strip() if start != -1 else ""

            if candidate:
                try:
                    data = json.loads(candidate)
                except Exception:
                    data = None

            if not isinstance(data, dict):
                data = {"type": "text", "content": raw_text}

        try:
            chat_data = self.load_chat_data_ui(chat_path_for_ai)
            messages = chat_data["messages"]
        except Exception:
            chat_data = {
                "summary": "",
                "messages": [],
                "code_context": {},
                "memory_chunks": []
            }
            messages = chat_data["messages"]

        # son streaming bot placeholder'ını bul
        bot_idx = None
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "bot" and msg.get("streaming"):
                bot_idx = i
                break

        if bot_idx is None:
            messages.append({"role": "bot", "content": ""})
            bot_idx = len(messages) - 1

        bot_msg = messages[bot_idx]
        bot_msg.pop("streaming", None)

        result_type = str(data.get("type") or "text").strip().lower()
        bot_msg["content"] = str(data.get("content") or "").strip()

        web_req = data.get("web_search_request")

        if isinstance(web_req, dict):
            bot_msg["web_search_request"] = {
                "query": str(web_req.get("query") or "").strip(),
                "status": str(web_req.get("status") or "pending").strip(),
                "err": str(web_req.get("err") or "").strip(),
                "sources": web_req.get("sources") if isinstance(web_req.get("sources"), list) else []
            }
        else:
            bot_msg.pop("web_search_request", None)

        saved_images = []
        seen_saved = set()
        seen_image_keys = set()

        # usage varsa kaydet
        usage = data.get("usage")
        if isinstance(usage, dict):
            bot_msg["usage"] = usage
        else:
            bot_msg.pop("usage", None)

        # 1) URL tabanlı görseller
        image_candidates = []
        inline_data_urls = []

        # tekil alanlar
        for key in ("image", "url"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                sval = val.strip()
                if sval.startswith("data:image/"):
                    inline_data_urls.append(sval)
                else:
                    image_candidates.append(sval)

        imgs = data.get("images")
        if isinstance(imgs, list):
            for item in imgs:
                if isinstance(item, str) and item.strip():
                    sval = item.strip()
                    if sval.startswith("data:image/"):
                        inline_data_urls.append(sval)
                    else:
                        image_candidates.append(sval)

                elif isinstance(item, dict):
                    u = item.get("url")
                    if isinstance(u, str) and u.strip():
                        sval = u.strip()
                        if sval.startswith("data:image/"):
                            inline_data_urls.append(sval)
                        else:
                            image_candidates.append(sval)

                    iu = item.get("image_url")
                    if isinstance(iu, str) and iu.strip():
                        sval = iu.strip()
                        if sval.startswith("data:image/"):
                            inline_data_urls.append(sval)
                        else:
                            image_candidates.append(sval)

                    elif isinstance(iu, dict):
                        uu = iu.get("url")
                        if isinstance(uu, str) and uu.strip():
                            sval = uu.strip()
                            if sval.startswith("data:image/"):
                                inline_data_urls.append(sval)
                            else:
                                image_candidates.append(sval)

        for url in image_candidates:
            local_path = self.download_image_to_cache(url)
            if local_path and local_path not in seen_saved:
                seen_saved.add(local_path)
                saved_images.append(local_path)

        # 2) base64 tabanlı görseller
        base64_candidates = []

        for item in inline_data_urls:
            if item.startswith("data:image/") and "," in item:
                b64 = item.split(",", 1)[1].strip()
                if b64:
                    base64_candidates.append(b64)

        one_b64 = data.get("image_base64")
        if isinstance(one_b64, str) and one_b64.strip():
            base64_candidates.append(one_b64.strip())

        many_b64 = data.get("images_base64")
        if isinstance(many_b64, list):
            for item in many_b64:
                if isinstance(item, str) and item.strip():
                    base64_candidates.append(item.strip())
                elif isinstance(item, dict):
                    b = item.get("b64_json")
                    if isinstance(b, str) and b.strip():
                        base64_candidates.append(b.strip())

        for item in base64_candidates:
            if item.startswith("data:image/") and "," in item:
                item = item.split(",", 1)[1].strip()

            image_key = item[:200] + str(len(item))

            if image_key in seen_image_keys:
                continue

            seen_image_keys.add(image_key)

            local_path = self.save_base64_image(item, "png")
            if local_path and local_path not in seen_saved:
                seen_saved.add(local_path)
                saved_images.append(local_path)

        if saved_images:
            bot_msg["images"] = saved_images

            if not str(bot_msg.get("content") or "").strip():
                bot_msg["content"] = (
                    self("o_Image_Created")
                    if self("o_Image_Created") != "o_Image_Created"
                    else self("o_Image_Created")
                )

        else:
            bot_msg.pop("images", None)

        generated_files = data.get("generated_files")

        if isinstance(generated_files, list):
            clean_files = []

            for item in generated_files:
                if not isinstance(item, dict):
                    continue

                path = str(item.get("path") or "").strip()
                name = str(item.get("name") or "").strip()

                if path and Path(path).exists() and Path(path).is_file():
                    clean_files.append({
                        "path": str(Path(path).resolve()),
                        "name": name or Path(path).name
                    })

            if clean_files:
                bot_msg["generated_files"] = clean_files
            else:
                bot_msg.pop("generated_files", None)
        else:
            bot_msg.pop("generated_files", None)

        messages[bot_idx] = bot_msg

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data, chat_path_for_ai)
        except Exception as e:
            print("finalize_ai_response write error:", e)

    # ---------------- IMAGE,DOCS PREVIEW AND ATTACH MENU ----------------

    def is_debian_like(self) -> bool:
        try:
            os_release = Path("/etc/os-release")
            if not os_release.exists():
                return False

            data = os_release.read_text(encoding="utf-8", errors="ignore").lower()

            keys = []
            for line in data.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k in ("id", "id_like"):
                    keys.append(v)

            joined = " ".join(keys)
            return any(x in joined for x in ("debian", "ubuntu", "raspbian", "linuxmint", "pop"))
        except Exception:
            return False


    def should_use_native_file_chooser(self) -> bool:
        # Debian/Ubuntu/Raspberry tarafında daha stabil fallback
        return self.is_debian_like()

    def open_files_dialog_portable(self, title=None, image_only=False, multiple=True, callback=None):
        if title is None:
            title = self("o_Select_File")
        """
        callback(paths: list[str]) çağrılır
        """
        callback = callback or (lambda paths: None)

        if self.should_use_native_file_chooser():
            self._open_files_dialog_native(title=title, image_only=image_only, multiple=multiple, callback=callback)
        else:
            self._open_files_dialog_modern(title=title, image_only=image_only, multiple=multiple, callback=callback)


    def open_single_file_dialog_portable(self, title=None, image_only=False, callback=None):
        if title is None:
            title = self("o_Select_File")
        """
        callback(path: str | None) çağrılır
        """
        callback = callback or (lambda path: None)

        if self.should_use_native_file_chooser():
            self._open_single_file_dialog_native(title=title, image_only=image_only, callback=callback)
        else:
            self._open_single_file_dialog_modern(title=title, image_only=image_only, callback=callback)


    def _build_file_filters(self, image_only=False):
        img_filter = Gtk.FileFilter()
        img_filter.set_name("Images")
        img_filter.add_mime_type("image/png")
        img_filter.add_mime_type("image/jpeg")
        img_filter.add_mime_type("image/webp")
        img_filter.add_mime_type("image/gif")
        img_filter.add_mime_type("image/bmp")
        img_filter.add_mime_type("image/tiff")

        any_filter = Gtk.FileFilter()
        any_filter.set_name("All files")
        any_filter.add_pattern("*")

        if image_only:
            return [img_filter]
        return [img_filter, any_filter]

    def _open_files_dialog_modern(self, title=None, image_only=False, multiple=True, callback=None):
        if title is None:
            title = self("o_Select_File")
        callback = callback or (lambda paths: None)

        dialog = Gtk.FileDialog()
        filters = self._build_file_filters(image_only=image_only)

        flist = Gio.ListStore.new(Gtk.FileFilter)
        for f in filters:
            flist.append(f)
        dialog.set_filters(flist)

        if multiple:
            def on_done(dlg, res):
                try:
                    files = dlg.open_multiple_finish(res)
                except Exception:
                    callback([])
                    return

                paths = []
                if files:
                    for i in range(files.get_n_items()):
                        gfile = files.get_item(i)
                        if not gfile:
                            continue
                        p = gfile.get_path()
                        if p:
                            paths.append(p)
                callback(paths)

            dialog.open_multiple(self.win, None, on_done)
        else:
            def on_done(dlg, res):
                try:
                    gfile = dlg.open_finish(res)
                except Exception:
                    callback([])
                    return

                if not gfile:
                    callback([])
                    return

                p = gfile.get_path()
                callback([p] if p else [])

            dialog.open(self.win, None, on_done)

    def _open_single_file_dialog_modern(self, title=None, image_only=False, callback=None):
        if title is None:
            title = self("o_Select_File")
        callback = callback or (lambda path: None)

        def _cb(paths):
            callback(paths[0] if paths else None)

        self._open_files_dialog_modern(title=title, image_only=image_only, multiple=False, callback=_cb)

    def _open_files_dialog_native(self, title=None, image_only=False, multiple=True, callback=None):
        if title is None:
            title = self("o_Select_File")
        callback = callback or (lambda paths: None)

        dialog = Gtk.FileChooserDialog(
                title=title,
                transient_for=self.win,
                modal=True,
                action=Gtk.FileChooserAction.OPEN
                )

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Open"), Gtk.ResponseType.ACCEPT)

        dialog.set_select_multiple(bool(multiple))

        for f in self._build_file_filters(image_only=image_only):
            dialog.add_filter(f)

        def on_response(dlg, response):
            paths = []

            try:
                if response == Gtk.ResponseType.ACCEPT:
                    if multiple:
                        model = dlg.get_files()
                        if model:
                            for i in range(model.get_n_items()):
                                gfile = model.get_item(i)
                                if not gfile:
                                    continue
                                p = gfile.get_path()
                                if p:
                                    paths.append(p)
                    else:
                        gfile = dlg.get_file()
                        if gfile:
                            p = gfile.get_path()
                            if p:
                                paths.append(p)
            except Exception as e:
                print("file chooser error:", e)

            dlg.destroy()
            callback(paths)

        dialog.connect("response", on_response)
        dialog.present()

    def _open_single_file_dialog_native(self, title=None, image_only=False, callback=None):
        if title is None:
            title = self("o_Select_File")
        callback = callback or (lambda path: None)

        def _cb(paths):
            callback(paths[0] if paths else None)

        self._open_files_dialog_native(
                title=title,
                image_only=image_only,
                multiple=False,
                callback=_cb
                )



    def _measure_text_px(self, widget: Gtk.Widget, text: str) -> int:
        # widget'in font context'iyle ölçüm yap
        ctx = widget.get_pango_context()
        layout = Pango.Layout.new(ctx)
        layout.set_text(text or "", -1)
        w, _ = layout.get_pixel_size()
        return int(w)

    def show_image_preview(self, image_path):
        p = str(Path(image_path).resolve())
        if Path(p).exists() and p not in self.pending_images:
            self.pending_images.append(p)
        self.refresh_attachments_preview()

    def _capture_photo_from_script(self):
        script = Path.home() / ".config" / "scripts" / "screenprint.sh"
        if not script.exists():
            GLib.idle_add(
                self.handle_ai_error,
                {
                    "role": "bot",
                    "error_key": "o_Script_Not_Found",
                    "error_params": {
                        "path": str(script)
                    }
                }
            )
            return

        watch_dirs = [Path.home() / "Resimler", Path.home() / "Pictures", Path("/tmp")]

        def collect_candidates():
            out = []
            for d in watch_dirs:
                if not d.exists():
                    continue
                out += list(d.glob("screen*.*"))
                out += list(d.glob("capture*.*"))
                out += list(d.glob("shot*.*"))
                out += list(d.glob("*.png"))
                out += list(d.glob("*.jpg"))
                out += list(d.glob("*.jpeg"))
            return [p for p in out if p.exists() and p.is_file()]

        try:
            # Script çalışmadan önce mevcut dosyaları kaydet
            before_files = {}
            for pth in collect_candidates():
                try:
                    before_files[str(pth.resolve())] = pth.stat().st_mtime
                except Exception:
                    pass

            start_ts = datetime.now().timestamp()

            p = subprocess.run(
                [str(script), "only-one"],
                capture_output=True,
                text=True
            )

            # İptal veya hata: eski resmi seçme
            if p.returncode != 0:
                return

            out = (p.stdout or "").strip()

            # 1) Script stdout'a path bastıysa sadece gerçekten yeniyse kabul et
            if out:
                try:
                    cand = Path(out).expanduser().resolve()
                    if cand.exists() and cand.is_file():
                        old_mtime = before_files.get(str(cand))
                        new_mtime = cand.stat().st_mtime

                        if old_mtime is None or new_mtime > old_mtime or new_mtime >= start_ts:
                            GLib.idle_add(self.show_image_preview, str(cand))
                            return
                except Exception:
                    pass

            # 2) Fallback: sadece bu işlem sırasında oluşan / güncellenen dosyaları al
            after_files = []
            for cand in collect_candidates():
                try:
                    resolved = str(cand.resolve())
                    mtime = cand.stat().st_mtime
                    old_mtime = before_files.get(resolved)

                    is_new = old_mtime is None
                    is_updated = old_mtime is not None and mtime > old_mtime
                    is_after_start = mtime >= start_ts

                    if (is_new or is_updated) and is_after_start:
                        after_files.append(cand)
                except Exception:
                    pass

            # Yeni dosya yoksa hiçbir şey yapma
            if not after_files:
                return

            newest = max(after_files, key=lambda x: x.stat().st_mtime)
            GLib.idle_add(self.show_image_preview, str(newest.resolve()))

        except Exception as e:
            GLib.idle_add(
                self.handle_ai_error,
                {
                    "role": "bot",
                    "error_key": "o_Photo_Capture_Error",
                    "error_params": {
                        "error": str(e)
                    }
                }
            )

    def open_attach_menu(self, button: Gtk.Button):
        pop = Gtk.Popover()
        pop.set_parent(button)
        pop.set_position(Gtk.PositionType.TOP)
        pop.set_has_arrow(True)
        pop.set_autohide(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        btn_photo = Gtk.Button(label=f"📸 {self('o_Take_Photo')}")
        btn_file  = Gtk.Button(label=f"📄 {self('o_Attach_File')}")

        btn_photo.set_halign(Gtk.Align.FILL)
        btn_file.set_halign(Gtk.Align.FILL)

        # bubble click'e taşmasın
        self._consume_click(btn_photo)
        self._consume_click(btn_file)

        def _photo_clicked(_b):
            pop.popdown()
            # UI donmasın diye thread
            threading.Thread(target=self._capture_photo_from_script, daemon=True).start()

        def _file_clicked(_b):
            pop.popdown()
            # senin mevcut dosya seçicini aç
            GLib.idle_add(self.on_attach_clicked)

        btn_photo.connect("clicked", _photo_clicked)
        btn_file.connect("clicked", _file_clicked)

        box.append(btn_photo)
        box.append(btn_file)

        pop.set_child(box)
        pop.popup()

    def setup_file_drop_target(self, widget):
        from urllib.parse import urlparse, unquote

        formats = Gdk.ContentFormats.new([
            "text/uri-list",
            "x-special/gnome-copied-files",
        ])

        drop_target = Gtk.DropTargetAsync.new(
            formats,
            Gdk.DragAction.COPY
        )

        def parse_uri_text(text: str):
            paths = []

            for line in (text or "").splitlines():
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if line in ("copy", "cut"):
                    continue

                if line.startswith("file://"):
                    line = unquote(urlparse(line).path)

                p = Path(line).expanduser()

                if p.exists() and p.is_file():
                    paths.append(str(p.resolve()))

            return paths

        def on_accept(_target, drop):
            print("DROP ACCEPT:", drop.get_formats().to_string(), flush=True)
            return True

        def on_drag_enter(_target, drop, x, y):
            print("DROP ENTER:", drop.get_formats().to_string(), flush=True)
            return Gdk.DragAction.COPY

        def on_drag_motion(_target, drop, x, y):
            print("DROP MOTION", flush=True)
            return Gdk.DragAction.COPY

        def on_drop(_target, drop, _x, _y):
            print("ASYNC DROP GELDI", flush=True)
            print("DROP FORMATS:", drop.get_formats().to_string(), flush=True)

            def finish_paths(paths):
                paths = [p for p in paths if p and Path(p).exists() and Path(p).is_file()]
                print("DROP PATHS:", paths, flush=True)

                ok = bool(paths)
                if ok:
                    self.add_dropped_files(paths)
                    GLib.idle_add(self.refresh_attachments_preview)
                    GLib.idle_add(self._sync_scroll_btn_margin)
                
                drop.finish(Gdk.DragAction.COPY if ok else 0)

            # 1) Önce Gdk.FileList oku
            def on_filelist_read(drop_obj, result):
                try:
                    value = drop_obj.read_value_finish(result)
                    files = value.get_files()
                    paths = []

                    for gfile in files:
                        p = gfile.get_path()
                        if p:
                            paths.append(str(Path(p).resolve()))

                    finish_paths(paths)
                    return

                except Exception as e:
                    print("Gdk.FileList read error:", e, flush=True)

                # 2) Olmazsa Gio.File oku
                def on_gfile_read(drop_obj2, result2):
                    try:
                        value2 = drop_obj2.read_value_finish(result2)
                        p = value2.get_path()
                        finish_paths([str(Path(p).resolve())] if p else [])
                        return

                    except Exception as e2:
                        print("Gio.File read error:", e2, flush=True)

                    # 3) En son text/uri-list oku
                    def on_text_read(drop_obj3, result3):
                        try:
                            value3 = drop_obj3.read_value_finish(result3)
                            text = str(value3 or "")
                            print("DROP RAW TEXT:", repr(text), flush=True)
                            finish_paths(parse_uri_text(text))
                            return

                        except Exception as e3:
                            print("text/uri-list read error:", e3, flush=True)
                            drop.finish(False)

                    drop.read_value_async(
                        str,
                        GLib.PRIORITY_DEFAULT,
                        None,
                        on_text_read
                    )

                drop.read_value_async(
                    Gio.File,
                    GLib.PRIORITY_DEFAULT,
                    None,
                    on_gfile_read
                )

            drop.read_value_async(
                Gdk.FileList,
                GLib.PRIORITY_DEFAULT,
                None,
                on_filelist_read
            )

            return True

        drop_target.connect("accept", on_accept)
        drop_target.connect("drag-enter", on_drag_enter)
        drop_target.connect("drag-motion", on_drag_motion)
        drop_target.connect("drop", on_drop)

        widget.add_controller(drop_target)

    def get_prompt_chooser_blocks(self) -> list[str]:
        cfg = ensure_base_config(self.load_config())
        blocks = cfg.get("prompt_chooser_blocks", ["copyable"])

        if not isinstance(blocks, list):
            blocks = ["copyable"]

        valid = {
            "copyable",
            "apply",
            "file_create",
            "web_search",
            "structured",
            "code",
            "pdf_text",
            "pdf_image",
            "pdf_text_image",
        }

        return [str(x) for x in blocks if str(x) in valid]


    def save_prompt_chooser_blocks(self, blocks: list[str]):
        cfg = ensure_base_config(self.load_config())
        cfg["prompt_chooser_blocks"] = list(blocks)
        self.save_config(cfg)


    def open_prompt_chooser_menu(self, button: Gtk.Button):
        pop = Gtk.Popover()
        pop.set_parent(button)
        pop.set_position(Gtk.PositionType.TOP)
        pop.set_has_arrow(True)
        pop.set_autohide(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)

        title = Gtk.Label(label=self("o_Prompt_Chooser"))
        title.set_xalign(0)
        title.add_css_class("dim-label")
        box.append(title)

        selected = set(self.get_prompt_chooser_blocks())

        items = [
            ("copyable", "o_PC_Copyable"),
            ("apply", "o_PC_Apply"),
            ("file_create", "o_PC_FileCreate"),
            ("web_search", "o_PC_WebSearch"),
            ("structured", "o_PC_Structured"),
            ("code", "o_PC_Code"),
            ("pdf_text", "o_PC_PDFText"),
            ("pdf_image", "o_PC_PDFImage"),
            ("pdf_text_image", "o_PC_PDFTextImage"),
        ]

        checks = {}

        def refresh_config(*_):
            new_selected = []
            for key, check in checks.items():
                if check.get_active():
                    new_selected.append(key)

            # Boş listeye izin var
            self.save_prompt_chooser_blocks(new_selected)

        for key, label in items:
            check = Gtk.CheckButton(label=self(label))
            check.set_active(key in selected)
            check.set_halign(Gtk.Align.START)
            check.connect("toggled", refresh_config)
            checks[key] = check
            box.append(check)

        pop.set_child(box)
        pop.popup()

    def add_dropped_files(self, paths):
        import mimetypes

        changed = False

        for path in paths:
            if not path:
                continue

            p = Path(path).expanduser()

            if not p.exists() or not p.is_file():
                continue

            path_str = str(p.resolve())
            mime, _ = mimetypes.guess_type(path_str)
            mime = (mime or "").lower()

            if mime.startswith("image/"):
                if path_str not in self.pending_images:
                    self.pending_images.append(path_str)
                    changed = True
            else:
                if not any(x.get("path") == path_str for x in self.pending_files):
                    self.pending_files.append({
                        "path": path_str,
                        "name": p.name,
                        "edit": False
                    })
                    changed = True

        if changed:
            print("PENDING IMAGES:", self.pending_images)
            print("PENDING FILES:", self.pending_files)

            self.refresh_attachments_preview()
            GLib.idle_add(self.refresh_attachments_preview)
            GLib.idle_add(self._sync_scroll_btn_margin)


    def on_attach_clicked(self, *_):
        def on_paths_selected(paths):
            if not paths:
                return

            import mimetypes

            for path in paths:
                if not path:
                    continue

                mime, _ = mimetypes.guess_type(path)
                mime = (mime or "").lower()

                if mime.startswith("image/"):
                    if path not in self.pending_images:
                        self.pending_images.append(path)
                else:
                    if not any(x.get("path") == path for x in self.pending_files):
                        self.pending_files.append({
                            "path": path,
                            "name": Path(path).name,
                            "edit": False
                            })

            self.refresh_attachments_preview()

        self.open_files_dialog_portable(
                title=self("o_Attach_File"),
                image_only=False,
                multiple=True,
                callback=on_paths_selected
                )

    def refresh_attachments_preview(self):
        try:
            # Preview her zaman preview_holder içinde kalsın
            holder = getattr(self, "preview_holder", None)
            if holder and self.preview_row:
                parent = self.preview_row.get_parent()
                if parent is not holder:
                    if parent is not None:
                        try:
                            parent.remove(self.preview_row)
                        except Exception:
                            pass
                    holder.append(self.preview_row)
        except Exception:
            pass

        was_at_bottom = self.is_at_bottom()
        # temizle
        if not hasattr(self, "preview_hbox"):
            return

        while True:
            c = self.preview_hbox.get_first_child()
            if not c:
                break
            self.preview_hbox.remove(c)

        has_any = bool(self.pending_images or self.pending_files)
        self.preview_row.set_visible(has_any)
        if not has_any:
            if hasattr(self, "preview_next_btn"):
                self.preview_next_btn.set_visible(False)
            return

        # görseller
        for p in list(self.pending_images):
            img_path = Path(p)
            if not img_path.exists():
                continue

            # Kart içeriği
            item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            item.set_halign(Gtk.Align.START)
            item.set_hexpand(False)
            item.set_vexpand(False)
            item.set_size_request(110, -1)

            pic_overlay = self.build_zoomable_image_widget(str(img_path.resolve()), 96, 72)
            pic_overlay.set_hexpand(False)
            pic_overlay.set_vexpand(False)
            pic_overlay.set_halign(Gtk.Align.START)

            name = Gtk.Label(label=img_path.name)
            name.set_xalign(0)
            name.set_hexpand(False)
            name.set_halign(Gtk.Align.START)
            name.set_wrap(False)
            name.set_single_line_mode(True)
            name.set_max_width_chars(18)
            name.set_ellipsize(Pango.EllipsizeMode.END)

            item.append(pic_overlay)
            item.append(name)

            # Overlay: sağ üst X
            overlay_item = Gtk.Overlay()
            overlay_item.set_child(item)

            xbtn = Gtk.Button(label="✕")
            xbtn.add_css_class("flat")
            xbtn.set_focusable(False)
            xbtn.set_halign(Gtk.Align.END)
            xbtn.set_valign(Gtk.Align.START)
            xbtn.set_margin_top(2)
            xbtn.set_margin_end(2)
            xbtn.set_size_request(22, 22)

            self._consume_click(xbtn)

            def _rm_img(_b, path=str(img_path.resolve())):
                try:
                    if path in self.pending_images:
                        self.pending_images.remove(path)
                except Exception:
                    pass
                self.refresh_attachments_preview()

            xbtn.connect("clicked", _rm_img)
            overlay_item.add_overlay(xbtn)

            self.preview_hbox.append(overlay_item)

        # belgeler
        for f in self.pending_files:
            p = Path(f.get("path",""))
            if not p.exists():
                continue

            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip.add_css_class("attach-chip")
            chip.set_hexpand(False)
            chip.set_halign(Gtk.Align.START)

            if f.get("edit"):
                chip.add_css_class("attach-edit-on")

            icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
            chip.append(icon)


            lab = Gtk.Label(label=p.name)
            lab.set_xalign(0.0)
            lab.set_single_line_mode(True)
            lab.set_wrap(False)
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.set_hexpand(True)
            lab.set_halign(Gtk.Align.FILL)
            chip.append(lab)

            # ---- CHIP WIDTH: text ölç -> clamp (min/max) ----
            MIN_PX = 140
            MAX_PX = 180

            # metnin px genişliği
            text_px = self._measure_text_px(lab, p.name)

            # icon + spacing + padding + X butonu için pay (biraz cömert bırak)
            EXTRA = 22 + 6 + 24 + 18   # icon(22) + spacing(6) + x alanı(24) + padding payı(18)

            target = max(MIN_PX, min(MAX_PX, text_px + EXTRA))
            chip.set_size_request(target, -1)



            # tıkla -> edit toggle
            click = Gtk.GestureClick()
            def _on_chip_clicked(_g, _n, _x, _y, item=f):
                item["edit"] = not bool(item.get("edit"))
                self.refresh_attachments_preview()
            click.connect("pressed", _on_chip_clicked)
            chip.add_controller(click)

            overlay_chip = Gtk.Overlay()
            overlay_chip.set_child(chip)

            xbtn = Gtk.Button(label="✕")
            xbtn.add_css_class("attach-x")
            xbtn.set_focusable(False)
            xbtn.set_halign(Gtk.Align.END)
            xbtn.set_valign(Gtk.Align.START)
            xbtn.set_margin_top(2)
            xbtn.set_margin_end(2)
            xbtn.set_size_request(22, 22)

            self._consume_click(xbtn)

            def _rm_file(_b, path=str(p.resolve())):
                try:
                    self.pending_files = [x for x in self.pending_files if str(Path(x.get("path","")).resolve()) != path]
                except Exception:
                    pass
                self.refresh_attachments_preview()

            xbtn.connect("clicked", _rm_file)
            overlay_chip.add_overlay(xbtn)

            self.preview_hbox.append(overlay_chip)

        # taşma var mı? (butonu ona göre aç/kapat)
        GLib.idle_add(self._update_preview_overflow)

        if was_at_bottom:
            GLib.idle_add(self.scroll_to_bottom, True)

        GLib.idle_add(self._sync_scroll_btn_margin)

    def _update_preview_overflow(self):
        try:
            hadj = self.preview_scroller.get_hadjustment()
            if not hadj:
                return False

            upper = hadj.get_upper()
            page = hadj.get_page_size()
            val = hadj.get_value()

            # taşma var mı?
            overflow = upper > page + 2

            # sola kaydırılabiliyor mu?
            can_left = overflow and (val > 2)

            # sağa kaydırılabiliyor mu?
            max_val = max(0, upper - page)
            can_right = overflow and (val < max_val - 2)

            if hasattr(self, "preview_prev_btn"):
                self.preview_prev_btn.set_visible(bool(can_left))
            if hasattr(self, "preview_next_btn"):
                self.preview_next_btn.set_visible(bool(can_right))

        except Exception:
            pass
        return False

    def scroll_preview_right(self, *_):
        try:
            hadj = self.preview_scroller.get_hadjustment()
            if not hadj:
                return
            step = max(120, hadj.get_page_size() * 0.75)
            hadj.set_value(min(hadj.get_upper() - hadj.get_page_size(), hadj.get_value() + step))
        except Exception:
            pass

    def scroll_preview_left(self, *_):
        try:
            hadj = self.preview_scroller.get_hadjustment()
            if not hadj:
                return
            step = max(120, hadj.get_page_size() * 0.75)
            hadj.set_value(max(0, hadj.get_value() - step))
        except Exception:
            pass

    # ---------------- AI ERROR / TYPING ----------------

    def handle_ai_error(self, error_text, chat_path_for_ai=None):
        chat_path_for_ai = Path(chat_path_for_ai or self.current_chat)
        key = self._chat_key(chat_path_for_ai)

        self.stream_active_chats.discard(key)
        self.stream_ui_ready_chats.discard(key)
        self.ai_procs.pop(key, None)
        self.set_generating_state(False, chat_path_for_ai)

        if Path(self.current_chat).resolve() == chat_path_for_ai.resolve():
            self.typing_active = False

        is_active_chat = Path(self.current_chat).resolve() == chat_path_for_ai.resolve()

        if is_active_chat:
            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass

        try:
            chat_data = self.load_chat_data_ui(chat_path_for_ai)
            messages = chat_data["messages"]
        except Exception:
            chat_data = {
                "summary": "",
                "messages": [],
                "code_context": {},
                "memory_chunks": []
            }
            messages = chat_data["messages"]

        # Error gelince bekleyen streaming placeholder'ı temizle
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]

            if not isinstance(msg, dict):
                continue

            if (
                msg.get("role") in ("bot", "assistant")
                and msg.get("streaming")
            ):
                messages.pop(i)
                break

            if msg.get("role") == "user":
                break

        err_msg = None

        # 1) Doğrudan dict geldiyse
        if isinstance(error_text, dict):
            err_msg = dict(error_text)

        else:
            # 2) JSON string geldiyse çöz
            parsed = None
            try:
                parsed = json.loads(str(error_text))
            except Exception:
                parsed = None

            if isinstance(parsed, dict):
                # structured error
                if parsed.get("error_key"):
                    err_msg = {
                        "role": "bot",
                        "error_key": parsed.get("error_key"),
                        "error_params": parsed.get("error_params") or {}
                    }
                # backend'den gelen type=text
                elif parsed.get("type") == "text":
                    err_msg = {
                        "role": "bot",
                        "error_key": "o_Error_With_Detail",
                        "error_params": {
                            "error": str(parsed.get("content") or "").strip()
                        }
                    }

        # 3) Hiçbiri değilse düz string fallback
        if not isinstance(err_msg, dict):
            err_msg = {
                "role": "bot",
                "error_key": "o_Error_With_Detail",
                "error_params": {
                    "error": str(error_text or "").strip()
                }
            }

        messages.append(err_msg)

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data, chat_path_for_ai)
        except Exception:
            pass

        if Path(self.current_chat) == chat_path_for_ai:
            self.load_chat()
            self.scroll_to_bottom(force=True)

    def animate_typing(self):
        if not getattr(self, "typing_active", False):
            self._typing_timer_active = False
            return False

        current_key = self._chat_key(self.current_chat)

        if current_key in self.stream_active_chats:
            self.typing_active = False
            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass

            self._typing_timer_active = False
            return False

        dots = "." * (self.typing_dots % 4)
        self.typing_label.set_text(f"{self('o_Thinking')}{dots}")
        self.typing_dots += 1

        return True

    def show_typing_indicator(self, chat_path=None):
        chat_path = Path(chat_path or self.current_chat)
        key = self._chat_key(chat_path)

        # Bu chat zaten stream'e geçtiyse thinking gösterme
        if key in self.stream_active_chats:
            return

        # Sadece ekrandaki aktif chat için görünür yap
        if Path(self.current_chat).resolve() != chat_path.resolve():
            return

        self.typing_active = True
        self.typing_dots = 0

        self.typing_label.set_text(self("o_Thinking"))
        self.typing_row.set_visible(True)

        if not getattr(self, "_typing_timer_active", False):
            self._typing_timer_active = True
            GLib.timeout_add(500, self.animate_typing)

    def hide_typing_indicator(self):
        self.typing_active = False

        try:
            self.typing_row.set_visible(False)
        except Exception:
            pass

    def chat_has_streaming_placeholder(self, chat_path=None) -> bool:
        try:
            chat_data = self.load_chat_data_ui(chat_path or self.current_chat)
            messages = chat_data.get("messages", [])

            for msg in reversed(messages):
                if not isinstance(msg, dict):
                    continue

                if msg.get("role") in ("bot", "assistant") and msg.get("streaming"):
                    return True

                # Son gerçek kullanıcıdan önceye geçme
                if msg.get("role") == "user":
                    break

        except Exception:
            pass

        return False

    def after_ai_response(self, chat_path_for_ai=None):
        chat_path_for_ai = Path(chat_path_for_ai or self.current_chat)
        key = self._chat_key(chat_path_for_ai)

        if Path(self.current_chat).resolve() == chat_path_for_ai.resolve():
            self.hide_typing_indicator()

        if key in self.ai_stop_requested_chats:
            try:
                chat_data = self.load_chat_data_ui(chat_path_for_ai)
                messages = chat_data["messages"]

                messages.append({
                    "role": "info",
                    "i18n_key": "o_Cancelled"
                })

                chat_data["messages"] = messages
                self.save_chat_data_ui(chat_data, chat_path_for_ai)

            except Exception:
                pass


        self.set_generating_state(False, chat_path_for_ai)

        self.ai_procs.pop(key, None)
        self.ai_stop_requested_chats.discard(key)
        self.stream_active_chats.discard(key)
        self.stream_ui_ready_chats.discard(key)

        if Path(self.current_chat).resolve() == chat_path_for_ai.resolve():
            self.streaming_row = None
            self.streaming_label = None

        if Path(self.current_chat) == chat_path_for_ai:
            try:
                chat_data = self.load_chat_data_ui(chat_path_for_ai)
                messages = chat_data["messages"]

                for msg in reversed(messages):
                    if (
                        isinstance(msg, dict)
                        and msg.get("role") in ("bot", "assistant")
                        and msg.get("streaming")
                    ):
                        msg.pop("streaming", None)
                        break

                chat_data["messages"] = messages
                self.save_chat_data_ui(chat_data, chat_path_for_ai)
            except Exception:
                pass

            self.load_chat()
            self.scroll_to_bottom(force=True)

    # ---------------- MODELS LIST ----------------

    def apply_models_visibility(self):
        if not getattr(self, "sidebar_expanded", True):
            try:
                self.context_mode_row.set_visible(False)
            except Exception:
                pass

            self.models_header.set_visible(False)
            self.models_list.set_visible(False)
            return

        try:
            self.context_mode_row.set_visible(True)
        except Exception:
            pass

        self.models_header.set_visible(True)
        self.models_list.set_visible(self.models_list_open)
        self.models_toggle_btn.set_label(
            f"{self('o_AI_Models')} ▾" if self.models_list_open else f"{self('o_AI_Models')} ▸"
        )

    def on_model_row_activated(self, listbox, row):
        if not row:
            return

        model_id = getattr(row, "model_id", None)
        if not model_id:
            return

        cfg = ensure_base_config(self.load_config())
        found = self._find_model_entry(cfg, model_id)
        is_local = bool(found.get("local", False)) if found else False

        self.set_chat_model(self.current_chat, model_id, is_local)

    def toggle_models_list(self, *_):
        self.models_list_open = not self.models_list_open
        self.apply_models_visibility()

    def refresh_context_mode_switch(self):
        try:
            if not hasattr(self, "context_mode_switch"):
                return

            mode = self.get_context_mode(self.current_chat)

            # switch kapalı: rag
            # switch açık: direct
            self.context_mode_switch.set_active(mode == "direct")

        except Exception:
            pass

    def on_context_mode_switch_changed(self, switch, _pspec):
        mode = "direct" if switch.get_active() else "rag"
        self.set_context_mode(mode, self.current_chat)

    def confirm_delete_model(self, button, model_id: str):
        cfg = ensure_base_config(self.load_config())
        models = self._ensure_min_one_model(cfg)

        if len(models) <= 1:
            dialog = Gtk.Dialog()
            dialog.set_transient_for(self.win)
            self.bind_i18n(dialog, "title", "o_Min_Model_Title")
            dialog.set_modal(True)
            content = dialog.get_content_area()
            label = Gtk.Label()
            self.bind_i18n(label, "label", "o_Min_Model_Error")
            content.append(label)
            dialog.add_button(self("o_OK"), Gtk.ResponseType.OK)
            dialog.connect("response", lambda d, r: d.destroy())
            dialog.present()
            return

        dialog = Gtk.Dialog()
        dialog.set_transient_for(self.win)
        self.bind_i18n(dialog, "title", "o_Delete_Model")
        dialog.set_modal(True)

        content = dialog.get_content_area()
        text = self("o_Delete_Model_Confirm").format(model_id=model_id)
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        content.append(label)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Delete"), Gtk.ResponseType.OK)

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = ensure_base_config(self.load_config())
                models2 = self._ensure_min_one_model(cfg2)

                if len(models2) <= 1:
                    d.destroy()
                    return

                models2 = [m for m in models2 if m["id"] != model_id]
                cfg2["ai_models"] = models2

                new_default = {
                    "id": str(models2[0]["id"]),
                    "local": bool(models2[0].get("local", False))
                } if models2 else {
                    "id": "google/gemini-2.5-flash-image",
                    "local": False
                }

                chat_models = cfg2["chat_models"]
                for k, v in list(chat_models.items()):
                    if isinstance(v, dict):
                        vid = str(v.get("id") or "").strip()
                        if vid == model_id:
                            chat_models[k] = dict(new_default)
                    elif isinstance(v, str) and v == model_id:
                        chat_models[k] = dict(new_default)

                cfg2["chat_models"] = chat_models
                self.save_config(cfg2)

                self.active_model = self.get_chat_model(self.current_chat)
                self.load_models_list()
                self.apply_models_visibility()

            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def open_add_model_dialog(self, *_):
        dialog = Gtk.Dialog()
        dialog.set_transient_for(self.win)
        self.bind_i18n(dialog, "title", "o_Add_Model")
        dialog.set_modal(True)

        content = dialog.get_content_area()

        info = Gtk.Label(label="Model ID:")
        info.set_wrap(True)
        info.set_xalign(0)
        content.append(info)

        entry = Gtk.Entry()
        entry.set_placeholder_text("provider/model (exp: openai/gpt-4.1-mini)")
        content.append(entry)

        local_check = Gtk.CheckButton(label=self("o_Local_Model"))
        content.append(local_check)

        # Ctrl+V ile yapıştırmayı garanti et
        key_controller = Gtk.EventControllerKey()

        def on_key_pressed(controller, keyval, keycode, state):
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

            # hem v hem V yakala
            if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
                display = Gdk.Display.get_default()
                if not display:
                    return False

                clipboard = display.get_clipboard()

                def on_text(cb, res):
                    try:
                        text = cb.read_text_finish(res) or ""
                    except Exception:
                        text = ""
                    if text:
                        entry.set_text(text.strip())

                clipboard.read_text_async(None, on_text)
                return True

            return False

        key_controller.connect("key-pressed", on_key_pressed)
        entry.add_controller(key_controller)

        # Link
        link = Gtk.LinkButton.new_with_label(
                "https://openrouter.ai/models",
                "OpenRouter – Models Page"
                )
        link.set_halign(Gtk.Align.CENTER)
        content.append(link)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Add"), Gtk.ResponseType.OK)

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                model_id = entry.get_text().strip()
                if model_id:
                    is_local = local_check.get_active()
                    self.set_chat_model(self.current_chat, model_id, is_local)
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

        def _focus():
            entry.grab_focus()
            return False
        GLib.idle_add(_focus)

    def load_models_list(self):
        while True:
            row = self.models_list.get_first_child()
            if not row:
                break
            self.models_list.remove(row)

        cfg = ensure_base_config(self.load_config())
        models = self._ensure_min_one_model(cfg)
        active = self.get_chat_model(self.current_chat)
        self.active_model = active

        for model in models:
            model_id = str(model["id"])
            is_local = bool(model.get("local", False))

            row = Gtk.ListBoxRow()
            row.model_id = model_id

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_hexpand(True)

            prefix = "✓ " if active == model_id else ""
            label = Gtk.Label(label=prefix + model_id)
            label.set_xalign(0)
            label.set_hexpand(True)
            label.set_halign(Gtk.Align.FILL)
            label.set_single_line_mode(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(22)

            if is_local:
                label.add_css_class("model-local-label")

            box.append(label)

            trash_btn = Gtk.Button(label="🗑")
            self.bind_i18n(trash_btn, "tooltip", "o_Delete_Model")
            trash_btn.set_size_request(30, 30)
            trash_btn.add_css_class("flat")
            trash_btn.connect("clicked", self.confirm_delete_model, model_id)
            box.append(trash_btn)

            row.set_child(box)
            self.models_list.append(row)

    # ---------------- RENAME POPOVER (ESKİ ÖZELLİK) ----------------

    def open_rename_popover(self, anchor_btn: Gtk.Button, file_path: Path, menu_popover: Gtk.Popover):
        menu_popover.popdown()

        rename_pop = Gtk.Popover()
        rename_pop.set_parent(anchor_btn)
        rename_pop.set_position(Gtk.PositionType.BOTTOM)
        rename_pop.set_has_arrow(True)
        rename_pop.set_autohide(False)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)

        title = Gtk.Label()
        self.bind_i18n(title, "label", "o_New_Name")
        title.set_halign(Gtk.Align.START)
        vbox.append(title)

        entry = Gtk.Entry()
        entry.set_placeholder_text("exp: my_chat")
        entry.set_text(file_path.stem)

        key_controller = Gtk.EventControllerKey()

        def on_key_pressed(_controller, keyval, _keycode, state):
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

            # Ctrl değilse bırak GTK normal davransın
            if not ctrl:
                return False

            display = Gdk.Display.get_default()
            if not display:
                return False
            clipboard = display.get_clipboard()

            # Ctrl+A: hepsini seç
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                entry.select_region(0, -1)
                return True

            # Ctrl+V: yapıştır
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                def on_text(cb, res):
                    try:
                        text = cb.read_text_finish(res) or ""
                    except Exception:
                        text = ""
                    if not text:
                        return

                    # seçili alan varsa sil
                    sel = entry.get_selection_bounds()
                    if sel:
                        s, e = sel
                        entry.delete_text(s, e)

                    pos = entry.get_position()
                    entry.insert_text(text, pos)
                    entry.set_position(pos + len(text))

                clipboard.read_text_async(None, on_text)
                return True

            # Ctrl+X: kes (seçiliyse seçiliyi, değilse tümünü)
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                cur = entry.get_text() or ""
                if not cur:
                    return True

                sel = entry.get_selection_bounds()
                if sel:
                    s, e = sel
                    cut = cur[s:e]
                    clipboard.set(cut)
                    entry.delete_text(s, e)
                else:
                    clipboard.set(cur)
                    entry.set_text("")

                return True

            return False

        key_controller.connect("key-pressed", on_key_pressed)
        entry.add_controller(key_controller)

        def do_rename(*_):
            self.rename_chat_file(file_path, entry.get_text(), rename_pop)

        entry.connect("activate", do_rename)
        vbox.append(entry)

        hint = Gtk.Label()
        self.bind_i18n(hint, "label", "o_Rename_Hint")
        hint.set_halign(Gtk.Align.START)
        hint.add_css_class("dim-label")
        vbox.append(hint)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label=f"{self('o_Cancel')}")
        save_btn = Gtk.Button(label=f"{self('o_Save')}")
        save_btn.add_css_class("suggested-action")

        cancel_btn.connect("clicked", lambda *_: rename_pop.popdown())
        save_btn.connect("clicked", do_rename)

        btn_row.append(cancel_btn)
        btn_row.append(save_btn)
        vbox.append(btn_row)

        rename_pop.set_child(vbox)
        rename_pop.popup()

        # focus/select
        def _focus():
            entry.select_region(0, -1)
            entry.grab_focus()
            return False
        GLib.idle_add(_focus)

    def rename_chat_file(self, old_path: Path, new_name_raw: str, pop: Gtk.Popover):
        new_name = (new_name_raw or "").strip()
        if not new_name:
            return

        for ch in ["/", "\\", "\n", "\r", "\t"]:
            new_name = new_name.replace(ch, "_")

        new_path = old_path.with_name(new_name + ".json")

        if new_path == old_path:
            pop.popdown()
            return

        if new_path.exists():
            print(f"Rename failed: {new_path.name} zaten var.")
            return

        try:
            old_path.rename(new_path)
        except Exception as e:
            print("Rename error:", e)
            return

        # pinned update + last_chat update + chat_models update
        cfg = ensure_base_config(self.load_config())

        plist = cfg["pinned_chats"]
        if old_path.name in plist:
            cfg["pinned_chats"] = [new_path.name if x == old_path.name else x for x in plist]

        if cfg["last_chat"] == old_path.name:
            cfg["last_chat"] = new_path.name

        chat_models = cfg["chat_models"]

        if isinstance(chat_models, dict) and old_path.name in chat_models:
            chat_models[new_path.name] = chat_models.pop(old_path.name)
            cfg["chat_models"] = chat_models

        chat_rag_switch = cfg.setdefault("chat_rag_switch", {})
        if old_path.name in chat_rag_switch:
            chat_rag_switch[new_path.name] = chat_rag_switch.pop(old_path.name)
            cfg["chat_rag_switch"] = chat_rag_switch

        self.save_config(cfg)

        if self.current_chat == old_path:
            self.current_chat = new_path
            self.active_model = self.get_chat_model(self.current_chat)

        pop.popdown()
        self.load_chat_list()
        self.load_models_list()
        self.load_chat()

    # ---------------- PIN TOGGLE (ESKİ ÖZELLİK) ----------------

    def toggle_pin_chat(self, file_path: Path):
        cfg = ensure_base_config(self.load_config())
        pinned = cfg["pinned_chats"]

        name = file_path.name
        if name in pinned:
            pinned.remove(name)
        else:
            pinned.append(name)

        cfg["pinned_chats"] = pinned
        self.save_config(cfg)
        self.load_chat_list()

    # ---------------- SETTINGS ----------------

    def refresh_all_i18n(self, root=None):
        if root is None:
            root = getattr(self, "win", None)

        if root is None:
            return

        self.apply_i18n_to_widget(root)

        try:
            child = root.get_first_child()
        except Exception:
            child = None

        while child is not None:
            self.refresh_all_i18n(child)
            try:
                child = child.get_next_sibling()
            except Exception:
                break

    def refresh_dynamic_i18n_texts(self):
        try:
            self.models_toggle_btn.set_label(
                    f"{self('o_AI_Models')} ▾" if self.models_list_open else f"{self('o_AI_Models')} ▸"
                    )
        except Exception:
            pass

        try:
            self.chats_toggle_btn.set_label(
                    f"{self('o_Chats')} ▾" if self.chats_list_open else f"{self('o_Chats')} ▸"
                    )
        except Exception:
            pass

        try:
            if getattr(self, "typing_active", False):
                self.typing_label.set_text(f"{self('o_Thinking')}...")
            else:
                self.typing_label.set_text(self("o_Thinking"))
        except Exception:
            pass

        try:
            self.set_generating_state(self.is_generating)
        except Exception:
            pass

    # --- CTRL C,V,X,A ---

    def add_entry_clipboard_shortcuts(self, entry_widget, dialog=None):
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_key_pressed(_controller, keyval, _keycode, state):
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

            if ctrl:
                display = Gdk.Display.get_default()
                if not display:
                    return False

                clipboard = display.get_clipboard()

                if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                    entry_widget.grab_focus()
                    entry_widget.select_region(0, -1)
                    return True

                if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                    text = entry_widget.get_text() or ""
                    sel = entry_widget.get_selection_bounds()
                    if sel:
                        s, e = sel
                        clipboard.set(text[s:e])
                    return True

                if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                    text = entry_widget.get_text() or ""
                    sel = entry_widget.get_selection_bounds()

                    if sel:
                        s, e = sel
                        clipboard.set(text[s:e])
                        entry_widget.delete_text(s, e)
                    else:
                        clipboard.set(text)
                        entry_widget.set_text("")

                    return True

                if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                    def on_text(cb, res):
                        try:
                            text = cb.read_text_finish(res) or ""
                        except Exception:
                            text = ""

                        if not text:
                            return

                        sel = entry_widget.get_selection_bounds()
                        if sel:
                            s, e = sel
                            entry_widget.delete_text(s, e)

                        pos = entry_widget.get_position()
                        entry_widget.insert_text(text, pos)
                        entry_widget.set_position(pos + len(text))

                    clipboard.read_text_async(None, on_text)
                    return True

            if dialog and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                dialog.response(Gtk.ResponseType.OK)
                return True

            return False

        key_controller.connect("key-pressed", on_key_pressed)
        entry_widget.add_controller(key_controller)


    def bind_i18n(self, widget, field: str, key: str):
        """
        field:
          - label
          - tooltip
          - title
          - text
          - placeholder
        """
        setattr(widget, f"_i18n_{field}_key", key)
        self.apply_i18n_to_widget(widget)

    def apply_i18n_to_widget(self, widget):
        try:
            label_key = getattr(widget, "_i18n_label_key", None)
            if label_key:
                text = self(str(label_key))
                if hasattr(widget, "set_label"):
                    widget.set_label(text)
        except Exception:
            pass

        try:
            tooltip_key = getattr(widget, "_i18n_tooltip_key", None)
            if tooltip_key:
                text = self(str(tooltip_key))
                if hasattr(widget, "set_tooltip_text"):
                    widget.set_tooltip_text(text)
        except Exception:
            pass

        try:
            title_key = getattr(widget, "_i18n_title_key", None)
            if title_key:
                text = self(str(title_key))
                if hasattr(widget, "set_title"):
                    widget.set_title(text)
        except Exception:
            pass

        try:
            text_key = getattr(widget, "_i18n_text_key", None)
            if text_key:
                text = self(str(text_key))
                if hasattr(widget, "set_text"):
                    widget.set_text(text)
        except Exception:
            pass

        try:
            placeholder_key = getattr(widget, "_i18n_placeholder_key", None)
            if placeholder_key:
                text = self(str(placeholder_key))
                if hasattr(widget, "set_placeholder_text"):
                    widget.set_placeholder_text(text)
        except Exception:
            pass

    def refresh_ui_texts(self):
        self.refresh_all_i18n()
        self.refresh_dynamic_i18n_texts()

        try:
            self.load_chat_list()
        except Exception:
            pass

        try:
            self.load_models_list()
        except Exception:
            pass

        try:
            self.load_chat()
        except Exception:
            pass

    def open_stt_settings_dialog(self):
        cfg = ensure_base_config(self.load_config())

        dialog = Gtk.Dialog(transient_for=self.win)
        self.bind_i18n(dialog, "title", "o_STT_Settings")
        dialog.set_modal(True)
        dialog.set_default_size(520, 380)


        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        # --- Online STT ---
        row_online = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        online_label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        online_label_box.set_hexpand(True)

        online_title = Gtk.Label()
        self.bind_i18n(online_title, "label", "o_Online_STT_Text")
        online_title.set_xalign(0)
        online_title.set_halign(Gtk.Align.START)

        online_desc = Gtk.Label()
        self.bind_i18n(online_desc, "label", "o_Online_STT_Hint")
        online_desc.set_xalign(0)
        online_desc.set_halign(Gtk.Align.START)
        online_desc.set_wrap(True)
        online_desc.add_css_class("dim-label")

        online_label_box.append(online_title)
        online_label_box.append(online_desc)

        online_switch = Gtk.Switch()
        online_switch.set_active(cfg["is_mic_online"])
        online_switch.set_halign(Gtk.Align.END)
        online_switch.set_valign(Gtk.Align.CENTER)

        row_online.append(online_label_box)
        row_online.append(online_switch)
        content.append(row_online)

        # --- Desktop Voice ---
        row_desktop = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        desktop_label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        desktop_label_box.set_hexpand(True)

        desktop_title = Gtk.Label()
        self.bind_i18n(desktop_title, "label", "o_Use_Desktop_Audio_Text")
        desktop_title.set_xalign(0)
        desktop_title.set_halign(Gtk.Align.START)

        desktop_desc = Gtk.Label()
        self.bind_i18n(desktop_desc, "label", "o_Use_Desktop_Audio_Hint")
        desktop_desc.set_xalign(0)
        desktop_desc.set_halign(Gtk.Align.START)
        desktop_desc.set_wrap(True)
        desktop_desc.add_css_class("dim-label")

        desktop_label_box.append(desktop_title)
        desktop_label_box.append(desktop_desc)

        desktop_switch = Gtk.Switch()
        desktop_switch.set_active(cfg["use_desktop_voice"])
        desktop_switch.set_halign(Gtk.Align.END)
        desktop_switch.set_valign(Gtk.Align.CENTER)

        row_desktop.append(desktop_label_box)
        row_desktop.append(desktop_switch)
        content.append(row_desktop)

        # --- Timeout ---
        timeout_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        timeout_switch = Gtk.Switch()
        timeout_switch.set_active(bool(cfg.get("use_stt_timeout", False)))

        timeout_label = Gtk.Label(label=self("o_Use_Timeout"))
        timeout_label.set_xalign(0)
        timeout_label.set_hexpand(True)

        timeout_entry = Gtk.Entry()
        timeout_entry.set_text(str(cfg.get("stt_timeout", "10")))
        timeout_entry.set_width_chars(6)

        timeout_row.append(timeout_label)
        timeout_row.append(timeout_switch)
        timeout_row.append(timeout_entry)
        content.append(timeout_row)

        # --- Silence ---
        silence_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        silence_switch = Gtk.Switch()
        silence_switch.set_active(bool(cfg.get("use_stt_silence", True)))

        silence_label = Gtk.Label(label=self("o_Use_Silence_Auto_Stop"))
        silence_label.set_xalign(0)
        silence_label.set_hexpand(True)

        silence_entry = Gtk.Entry()
        silence_entry.set_text(str(cfg.get("stt_silence_duration", "2")))
        silence_entry.set_width_chars(6)

        silence_row.append(silence_label)
        silence_row.append(silence_switch)
        silence_row.append(silence_entry)
        content.append(silence_row)

        # --- Online model ---
        stt_model_label = Gtk.Label()
        self.bind_i18n(stt_model_label, "label", "o_Online_STT_Model")
        stt_model_label.set_xalign(0)
        content.append(stt_model_label)

        stt_model_entry = Gtk.Entry()
        stt_model_entry.set_placeholder_text("openai/gpt-audio-mini")
        stt_model_entry.set_text(str(cfg["stt_model_online"]))
        content.append(stt_model_entry)
        self.add_entry_clipboard_shortcuts(stt_model_entry)

        # --- whisper.cpp binary ---
        whisper_bin_label = Gtk.Label(label=f"whisper.cpp {self('o_Binary_Path')}")
        whisper_bin_label.set_xalign(0)
        content.append(whisper_bin_label)

        whisper_bin_entry = Gtk.Entry()
        whisper_bin_entry.set_placeholder_text("/home/usr/whisper.cpp/build/bin/whisper-cli")
        whisper_bin_entry.set_text(str(cfg["whisper_cpp_bin"]))
        content.append(whisper_bin_entry)
        self.add_entry_clipboard_shortcuts(whisper_bin_entry)

        # --- whisper.cpp model ---
        whisper_model_label = Gtk.Label(label=f"whisper.cpp {self('o_Model_Path')}")
        whisper_model_label.set_xalign(0)
        content.append(whisper_model_label)

        whisper_model_entry = Gtk.Entry()
        whisper_model_entry.set_placeholder_text("/home/usr/.local/share/whisper/ggml-tiny.bin")
        whisper_model_entry.set_text(str(cfg["whisper_cpp_model"]))
        content.append(whisper_model_entry)
        self.add_entry_clipboard_shortcuts(whisper_model_entry)

        # Link
        link = Gtk.LinkButton.new_with_label(
                "https://openrouter.ai/models?fmt=cards&input_modalities=audio",
                "OpenRouter – STT Models Page"
                )
        link.set_halign(Gtk.Align.CENTER)
        content.append(link)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)

        def refresh_sensitive_state(*_):
            is_online = bool(online_switch.get_active())

            stt_model_label.set_sensitive(is_online)
            stt_model_entry.set_sensitive(is_online)

            whisper_bin_label.set_sensitive(not is_online)
            whisper_bin_entry.set_sensitive(not is_online)
            whisper_model_label.set_sensitive(not is_online)
            whisper_model_entry.set_sensitive(not is_online)

            timeout_entry.set_sensitive(bool(timeout_switch.get_active()))
            silence_entry.set_sensitive(bool(silence_switch.get_active()))


        online_switch.connect("notify::active", refresh_sensitive_state)
        timeout_switch.connect("notify::active", refresh_sensitive_state)
        silence_switch.connect("notify::active", refresh_sensitive_state)

        refresh_sensitive_state()

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = ensure_base_config(self.load_config())

                cfg2["is_mic_online"] = bool(online_switch.get_active())
                cfg2["use_desktop_voice"] = bool(desktop_switch.get_active())
                cfg2["stt_model_online"] = stt_model_entry.get_text().strip()
                cfg2["whisper_cpp_bin"] = whisper_bin_entry.get_text().strip()
                cfg2["whisper_cpp_model"] = whisper_model_entry.get_text().strip()
                cfg2["use_stt_timeout"] = bool(timeout_switch.get_active())
                cfg2["stt_timeout"] = timeout_entry.get_text().strip() or "10"
                cfg2["use_stt_silence"] = bool(silence_switch.get_active())
                cfg2["stt_silence_duration"] = silence_entry.get_text().strip() or "2"

                self.save_config(cfg2)

            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _show_info_dialog(self, title: str, message: str):
        dialog = Gtk.Dialog(transient_for=self.win)
        dialog.set_modal(True)
        dialog.set_title(title)

        content = dialog.get_content_area()
        label = Gtk.Label(label=message)
        label.set_wrap(True)
        label.set_xalign(0)
        content.append(label)

        dialog.add_button(self("o_OK"), Gtk.ResponseType.OK)
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()


    def _run_provider_command(self, command: str, provider_name: str, action_name: str):
        cmd = str(command or "").strip()
        if not cmd:
            self._show_info_dialog(
                f"{action_name} {self('o_Error')}",
                self("o_Provider_Command_No_Command", provider=provider_name)
            )
            return

        try:
            # bash ile çalıştır: terminaldeki quote davranışına daha yakın olur
            p = subprocess.Popen(
                ["bash", "-lc", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

            def _wait():
                try:
                    out, err = p.communicate(timeout=15)
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    try:
                        p.kill()
                    except Exception:
                        pass
                    GLib.idle_add(
                        self._show_info_dialog,
                        f"{action_name} {self('o_Timeout')}",
                        self("o_Provider_Command_Timeout", provider=provider_name, command=cmd)
                    )
                    return
                except Exception as e:
                    GLib.idle_add(
                        self._show_info_dialog,
                        f"{action_name} {self('o_Error')}",
                        self("o_Provider_Command_Error", provider=provider_name, error=e)
                    )
                    return

                out = (out or "").strip()
                err = (err or "").strip()

                if rc == 0:
                    msg = self("o_Provider_Command_Success", provider=provider_name, command=cmd)
                    if out:
                        msg += f"\n\nOutput:\n{out[:1200]}"
                    GLib.idle_add(self._show_info_dialog, action_name, msg)
                else:
                    msg = self("o_Provider_Command_Failed", provider=provider_name, command=cmd, code=rc)
                    if err:
                        msg += f"\n\nError:\n{err[:1200]}"
                    elif out:
                        msg += f"\n\nOutput:\n{out[:1200]}"
                    GLib.idle_add(self._show_info_dialog, f"{action_name} {self('o_Failed')}", msg)

            threading.Thread(target=_wait, daemon=True).start()

        except Exception as e:
            self._show_info_dialog(
                    f"{action_name} {self('o_Error')}",
                self("o_Provider_Command_Error", provider=provider_name, error=e)
            )


    def stop_selected_provider(self, provider_name: str, provider_cfg: dict):
        stop_cmd = str((provider_cfg or {}).get("stop_command") or "").strip()
        self._run_provider_command(stop_cmd, provider_name, self("o_Stop_Provider"))

    def open_ai_settings_dialog(self):
        cfg = ensure_base_config(self.load_config())

        dialog = Gtk.Dialog(transient_for=self.win)
        self.bind_i18n(dialog, "title", "o_AI_Settings")
        dialog.set_modal(True)
        dialog.set_default_size(520, 380)


        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        # --- Response Language ---
        row_response = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        response_label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        response_label_box.set_hexpand(True)

        response_title = Gtk.Label()
        self.bind_i18n(response_title, "label", "o_Force_Language_Text")
        response_title.set_xalign(0)
        response_title.set_halign(Gtk.Align.START)

        response_desc = Gtk.Label()
        self.bind_i18n(response_desc, "label", "o_Force_Language_Hint")
        response_desc.set_xalign(0)
        response_desc.set_halign(Gtk.Align.START)
        response_desc.set_wrap(True)
        response_desc.add_css_class("dim-label")

        response_label_box.append(response_title)
        response_label_box.append(response_desc)

        response_switch = Gtk.Switch()
        response_switch.set_active(cfg["force_ui_language"])
        response_switch.set_halign(Gtk.Align.END)
        response_switch.set_valign(Gtk.Align.CENTER)

        row_response.append(response_label_box)
        row_response.append(response_switch)
        content.append(row_response)

        # --- ASK FOR WEB SEARCH ---

        row_ask_web = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        ask_web_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ask_web_box.set_hexpand(True)

        ask_web_title = Gtk.Label()
        self.bind_i18n(ask_web_title, "label", "o_Ask_Web_Search")
        ask_web_title.set_xalign(0)
        ask_web_title.set_halign(Gtk.Align.START)

        ask_web_desc = Gtk.Label()
        self.bind_i18n(ask_web_desc, "label", "o_Ask_Web_Search_Hint")
        ask_web_desc.set_xalign(0)
        ask_web_desc.set_halign(Gtk.Align.START)
        ask_web_desc.set_wrap(True)
        ask_web_desc.add_css_class("dim-label")

        ask_web_box.append(ask_web_title)
        ask_web_box.append(ask_web_desc)

        ask_web_switch = Gtk.Switch()
        ask_web_switch.set_active(bool(cfg.get("ask_for_web_search", True)))
        ask_web_switch.set_halign(Gtk.Align.END)
        ask_web_switch.set_valign(Gtk.Align.CENTER)

        row_ask_web.append(ask_web_box)
        row_ask_web.append(ask_web_switch)
        content.append(row_ask_web)

        # --- AI Providers ---
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.append(sep)

        local_title = Gtk.Label()
        self.bind_i18n(local_title, "label", "o_AI_Providers")
        local_title.set_xalign(0)
        content.append(local_title)

        local_providers = cfg.get("local_providers", {})
        if not isinstance(local_providers, dict):
            local_providers = {}

        provider_defaults = {
            "enabled": False,
            "base_url": "http://127.0.0.1:11434",
            "run_startup": "",
            "stop_command": "",
            "system_error": "",
            "temperature": "",
            "top_p": "",
            "top_k": "",
            "repeat_penalty": "",
            "num_ctx": "",
            "num_predict": "",
            "keep_alive": "",
            "system_prompt": ""
        }

        if not local_providers:
            local_providers = {
                "ollama": dict(provider_defaults)
            }

        provider_names = list(local_providers.keys())
        selected_provider_name = provider_names[0]

        def merged_provider_cfg(provider_name: str) -> dict:
            out = dict(provider_defaults)
            raw = local_providers.get(provider_name, {})
            if isinstance(raw, dict):
                out.update(raw)
            return out

        local_cfg = merged_provider_cfg(selected_provider_name)

        provider_select_label = Gtk.Label()
        self.bind_i18n(provider_select_label, "label", "o_Selected_Provider")
        provider_select_label.set_xalign(0)
        content.append(provider_select_label)

        provider_select_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        provider_select_row.set_hexpand(True)
        content.append(provider_select_row)

        provider_string_list = Gtk.StringList.new(provider_names)
        provider_dropdown = Gtk.DropDown.new(provider_string_list, None)
        provider_dropdown.set_hexpand(True)
        provider_dropdown.set_halign(Gtk.Align.FILL)
        provider_dropdown.set_selected(provider_names.index(selected_provider_name))
        provider_select_row.append(provider_dropdown)

        add_provider_btn = Gtk.Button(label="+")
        self.bind_i18n(add_provider_btn, "tooltip", "o_Add_Provider")
        add_provider_btn.set_size_request(34, 34)
        provider_select_row.append(add_provider_btn)

        stop_provider_btn = Gtk.Button(label="⏹")
        self.bind_i18n(stop_provider_btn, "tooltip", "o_Stop_Provider")
        stop_provider_btn.set_size_request(34, 34)
        stop_provider_btn.add_css_class("flat")
        provider_select_row.append(stop_provider_btn)

        delete_provider_btn = Gtk.Button(label="🗑")
        self.bind_i18n(delete_provider_btn, "tooltip", "o_Delete_Provider")
        delete_provider_btn.set_size_request(34, 34)
        delete_provider_btn.add_css_class("flat")
        provider_select_row.append(delete_provider_btn)

        local_provider_name_label = Gtk.Label()
        self.bind_i18n(local_provider_name_label, "label", "o_Provider_Name")
        local_provider_name_label.set_xalign(0)
        content.append(local_provider_name_label)

        local_provider_name_entry = Gtk.Entry()
        local_provider_name_entry.set_text(selected_provider_name)
        local_provider_name_entry.set_placeholder_text("ollama")
        content.append(local_provider_name_entry)
        self.add_entry_clipboard_shortcuts(local_provider_name_entry)

        row_local_enabled = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        local_enabled_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        local_enabled_box.set_hexpand(True)

        local_enabled_title = Gtk.Label()
        self.bind_i18n(local_enabled_title, "label", "o_Enable_Provider")
        local_enabled_title.set_xalign(0)
        local_enabled_title.set_halign(Gtk.Align.START)

        local_enabled_desc = Gtk.Label()
        self.bind_i18n(local_enabled_desc, "label", "o_Enable_Provider_Hint")
        local_enabled_desc.set_xalign(0)
        local_enabled_desc.set_halign(Gtk.Align.START)
        local_enabled_desc.set_wrap(True)
        local_enabled_desc.add_css_class("dim-label")

        local_enabled_box.append(local_enabled_title)
        local_enabled_box.append(local_enabled_desc)

        local_enabled_switch = Gtk.Switch()
        local_enabled_switch.set_active(bool(local_cfg.get("enabled", False)))
        local_enabled_switch.set_halign(Gtk.Align.END)
        local_enabled_switch.set_valign(Gtk.Align.CENTER)

        row_local_enabled.append(local_enabled_box)
        row_local_enabled.append(local_enabled_switch)
        content.append(row_local_enabled)

        local_base_url_label = Gtk.Label(label="Base URL")
        local_base_url_label.set_xalign(0)
        content.append(local_base_url_label)

        local_base_url_entry = Gtk.Entry()
        local_base_url_entry.set_text(str(local_cfg.get("base_url", "")))
        local_base_url_entry.set_placeholder_text("http://127.0.0.1:11434")
        content.append(local_base_url_entry)
        self.add_entry_clipboard_shortcuts(local_base_url_entry)

        local_run_startup_label = Gtk.Label()
        self.bind_i18n(local_run_startup_label, "label", "o_Run_Startup")
        local_run_startup_label.set_xalign(0)
        content.append(local_run_startup_label)

        local_run_startup_entry = Gtk.Entry()
        local_run_startup_entry.set_text(str(local_cfg.get("run_startup", "")))
        self.bind_i18n(local_run_startup_entry, "placeholder", "o_Run_Startup_Placeholder")
        content.append(local_run_startup_entry)
        self.add_entry_clipboard_shortcuts(local_run_startup_entry)

        local_stop_command_label = Gtk.Label()
        self.bind_i18n(local_stop_command_label, "label", "o_Stop_Command")
        local_stop_command_label.set_xalign(0)
        content.append(local_stop_command_label)

        local_stop_command_entry = Gtk.Entry()
        local_stop_command_entry.set_text(str(local_cfg.get("stop_command", "")))
        self.bind_i18n(local_stop_command_entry, "placeholder", "o_Stop_Command_Placeholder")
        content.append(local_stop_command_entry)
        self.add_entry_clipboard_shortcuts(local_stop_command_entry)

        local_system_error_label = Gtk.Label()
        self.bind_i18n(local_system_error_label, "label", "o_Custom_Error")
        local_system_error_label.set_xalign(0)
        content.append(local_system_error_label)

        local_system_error_entry = Gtk.Entry()
        local_system_error_entry.set_text(str(local_cfg.get("system_error", "")))
        self.bind_i18n(local_system_error_entry, "placeholder", "o_Custom_Error_Placeholder")
        content.append(local_system_error_entry)
        self.add_entry_clipboard_shortcuts(local_system_error_entry)

        def add_local_param_row(title_text, key, placeholder=""):
            lab = Gtk.Label(label=title_text)
            lab.set_xalign(0)
            content.append(lab)

            entry = Gtk.Entry()
            self.add_entry_clipboard_shortcuts(entry)
            entry.set_text(str(local_cfg.get(key, "")))
            if placeholder:
                entry.set_placeholder_text(placeholder)
            content.append(entry)
            return entry

        local_temperature_entry = add_local_param_row("Temperature", "temperature", "0.7")
        local_top_p_entry = add_local_param_row("Top P", "top_p", "0.9")
        local_top_k_entry = add_local_param_row("Top K", "top_k", "40")
        local_repeat_penalty_entry = add_local_param_row("Repeat Penalty", "repeat_penalty", "1.1")
        local_num_ctx_entry = add_local_param_row("Num Ctx", "num_ctx", "4096")
        local_num_predict_entry = add_local_param_row("Num Predict", "num_predict", "512")
        local_keep_alive_entry = add_local_param_row("Keep Alive", "keep_alive", "5m")
        local_system_prompt_entry = add_local_param_row(self("o_System_Prompt"), "system_prompt", "")

        local_link_ollama = Gtk.LinkButton.new_with_label(
            "https://docs.ollama.com/api/chat",
            "Ollama – Local API"
        )
        local_link_ollama.set_halign(Gtk.Align.CENTER)
        content.append(local_link_ollama)

        local_link_lmstudio = Gtk.LinkButton.new_with_label(
            "https://lmstudio.ai/docs/developer",
            "LM Studio – Local API"
        )
        local_link_lmstudio.set_halign(Gtk.Align.CENTER)
        content.append(local_link_lmstudio)

        local_link_vllm = Gtk.LinkButton.new_with_label(
            "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
            "vLLM – OpenAI-Compatible API"
        )
        local_link_vllm.set_halign(Gtk.Align.CENTER)
        content.append(local_link_vllm)



        def get_current_form_data():
            return {
                "enabled": bool(local_enabled_switch.get_active()),
                "base_url": local_base_url_entry.get_text().strip() or "http://127.0.0.1:11434",
                "run_startup": local_run_startup_entry.get_text().strip(),
                "stop_command": local_stop_command_entry.get_text().strip(),
                "system_error": local_system_error_entry.get_text().strip(),
                "temperature": local_temperature_entry.get_text().strip(),
                "top_p": local_top_p_entry.get_text().strip(),
                "top_k": local_top_k_entry.get_text().strip(),
                "repeat_penalty": local_repeat_penalty_entry.get_text().strip(),
                "num_ctx": local_num_ctx_entry.get_text().strip(),
                "num_predict": local_num_predict_entry.get_text().strip(),
                "keep_alive": local_keep_alive_entry.get_text().strip(),
                "system_prompt": local_system_prompt_entry.get_text().strip(),
            }

        def set_form_data(provider_name: str):
            cfg_map = merged_provider_cfg(provider_name)

            local_provider_name_entry.set_text(provider_name)
            local_enabled_switch.set_active(bool(cfg_map.get("enabled", False)))
            local_base_url_entry.set_text(str(cfg_map.get("base_url", "")))
            local_run_startup_entry.set_text(str(cfg_map.get("run_startup", "")))
            local_run_startup_entry.set_placeholder_text(f"Exp: ollama serve")
            local_stop_command_entry.set_text(str(cfg_map.get("stop_command", "")))
            local_system_error_entry.set_text(str(cfg_map.get("system_error", "")))
            local_temperature_entry.set_text(str(cfg_map.get("temperature", "")))
            local_top_p_entry.set_text(str(cfg_map.get("top_p", "")))
            local_top_k_entry.set_text(str(cfg_map.get("top_k", "")))
            local_repeat_penalty_entry.set_text(str(cfg_map.get("repeat_penalty", "")))
            local_num_ctx_entry.set_text(str(cfg_map.get("num_ctx", "")))
            local_num_predict_entry.set_text(str(cfg_map.get("num_predict", "")))
            local_keep_alive_entry.set_text(str(cfg_map.get("keep_alive", "")))
            local_system_prompt_entry.set_text(str(cfg_map.get("system_prompt", "")))

        def write_local_providers_to_config():
            cfg_now = ensure_base_config(self.load_config())
            cfg_now["local_providers"] = dict(local_providers)
            self.save_config(cfg_now)

        def refresh_provider_dropdown(selected_name: str | None = None):
            nonlocal provider_names, provider_string_list, selected_provider_name

            provider_names = list(local_providers.keys())
            if not provider_names:
                local_providers["ollama"] = dict(provider_defaults)
                provider_names = ["ollama"]

            provider_string_list = Gtk.StringList.new(provider_names)
            provider_dropdown.set_model(provider_string_list)

            if selected_name and selected_name in provider_names:
                selected_provider_name = selected_name
                provider_dropdown.set_selected(provider_names.index(selected_name))
            else:
                selected_provider_name = provider_names[0]
                provider_dropdown.set_selected(0)

            set_form_data(selected_provider_name)

        def on_provider_changed(dropdown, _pspec):
            nonlocal selected_provider_name

            idx = dropdown.get_selected()
            if idx == Gtk.INVALID_LIST_POSITION:
                return

            old_name = selected_provider_name
            new_name = provider_names[idx]

            if old_name and old_name in local_providers:
                local_providers[old_name] = get_current_form_data()

            selected_provider_name = new_name
            set_form_data(new_name)

        provider_dropdown.connect("notify::selected", on_provider_changed)

        def on_add_provider_clicked(_btn):
            nonlocal selected_provider_name

            if selected_provider_name and selected_provider_name in local_providers:
                local_providers[selected_provider_name] = get_current_form_data()

            base_name = "new_provider"
            new_name = base_name
            i = 1
            while new_name in local_providers:
                new_name = f"{base_name}_{i}"
                i += 1

            local_providers[new_name] = dict(provider_defaults)
            write_local_providers_to_config()
            refresh_provider_dropdown(new_name)

            local_provider_name_entry.grab_focus()
            local_provider_name_entry.select_region(0, -1)

        add_provider_btn.connect("clicked", on_add_provider_clicked)

        def on_delete_provider_clicked(_btn):
            nonlocal selected_provider_name

            if not selected_provider_name or selected_provider_name not in local_providers:
                return

            if len(local_providers) <= 1:
                warn = Gtk.Dialog(transient_for=self.win)
                warn.set_modal(True)
                warn.set_title(self("o_Provider_Cant_Be_Deleted"))

                warn_content = warn.get_content_area()
                warn_label = Gtk.Label(label=self("o_Provider_Min_One_Required"))
                warn_label.set_wrap(True)
                warn_content.append(warn_label)

                warn.add_button(self("o_OK"), Gtk.ResponseType.OK)
                warn.connect("response", lambda d, r: d.destroy())
                warn.present()
                return

            confirm = Gtk.Dialog(transient_for=self.win)
            confirm.set_modal(True)
            confirm.set_title(self("o_Delete_Provider"))

            confirm_content = confirm.get_content_area()
            confirm_label = Gtk.Label(
                label=self("o_Delete_Provider_Confirm", provider=selected_provider_name)
            )
            confirm_label.set_wrap(True)
            confirm_content.append(confirm_label)

            confirm.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
            confirm.add_button(self("o_Delete"), Gtk.ResponseType.OK)

            def on_confirm_response(dlg, response):
                nonlocal selected_provider_name

                if response == Gtk.ResponseType.OK:
                    old_name = selected_provider_name
                    if old_name in local_providers:
                        del local_providers[old_name]

                    write_local_providers_to_config()

                    remaining = list(local_providers.keys())
                    next_name = remaining[0] if remaining else "ollama"
                    refresh_provider_dropdown(next_name)

                dlg.destroy()

            confirm.connect("response", on_confirm_response)
            confirm.present()

        delete_provider_btn.connect("clicked", on_delete_provider_clicked)

        def on_stop_provider_clicked(_btn):
            nonlocal selected_provider_name

            if not selected_provider_name:
                return

            # dialog içindeki henüz kaydedilmemiş form verisini de kullan
            current_cfg = get_current_form_data()
            self.stop_selected_provider(selected_provider_name, current_cfg)

        stop_provider_btn.connect("clicked", on_stop_provider_clicked)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = ensure_base_config(self.load_config())

                cfg2["force_ui_language"] = bool(response_switch.get_active())
                cfg2["ask_for_web_search"] = bool(ask_web_switch.get_active())

                old_selected_name = selected_provider_name
                new_provider_name = local_provider_name_entry.get_text().strip() or "ollama"

                if "local_providers" not in cfg2 or not isinstance(cfg2["local_providers"], dict):
                    cfg2["local_providers"] = {}

                if old_selected_name in local_providers:
                    local_providers[old_selected_name] = get_current_form_data()

                if new_provider_name != old_selected_name:
                    if new_provider_name in local_providers and new_provider_name != old_selected_name:
                        suffix = 1
                        candidate = f"{new_provider_name}_{suffix}"
                        while candidate in local_providers:
                            suffix += 1
                            candidate = f"{new_provider_name}_{suffix}"
                        new_provider_name = candidate

                    local_providers[new_provider_name] = local_providers.pop(old_selected_name)

                cfg2["local_providers"] = dict(local_providers)

                self.save_config(cfg2)

            d.close()

        dialog.connect("response", on_response)
        dialog.present()


    def open_rag_settings_dialog(self):
        cfg = ensure_base_config(self.load_config())
        rag = cfg["rag_settings"]

        dialog = Gtk.Dialog(transient_for=self.win)
        self.bind_i18n(dialog, "title", "o_RAG_Settings")
        dialog.set_modal(True)
        dialog.set_default_size(520, 520)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        entries = {}
        switches = {}

        def add_int_row(label_key, hint_key, setting_key):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_hexpand(True)

            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            left.set_hexpand(True)

            title = Gtk.Label()
            self.bind_i18n(title, "label", label_key)
            title.set_xalign(0)
            title.set_halign(Gtk.Align.START)

            hint = Gtk.Label()
            self.bind_i18n(hint, "label", hint_key)
            hint.set_xalign(0)
            hint.set_halign(Gtk.Align.START)
            hint.set_wrap(True)
            hint.add_css_class("dim-label")

            left.append(title)
            left.append(hint)

            entry = Gtk.Entry()
            entry.set_text(str(rag.get(setting_key, "")))
            entry.set_width_chars(8)
            entry.set_max_width_chars(10)
            entry.set_halign(Gtk.Align.END)

            row.append(left)
            row.append(entry)
            content.append(row)

            entries[setting_key] = entry

        def add_bool_row(label_key, hint_key, setting_key):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_hexpand(True)

            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            left.set_hexpand(True)

            title = Gtk.Label()
            self.bind_i18n(title, "label", label_key)
            title.set_xalign(0)
            title.set_halign(Gtk.Align.START)

            hint = Gtk.Label()
            self.bind_i18n(hint, "label", hint_key)
            hint.set_xalign(0)
            hint.set_halign(Gtk.Align.START)
            hint.set_wrap(True)
            hint.add_css_class("dim-label")

            left.append(title)
            left.append(hint)

            sw = Gtk.Switch()
            sw.set_active(bool(rag.get(setting_key, False)))
            sw.set_halign(Gtk.Align.END)
            sw.set_valign(Gtk.Align.CENTER)

            row.append(left)
            row.append(sw)
            content.append(row)

            switches[setting_key] = sw

        add_int_row("o_RAG_Recent_Count", "o_RAG_Recent_Count_Hint", "recent_message_count")
        add_int_row("o_RAG_Retrieved_Count", "o_RAG_Retrieved_Count_Hint", "retrieved_chunk_count")
        add_int_row("o_RAG_Summary_Every", "o_RAG_Summary_Every_Hint", "summary_update_every")
        add_int_row("o_RAG_Memory_Chunk_Max", "o_RAG_Memory_Chunk_Max_Hint", "memory_chunk_max_chars")
        add_int_row("o_RAG_Summary_Max", "o_RAG_Summary_Max_Hint", "summary_max_chars")
        add_int_row("o_RAG_Code_Context_Max", "o_RAG_Code_Context_Max_Hint", "code_context_max_chars")

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.append(sep)

        add_bool_row("o_RAG_Use_Summary", "o_RAG_Use_Summary_Hint", "use_summary")
        add_bool_row("o_RAG_Use_Recent", "o_RAG_Use_Recent_Hint", "use_recent_messages")
        add_bool_row("o_RAG_Use_Retrieval", "o_RAG_Use_Retrieval_Hint", "use_retrieval")
        add_bool_row("o_RAG_Use_Code_Context", "o_RAG_Use_Code_Context_Hint", "use_code_context")
        add_bool_row("o_RAG_Recent_Attachments", "o_RAG_Recent_Attachments_Hint", "include_recent_attachments")

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)

        def safe_int(entry, default, min_v, max_v):
            try:
                val = int(entry.get_text().strip())
            except Exception:
                val = default
            return max(min_v, min(max_v, val))

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = ensure_base_config(self.load_config())
                rag2 = dict(cfg2.get("rag_settings", {}))

                rag2["recent_message_count"] = safe_int(entries["recent_message_count"], 10, 0, 50)
                rag2["retrieved_chunk_count"] = safe_int(entries["retrieved_chunk_count"], 5, 0, 50)
                rag2["summary_update_every"] = safe_int(entries["summary_update_every"], 20, 1, 200)
                rag2["memory_chunk_max_chars"] = safe_int(entries["memory_chunk_max_chars"], 1200, 100, 100_000)
                rag2["summary_max_chars"] = safe_int(entries["summary_max_chars"], 6000, 100, 100_000)
                rag2["code_context_max_chars"] = safe_int(entries["code_context_max_chars"], 8000, 100, 100_000)

                for key, sw in switches.items():
                    rag2[key] = bool(sw.get_active())

                cfg2["rag_settings"] = rag2
                self.save_config(cfg2)

            d.close()

        dialog.connect("response", on_response)
        dialog.present()


    def open_language_dialog(self):
        dialog = Gtk.Dialog(transient_for=self.win)
        self.bind_i18n(dialog, "title", "o_Language")
        dialog.set_modal(True)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        current_lang = self.get_ui_language()
        langs = self.get_available_ui_languages()

        label = Gtk.Label()
        self.bind_i18n(label, "label", "o_Language")
        label.set_xalign(0)
        content.append(label)

        combo = Gtk.ComboBoxText()
        for lang in langs:
            combo.append(lang, lang)
        combo.set_active_id(current_lang)
        content.append(combo)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)


        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                chosen = combo.get_active_id()
                if chosen:
                    self.set_ui_language(chosen)
                    self.refresh_ui_texts()
            d.close()

        dialog.connect("response", on_response)
        dialog.present()


    def _get_chat_bg_image_path(self) -> str:
        cfg = ensure_base_config(self.load_config())
        return str(cfg["chat_bg_image"]).strip()

    def _save_chat_bg_image_path(self, path: str):
        cfg = self.load_config()
        cfg["chat_bg_image"] = str(path or "").strip()
        self.save_config(cfg)

    def _reset_chat_bg_image(self):
        cfg = self.load_config()
        cfg["chat_bg_image"] = ""
        self.save_config(cfg)

    def refresh_visual_theme_only(self):
        self.apply_css()
        cfg = ensure_base_config(self.load_config())
        self.apply_theme(cfg["dark_mode"])

    def refresh_entire_ui_theme(self):
        self.refresh_visual_theme_only()
        self.refresh_ui_texts()
        self.load_chat()
        self.load_chat_list()
        self.load_models_list()
        self.refresh_attachments_preview()

        GLib.idle_add(self.scroll_to_bottom, True)

    def open_personalization_dialog(self):
        cfg = ensure_base_config(self.load_config())
        is_dark = cfg["dark_mode"]
        mode_name = "Dark Mode" if is_dark else "Light Mode"

        dialog = Gtk.Dialog(title=f"{self('o_Personalization')} ({mode_name})",transient_for=self.win)
        dialog.set_modal(True)
        dialog.set_default_size(460, 340)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        colors = self._get_theme_colors()
        rows = {}

        def on_pick_bg(*_):
            def on_path_selected(path):
                if path:
                    bg_path_entry.set_text(path)

            self.open_single_file_dialog_portable(
                    title=self("o_Select_Image"),
                    image_only=True,
                    callback=on_path_selected
                    )

        def on_clear_bg(*_):
            bg_path_entry.set_text("")

        def add_row(label_text, key):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            lab = Gtk.Label(label=label_text)
            lab.set_xalign(0)
            lab.set_hexpand(True)

            entry = Gtk.Entry()
            entry.set_text(colors[key])
            entry.set_placeholder_text("#rrggbb")
            self.add_entry_clipboard_shortcuts(entry)

            row.append(lab)
            row.append(entry)
            content.append(row)

            rows[key] = entry

        hint = Gtk.Label()
        hint.set_label(self("o_Color_Edit_Hint").format(mode_name=mode_name))
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        content.append(hint)


        add_row(self("o_Window_BG"), "window_bg")
        add_row(self("o_Input_BG"), "input_bg")
        add_row(self("o_User_BG"), "user_bg")
        add_row(self("o_User_Text"), "user_text")
        add_row(self("o_Bot_BG"), "bot_bg")
        add_row(self("o_Bot_Text"), "bot_text")
        add_row(self("o_Sidebar_Text"), "sidebar_text")
        add_row(self("o_Local_Model_Color"), "local_model_highlight")

        # --- SHOW USAGE SWITCH ---
        row_usage = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        usage_label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        usage_label_box.set_hexpand(True)

        usage_title = Gtk.Label()
        self.bind_i18n(usage_title, "label", "o_Token_Usage")
        usage_title.set_xalign(0)
        usage_title.set_halign(Gtk.Align.START)

        usage_label_box.append(usage_title)

        usage_switch = Gtk.Switch()
        usage_switch.set_active(cfg["show_usage"])
        usage_switch.set_halign(Gtk.Align.END)
        usage_switch.set_valign(Gtk.Align.CENTER)

        row_usage.append(usage_label_box)
        row_usage.append(usage_switch)

        content.append(row_usage)

        # --- SHOW TOKEN VALUE SWITCH ---
        row_show_token_value = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        show_token_value_label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        show_token_value_label_box.set_hexpand(True)

        show_token_value_title = Gtk.Label()
        self.bind_i18n(show_token_value_title, "label", "o_Use_Token_Value")
        show_token_value_title.set_xalign(0)
        show_token_value_title.set_halign(Gtk.Align.START)

        show_token_value_label_box.append(show_token_value_title)

        show_token_value_switch = Gtk.Switch()
        show_token_value_switch.set_active(bool(cfg.get("show_token_value", False)))
        show_token_value_switch.set_halign(Gtk.Align.END)
        show_token_value_switch.set_valign(Gtk.Align.CENTER)

        row_show_token_value.append(show_token_value_label_box)
        row_show_token_value.append(show_token_value_switch)
        content.append(row_show_token_value)


        # --- TOKEN VALUE ENTRY ---
        row_token_value = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        token_value_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        token_value_left.set_hexpand(True)

        token_value_label = Gtk.Label()
        self.bind_i18n(token_value_label, "label", "o_Token_Value")
        token_value_label.set_xalign(0)
        token_value_label.set_halign(Gtk.Align.START)

        token_value_hint = Gtk.Label()
        self.bind_i18n(token_value_hint, "label", "o_Token_Value_Hint")
        token_value_hint.set_xalign(0)
        token_value_hint.set_halign(Gtk.Align.START)
        token_value_hint.set_wrap(True)
        token_value_hint.add_css_class("dim-label")

        token_value_left.append(token_value_label)
        token_value_left.append(token_value_hint)

        token_value_entry = Gtk.Entry()
        token_value_entry.set_text(str(cfg.get("token_value", "2.0")))
        token_value_entry.set_placeholder_text("2.0")
        token_value_entry.set_width_chars(8)
        token_value_entry.set_max_width_chars(10)
        token_value_entry.set_halign(Gtk.Align.END)
        self.add_entry_clipboard_shortcuts(token_value_entry)

        row_token_value.append(token_value_left)
        row_token_value.append(token_value_entry)

        content.append(row_token_value)


        def refresh_token_value_sensitive(*_):
            active = bool(show_token_value_switch.get_active())

            token_value_label.set_sensitive(active)
            token_value_hint.set_sensitive(active)
            token_value_entry.set_sensitive(active)
            row_token_value.set_sensitive(active)


        show_token_value_switch.connect("notify::active", refresh_token_value_sensitive)
        refresh_token_value_sensitive()

        bg_path_label = Gtk.Label()
        self.bind_i18n(bg_path_label, "label", "o_Background_Hint")
        bg_path_label.set_xalign(0)
        content.append(bg_path_label)

        bg_path_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        bg_path_entry = Gtk.Entry()
        bg_path_entry.set_hexpand(True)
        self.bind_i18n(bg_path_entry, "placeholder", "o_Background_Path_Placeholder")
        bg_path_entry.set_text(self._get_chat_bg_image_path())
        self.add_entry_clipboard_shortcuts(bg_path_entry)

        bg_pick_btn = Gtk.Button(label=f"{self('o_Select_Image')}")
        bg_clear_btn = Gtk.Button(label=f"{self('o_Clear')}")
        bg_pick_btn.connect("clicked", on_pick_bg)
        bg_clear_btn.connect("clicked", on_clear_bg)

        bg_path_controls.append(bg_path_entry)
        bg_path_controls.append(bg_pick_btn)
        bg_path_controls.append(bg_clear_btn)
        content.append(bg_path_controls)


        reset_btn = Gtk.Button(label=f"{self('o_Reset_Default')}")
        content.append(reset_btn)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)


        def valid_hex(s: str) -> bool:
            s = (s or "").strip()
            if len(s) != 7 or not s.startswith("#"):
                return False
            try:
                int(s[1:], 16)
                return True
            except Exception:
                return False

        def on_reset(*_):
            defaults = self._get_default_theme_colors(is_dark)
            for key, entry in rows.items():
                entry.set_text(defaults[key])
            bg_path_entry.set_text("")

            usage_switch.set_active(True)
            show_token_value_switch.set_active(False)
            token_value_entry.set_text("2.0")
            refresh_token_value_sensitive()

        reset_btn.connect("clicked", on_reset)

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = self.load_config()
                cfg2["show_usage"] = bool(usage_switch.get_active())
                cfg2["show_token_value"] = bool(show_token_value_switch.get_active())

                val = token_value_entry.get_text().strip()
                cfg2["token_value"] = val if val else "2.0"
                self.save_config(cfg2)

                new_colors = {}
                for key, entry in rows.items():
                    val = entry.get_text().strip()
                    if not valid_hex(val):
                        return
                    new_colors[key] = val

                bg_path = bg_path_entry.get_text().strip()
                if bg_path:
                    try:
                        p = Path(bg_path).expanduser().resolve()
                        bg_path = str(p)
                    except Exception:
                        bg_path = ""

                self._save_theme_colors(new_colors)
                self._save_chat_bg_image_path(bg_path)
                self.refresh_entire_ui_theme()

            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def open_style_dialog(self):
        dialog = Gtk.Dialog(transient_for=self.win)
        self.bind_i18n(dialog, "title", "o_Response_Style")
        dialog.set_modal(True)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)


        label = Gtk.Label()
        self.bind_i18n(label, "label", "o_Response_Style")
        label.set_xalign(0)
        content.append(label)


        entry = Gtk.Entry()
        self.add_entry_clipboard_shortcuts(entry, dialog)
        self.bind_i18n(entry, "placeholder", "o_Response_Style_Placeholder")

        cfg = ensure_base_config(self.load_config())
        entry.set_text(cfg["response_style"])

        content.append(entry)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)


        # Enter → Kaydet
        entry.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.OK))

        def on_response(d, resp):
            if resp == Gtk.ResponseType.OK:
                cfg2 = self.load_config()
                cfg2["response_style"] = entry.get_text().strip()
                self.save_config(cfg2)

            d.close()

        dialog.connect("response", on_response)

        dialog.show()

        # Fokus ver ama seçim yapma
        def focus_entry():
            entry.grab_focus()
            entry.set_position(-1)   # cursor sona gider
            entry.select_region(0, 0)  # seçim yok
            return False

        GLib.idle_add(focus_entry)    


    def open_key_dialog(self, _button=None):
        cfg = ensure_base_config(self.load_config())

        dialog = Gtk.Dialog()
        dialog.set_transient_for(self.win)
        dialog.set_modal(True)
        self.bind_i18n(dialog, "title", "o_OpenRouter-Tavily_API_Key")

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        # OpenRouter API
        label = Gtk.Label()
        self.bind_i18n(label, "label", "o_OpenRouter_API_Key")
        label.set_xalign(0)
        content.append(label)

        openrouter_entry = Gtk.Entry()
        self.bind_i18n(openrouter_entry, "placeholder", "o_OpenRouter_Placeholder")
        openrouter_entry.set_visibility(False)
        openrouter_entry.set_text(str(cfg.get("open_router_key", "")))
        content.append(openrouter_entry)


        # Tavily API
        label = Gtk.Label()
        self.bind_i18n(label, "label", "o_Tavily_API_Key")
        label.set_xalign(0)
        content.append(label)

        tavily_entry = Gtk.Entry()
        self.bind_i18n(tavily_entry, "placeholder", "o_Tavily_Placeholder")
        tavily_entry.set_visibility(False)
        tavily_entry.set_text(str(cfg.get("tavily_api_key", "")))
        content.append(tavily_entry)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Save"), Gtk.ResponseType.OK)

        self.add_entry_clipboard_shortcuts(openrouter_entry, dialog)
        self.add_entry_clipboard_shortcuts(tavily_entry, dialog)

        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                cfg = ensure_base_config(self.load_config())

                cfg["open_router_key"] = openrouter_entry.get_text().strip()
                cfg["tavily_api_key"] = tavily_entry.get_text().strip()

                self.save_config(cfg)

            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def open_settings_menu(self, button):
        pop = Gtk.Popover()
        pop.set_parent(button)
        pop.set_position(Gtk.PositionType.TOP)
        pop.set_has_arrow(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        api_btn = Gtk.Button(label=self('o_OpenRouter-Tavily_API_Key'))
        api_btn.set_halign(Gtk.Align.FILL)
        api_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_key_dialog(None)))
        box.append(api_btn)

        style_btn = Gtk.Button(label=self('o_Response_Style'))
        style_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_style_dialog()))
        box.append(style_btn)

        personalization_btn = Gtk.Button(label=self('o_Personalization'))
        personalization_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_personalization_dialog()))
        box.append(personalization_btn)

        lang_btn = Gtk.Button(label=self('o_Language'))
        lang_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_language_dialog()))
        box.append(lang_btn)

        rag_settings_btn = Gtk.Button(label=self("o_RAG_Settings"))
        rag_settings_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_rag_settings_dialog()))
        box.append(rag_settings_btn)

        ai_settings_btn = Gtk.Button(label=self('o_AI_Settings'))
        ai_settings_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_ai_settings_dialog()))
        box.append(ai_settings_btn)

        stt_settings_btn = Gtk.Button(label=self('o_STT_Settings'))
        stt_settings_btn.connect("clicked", lambda *_: (pop.popdown(), self.open_stt_settings_dialog()))
        box.append(stt_settings_btn)


        pop.set_child(box)
        pop.popup()

    # ---------------- CHAT LIST ----------------

    def load_chat_list(self):
        self.streaming_label = None
        while True:
            row = self.chat_list.get_first_child()
            if not row:
                break
            self.chat_list.remove(row)

        cfg = ensure_base_config(self.load_config())
        pinned = cfg["pinned_chats"]

        files = list(CHAT_DIR.glob("*.json"))

        def mtime(p):
            try:
                return p.stat().st_mtime
            except Exception:
                return 0.0

        def sort_key(p):
            return (0 if p.name in pinned else 1, -mtime(p), p.name.lower())

        for file in sorted(files, key=sort_key):
            row = Gtk.ListBoxRow()
            row.chat_path = file

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_hexpand(True)

            if file.name in pinned:
                pin_label = Gtk.Label(label="📌")
                pin_label.set_xalign(0)
                box.append(pin_label)

            label = Gtk.Label(label=file.stem)
            label.set_xalign(0)
            label.set_hexpand(True)
            label.set_halign(Gtk.Align.FILL)
            box.append(label)

            more_btn = Gtk.Button(label="⋯")
            self.bind_i18n(more_btn, "tooltip", "o_Options")
            more_btn.set_size_request(30, 30)
            more_btn.add_css_class("flat")
            box.append(more_btn)

            popover = Gtk.Popover()
            popover.set_autohide(True)
            popover.set_has_arrow(True)
            popover.set_position(Gtk.PositionType.BOTTOM)
            popover.set_parent(more_btn)

            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pop_box.set_margin_top(8)
            pop_box.set_margin_bottom(8)
            pop_box.set_margin_start(8)
            pop_box.set_margin_end(8)

            # Pin toggle
            is_pinned = file.name in pinned
            pin_item = Gtk.Button(label=(f"{self('o_Unpin')}" if is_pinned else f"{self('o_Pin')}"))
            pin_item.set_halign(Gtk.Align.FILL)

            def _toggle_pin(_btn, f=file, p=popover):
                p.popdown()
                self.toggle_pin_chat(f)

            pin_item.connect("clicked", _toggle_pin)
            pop_box.append(pin_item)

            # Rename
            rename_item = Gtk.Button(label=f"{self('o_Rename')}")
            rename_item.set_halign(Gtk.Align.FILL)
            rename_item.connect(
                    "clicked",
                    lambda *_, f=file, p=popover, anchor=more_btn: self.open_rename_popover(anchor, f, p)
                    )
            pop_box.append(rename_item)

            # Delete
            del_item = Gtk.Button(label=f"{self('o_Delete_Chat')}")
            del_item.set_halign(Gtk.Align.FILL)
            del_item.connect("clicked", lambda *_, f=file, p=popover: (p.popdown(), self.delete_chat(None, f)))
            pop_box.append(del_item)

            popover.set_child(pop_box)
            more_btn.connect("clicked", lambda *_, p=popover: p.popup())

            row.set_child(box)
            self.chat_list.append(row)

            if file == self.current_chat:
                GLib.idle_add(self.chat_list.select_row, row)

    # ---------------- CHAT DELETE ----------------

    def delete_chat(self, button, file_path):
        dialog = Gtk.Dialog()
        dialog.set_transient_for(self.win)
        self.bind_i18n(dialog, "title", "o_Delete_Chat")
        dialog.set_modal(True)

        content = dialog.get_content_area()
        label = Gtk.Label()
        self.bind_i18n(label, "label", "o_Delete_Chat_Confirm")
        label.set_wrap(True)
        content.append(label)

        dialog.add_button(self("o_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self("o_Delete"), Gtk.ResponseType.OK)


        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                if file_path.exists():
                    file_path.unlink()

                cfg = ensure_base_config(self.load_config())

                pinned = cfg["pinned_chats"]
                if file_path.name in pinned:
                    cfg["pinned_chats"] = [x for x in pinned if x != file_path.name]

                # chat_models cleanup
                chat_models = cfg["chat_models"]
                if file_path.name in chat_models:
                    del chat_models[file_path.name]
                    cfg["chat_models"] = chat_models

                chat_rag_switch = cfg.setdefault("chat_rag_switch", {})
                if file_path.name in chat_rag_switch:
                    del chat_rag_switch[file_path.name]
                    cfg["chat_rag_switch"] = chat_rag_switch

                if cfg["last_chat"] == file_path.name:
                    cfg["last_chat"] = "default.json"

                self.save_config(cfg)

                if self.current_chat == file_path:
                    chats = list(CHAT_DIR.glob("*.json"))
                    if chats:
                        self.current_chat = chats[0]
                    else:
                        DEFAULT_CHAT.write_text(
                            json.dumps({
                                "summary": "",
                                "messages": [],
                                "code_context": {},
                                "memory_chunks": []
                            }, ensure_ascii=False, indent=2),
                            encoding="utf-8"
                        )
                        self.current_chat = DEFAULT_CHAT

                # active model for new current chat
                self.active_model = self.get_chat_model(self.current_chat)

                self.load_chat_list()
                self.load_chat()
                self.load_models_list()

            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    # ---------------- CHAT VISIBILITY / SIDEBAR ----------------

    def apply_chats_visibility(self):
        if not getattr(self, "sidebar_expanded", True):
            self.chat_list.set_visible(False)
            return
        self.chat_list.set_visible(self.chats_list_open)
        self.chats_toggle_btn.set_label(f"{self('o_Chats')} ▾" if self.chats_list_open else f"{self('o_Chats')} ▸")

    def toggle_chats_list(self, *_):
        self.chats_list_open = not self.chats_list_open
        self.apply_chats_visibility()

    def toggle_sidebar(self, button):
        if self.sidebar_expanded:
            self.sidebar.set_size_request(60, -1)
            self.sidebar_expanded = False
            self.sidebar_scroll.set_visible(False)
            self.new_btn_sidebar.set_visible(True)
            self.sidebar_spacer.set_visible(True)
            self.apply_chats_visibility()
            self.apply_models_visibility()
        else:
            self.sidebar.set_size_request(250, -1)
            self.sidebar.set_hexpand(False)
            self.sidebar.set_vexpand(True)
            self.sidebar_expanded = True
            self.sidebar_scroll.set_visible(True)
            self.new_btn_sidebar.set_visible(False)
            self.sidebar_spacer.set_visible(False)
            self.apply_chats_visibility()
            self.apply_models_visibility()

    # ----------------COPY / REGENERATE --------------------

    def find_prev_user_index(self, messages: list, from_idx: int):
        j = from_idx - 1
        while j >= 0:
            if messages[j].get("role") == "user":
                return j
            j -= 1
        return None

    def build_regen_prompt_for_bot(self, question: str, answer: str) -> str:
        return (
                "Aşağıdaki soru ve verilen cevaba benzer bir sonuç üret.\n"
                "Aynı yaklaşımı ve içeriği koru, ama metni birebir kopyalama; yeniden yaz "
                "ve mümkünse küçük iyileştirmeler yap.\n\n"
                "[SORU]\n"
                f"{question.strip()}\n\n"
                "[ÖNCEKİ CEVAP]\n"
                f"{answer.strip()}\n"
                )

    def copy_message_by_index(self, idx: int):
        try:
            chat_data = self.load_chat_data_ui()
            messages = chat_data["messages"]
        except Exception:
            return

        if not (0 <= idx < len(messages)):
            return

        content = self.render_message_text(messages[idx])
        if not content:
            return

        inner = self.extract_copy_block(content)
        self.copy_to_clipboard(inner if inner is not None else content)

    def download_copy_block_content(self, content: str, suggested_name: str = "content.txt"):
        content = str(content or "")

        def safe_name(name: str):
            name = str(name or "").strip() or "content.txt"
            for ch in ["/", "\\", "\n", "\r", "\t"]:
                name = name.replace(ch, "_")
            if "." not in Path(name).name:
                name += ".txt"
            return name

        suggested_name = safe_name(suggested_name)

        def do_write(dest_path):
            if not dest_path:
                return
            try:
                Path(dest_path).write_text(content, encoding="utf-8")
                self._show_info_dialog(
                    self("o_Download"),
                    self("o_File_Saved")
                )
            except Exception as e:
                self._show_info_dialog(self("o_Error"), str(e))

        try:
            dialog = Gtk.FileDialog()
            dialog.set_initial_name(suggested_name)

            def on_save(dlg, res):
                try:
                    gfile = dlg.save_finish(res)
                    dest = gfile.get_path() if gfile else None
                except Exception:
                    return

                if dest:
                    do_write(dest)

            dialog.save(self.win, None, on_save)
            return

        except Exception:
            pass

        dest = Path.home() / "Downloads" / suggested_name
        do_write(str(dest))

    def download_generated_file(self, src_path: str):
        src = Path(src_path).expanduser()

        if not src.exists() or not src.is_file():
            self._show_info_dialog(
                self("o_Error"),
                self("o_File_Not_Found") if self("o_File_Not_Found") != "o_File_Not_Found" else "File not found."
            )
            return

        def do_copy(dest_path):
            if not dest_path:
                return

            try:
                shutil.copy2(src, dest_path)
                self._show_info_dialog(
                    self("o_Download"),
                    self("o_File_Saved")
                )
            except Exception as e:
                self._show_info_dialog(
                    self("o_Error"),
                    str(e)
                )

        # Modern GTK save dialog
        try:
            dialog = Gtk.FileDialog()
            dialog.set_initial_name(src.name)

            def on_save(dlg, res):
                try:
                    gfile = dlg.save_finish(res)
                    dest = gfile.get_path() if gfile else None
                except Exception:
                    return

                if dest:
                    do_copy(dest)

            dialog.save(self.win, None, on_save)
            return

        except Exception:
            pass

        # fallback: Downloads içine kopyala
        dest = Path.home() / "Downloads" / src.name
        do_copy(str(dest))

    def regenerate_by_index(self, idx: int):
        # 1) Chat'i oku
        try:
            chat_data = self.load_chat_data_ui()
            messages = chat_data["messages"]
        except Exception:
            return

        if not (0 <= idx < len(messages)):
            return

        if getattr(self, "is_generating", False):
            return

        role = (messages[idx].get("role") or "").strip()
        content = self.render_message_text(messages[idx]).strip()
        if not content:
            return

        # 2) regen_text üret
        regen_text = None
        q_idx = None

        if role == "user":
            # user seçiliyse: aynısını tekrar sor
            regen_text = content
        else:
            # bot seçiliyse: bağlı user sorusunu bul + bot cevabı ile benzer üret
            q_idx = self.find_prev_user_index(messages, idx)
            question = (messages[q_idx].get("content") or "").strip() if q_idx is not None else ""

            # bot cevabı copy bloğuysa içeriğini al (daha iyi prompt)
            ans_inner = self.extract_copy_block(content)
            answer = ans_inner if ans_inner is not None else content

            if question:
                # build_regen_prompt_for_bot senin mevcut fonksiyonun (eski özellik)
                regen_text = self.build_regen_prompt_for_bot(question, answer)
            else:
                # user bulunamadıysa sadece cevaba benzer üret
                regen_text = (
                        "Aşağıdaki cevaba benzer bir sonuç üret. Metni birebir kopyalama; yeniden yaz ve iyileştir.\n\n"
                        f"[ÖNCEKİ CEVAP]\n{answer}\n"
                        )

        if not regen_text:
            return

        # 3) Referans zinciri: seçilen mesajın used_refs'lerini de dahil et
        #    (böylece içinde referans olan mesaj regenerate edilirken foto/ref kaybolmaz)
        ref_set = set()
        ref_set.add(idx)

        used = messages[idx].get("used_refs") or []
        if isinstance(used, list):
            for j in used:
                if isinstance(j, int) and 0 <= j < len(messages):
                    ref_set |= self.expand_ref_chain(messages, j)

        # bot seçildiyse bağlı user'ı da dahil et + onun zinciri
        if role != "user" and q_idx is not None:
            ref_set.add(q_idx)
            used_q = messages[q_idx].get("used_refs") or []
            if isinstance(used_q, list):
                for j in used_q:
                    if isinstance(j, int) and 0 <= j < len(messages):
                        ref_set |= self.expand_ref_chain(messages, j)

        selected_arg = ",".join(str(x) for x in sorted(ref_set))

        # 4) Chat sonuna yeni user mesajı ekle (regen flag + referans meta + preview)
        new_user = {
                "role": "user",
                "content": regen_text,
                "regen": True,  # regenerate rengi/işareti
                "used_refs": sorted(ref_set),  # AI'ya giden referans setiyle uyumlu meta
                }

        # UI'da karışmaması için: seçilen balon + (bot ise bağlı user) ayrı gruplar
        groups = []

        def _collect_group_for_index(ridx: int):
            if not (0 <= ridx < len(messages)):
                return None
            rmsg = messages[ridx]
            items = []

            # referans mesajının kendi önceki preview'ları (yeni format)
            rg = rmsg.get("refs_groups")
            if isinstance(rg, list) and rg:
                for g in rg:
                    its = g.get("items") if isinstance(g, dict) else None
                    if isinstance(its, list):
                        for it in its:
                            if isinstance(it, dict) and it.get("text"):
                                items.append({"role": it.get("role", ""), "text": it.get("text", "")})
            else:
                # eski format kalmış olabilir
                rp = rmsg.get("refs_preview")
                if isinstance(rp, list) and rp:
                    for it in rp:
                        if isinstance(it, dict) and it.get("text"):
                            items.append({"role": it.get("role", ""), "text": it.get("text", "")})

            # en sona referans mesajın kendisini koy (metin + image varsa pack_preview_item ekler)
            try:
                items.append(self.pack_preview_item(rmsg))
            except Exception:
                # pack_preview_item yoksa yine de minimum göster
                rrole = (rmsg.get("role") or "").strip()
                rtext = (rmsg.get("content") or "").strip()
                if not rtext and rmsg.get("image"):
                    rtext = "[image]"
                items.append({"role": rrole, "text": rtext})

            return {"items": items}

        g1 = _collect_group_for_index(idx)
        if g1 is not None:
            groups.append(g1)

        if role != "user" and q_idx is not None:
            g2 = _collect_group_for_index(q_idx)
            if g2 is not None:
                groups.append(g2)

        new_user["refs_groups"] = groups

        messages.append(new_user)

        messages.append({
            "role": "bot",
            "content": "",
            "streaming": True
        })

        self._streaming_bot_idx = len(messages) - 1

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data)
        except Exception:
            return

        # 5) UI: seçim temizle + typing indicator
        self.selected_indexes.clear()
        self.selection_mode = False
        self.update_selection_label()
        self.auto_scroll_enabled = True
        self.load_chat()
        self.show_typing_indicator()

        self.ai_stop_requested = False
        self.set_generating_state(True)

        # 6) AI çağır: zincir referanslarla birlikte
        chat_path_for_ai = self.current_chat
        threading.Thread(
            target=self.call_ai,
            args=(selected_arg, chat_path_for_ai),
            daemon=True
        ).start()

    def expand_ref_chain(self, messages: list, idx: int, seen=None) -> set[int]:
        if seen is None:
            seen = set()
        if idx in seen:
            return seen
        seen.add(idx)

        msg = messages[idx] if 0 <= idx < len(messages) else None
        if not isinstance(msg, dict):
            return seen

        used = msg.get("used_refs") or []
        if isinstance(used, list):
            for j in used:
                if isinstance(j, int) and 0 <= j < len(messages):
                    self.expand_ref_chain(messages, j, seen)
        return seen

    def pack_preview_item(self, rmsg: dict) -> dict:
        rrole = (rmsg.get("role") or "").strip()
        rtext = (rmsg.get("content") or "").strip()
        has_img = bool(
            rmsg.get("image") or
            (isinstance(rmsg.get("images"), list) and rmsg.get("images"))
        )

        if not rtext and has_img:
            rtext = "[image]"
        elif has_img:
            # metin + image birlikteyse anlaşılır olsun
            rtext = rtext + "  + [image]"

        return {"role": rrole, "text": rtext}

    # ---------------- CHAT CREATE / SWITCH ----------------

    def create_chat(self, button):
        i = 1
        while True:
            name = f"chat_{i}.json"
            path = CHAT_DIR / name
            if not path.exists():
                break
            i += 1
        #Buradan itibaren rag yaziyoruz
        path.write_text(
            json.dumps({
                "summary": "",
                "messages": [],
                "code_context": {},
                "memory_chunks": []
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self.current_chat = path
        self.loaded_message_start = None
        self._loading_older_messages = False

        cfg = ensure_base_config(self.load_config())
        cfg["last_chat"] = path.name

        models = self._ensure_min_one_model(cfg)
        cfg["chat_models"][path.name] = {
            "id": str(models[0]["id"]),
            "local": bool(models[0].get("local", False))
        }

        cfg.setdefault("chat_rag_switch", {})
        cfg["chat_rag_switch"][path.name] = True

        self.save_config(cfg)

        self.active_model = self.get_chat_model(self.current_chat)

        self.load_chat_list()
        self.load_chat()
        self.load_models_list()
        self.refresh_context_mode_switch()

        if not self.sidebar_expanded:
            self.toggle_sidebar(None)

        self.auto_focus_enabled = True
        GLib.idle_add(self.force_focus_entry)

    def switch_chat(self, listbox, row):
        if not row:
            return
        chat_path = getattr(row, "chat_path", None)
        if not chat_path:
            return

        self.clear_closed_chat_memory()
        self.current_chat = Path(chat_path)
        self.loaded_message_start = None
        self._loading_older_messages = False

        self.selected_indexes.clear()
        self.selection_mode = False
        self.update_selection_label()

        cfg = self.load_config()
        cfg["last_chat"] = self.current_chat.name
        self.save_config(cfg)

        self.selected_indexes.clear()
        self.selection_mode = False
        self.selection_label.set_text("")

        self.active_model = self.get_chat_model(self.current_chat)

        self.load_chat()
        self.load_models_list()
        self.refresh_context_mode_switch()
        self.refresh_current_generating_ui()

        self.auto_focus_enabled = True
        GLib.idle_add(self.force_focus_entry)

    # ---------------- CHAT LOAD / SELECTION ----------------

    def clear_selection(self, *_):
        self.auto_scroll_enabled = False

        old_scroll = None
        try:
            adj = self.scroll.get_vadjustment()
            if adj:
                old_scroll = adj.get_value()
        except Exception:
            pass

        self.selected_indexes.clear()
        self.selection_mode = False

        self.update_selection_label()
        self.load_chat()

        self.restore_scroll_position_later(old_scroll)

    def show_empty_chat_suggestions(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.CENTER)
        outer.add_css_class("suggestion-wrap")

        title = Gtk.Label(label=self("o_Suggestions_Title"))
        title.set_halign(Gtk.Align.CENTER)
        title.add_css_class("dim-label")
        outer.append(title)

        suggestions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        suggestions_box.set_halign(Gtk.Align.CENTER)

        keys = [
            "o_Suggestion_1",
            "o_Suggestion_2",
            "o_Suggestion_3",
            "o_Suggestion_4",
            "o_Suggestion_5",
        ]

        for key in keys:
            text = self(key).strip()

            # Çeviri yoksa key'in kendisi döner; onu göstermeyelim
            if not text or text == key:
                continue

            btn = Gtk.Button(label=text)
            btn.add_css_class("flat")
            btn.add_css_class("suggestion-bubble")
            btn.set_halign(Gtk.Align.CENTER)

            self._consume_click(btn)

            def _on_suggestion_clicked(_btn, suggestion=text):
                buf = self.textview.get_buffer()
                buf.set_text(suggestion)
                self.textview.grab_focus()

            btn.connect("clicked", _on_suggestion_clicked)
            suggestions_box.append(btn)

        outer.append(suggestions_box)
        self.chat_box.append(outer)

    def load_chat(self):
        alloc = self.chat_box.get_allocation()

        chat_width = alloc.width if alloc.width > 0 else 800  # fallback

        bubble_max_px = int(chat_width * 0.7)  # %70 Bot bubbles max width
        # Typing indicator varsa, önce onu çıkarıp kenarda tutacağız (silmek yok)
        kept_typing_row = None
        if getattr(self, "typing_active", False) and hasattr(self, "typing_row"):
            try:
                kept_typing_row = self.typing_row
                # container’dan çıkar ama objeyi öldürme
                if kept_typing_row.get_parent() is self.chat_box:
                    self.chat_box.remove(kept_typing_row)
            except Exception:
                kept_typing_row = None

        # normal temizleme (typing hariç her şeyi kaldır)
        while True:
            child = self.chat_box.get_first_child()
            if not child:
                break
            self.chat_box.remove(child)

        chat_data = self.load_chat_data_ui()
        all_messages = chat_data["messages"]

        has_streaming_placeholder = self.chat_has_streaming_placeholder(self.current_chat)
        current_key = self._chat_key(self.current_chat)
        current_is_streaming = current_key in self.stream_active_chats

        if has_streaming_placeholder and not current_is_streaming:
            self.typing_active = True
            self.typing_row.set_visible(True)

            if not getattr(self, "_typing_timer_active", False):
                self._typing_timer_active = True
                GLib.timeout_add(500, self.animate_typing)
        else:
            self.typing_active = False
            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass
        
        total_messages = len(all_messages)

        if self.loaded_message_start is None:
            self.loaded_message_start = max(
                0,
                total_messages - self.lazy_chat_initial_count
            )

        # Yeni mesaj geldiyse ve kullanıcı aşağıdaysa son tarafa doğru genişlet
        if self.loaded_message_start > total_messages:
            self.loaded_message_start = max(
                0,
                total_messages - self.lazy_chat_initial_count
            )

        messages = all_messages[self.loaded_message_start:]
        index_offset = self.loaded_message_start

        has_real_chat = any(
            isinstance(m, dict) and m.get("role") in ("user", "bot", "assistant")
            for m in all_messages
        )

        if not has_real_chat:
            self.show_empty_chat_suggestions()
            if self.auto_scroll_enabled:
                GLib.idle_add(self.scroll_to_bottom)
            return

        cfg = ensure_base_config(self.load_config())
        show_usage = cfg["show_usage"]
        show_token_value = cfg["show_token_value"]

        try:
            token_value = float(str(cfg.get("token_value", "2.0")).replace(",", "."))
        except Exception:
            token_value = 2.0

        for local_i, msg in enumerate(messages):
            if (
                isinstance(msg, dict)
                and msg.get("role") in ("bot", "assistant")
                and msg.get("streaming")
                and not str(msg.get("content") or "").strip()
            ):
                continue

            i = index_offset + local_i
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.set_hexpand(True)

            bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            bubble.set_hexpand(False)

            bubble_wrap = Adw.Clamp()
            bubble_wrap.set_maximum_size(bubble_max_px)
            bubble_wrap.set_tightening_threshold(bubble_max_px)
            bubble_wrap.set_hexpand(False)
            bubble_wrap.set_child(bubble)

            # Hover shadow (eski özellik)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda c, b=bubble: b.add_css_class("hovered"))
            motion.connect("leave", lambda c, b=bubble: b.remove_css_class("hovered"))
            bubble.add_controller(motion)

            # Referans preview (mesaj referansla gönderildiyse)
            groups = msg.get("refs_groups") or []
            if isinstance(groups, list) and groups:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                outer.add_css_class("refs-groups")

                for g in groups:
                    items = g.get("items") if isinstance(g, dict) else None
                    if not isinstance(items, list) or not items:
                        continue

                    mini = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    mini.add_css_class("refs-preview")  # aynı background mantığı

                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        rrole = (it.get("role") or "").strip()
                        rtext = (it.get("text") or "").strip()
                        if not rtext:
                            continue

                        if len(rtext) > 200:
                            rtext = rtext[:200] + "..."

                        line = f"{rrole}: {rtext}" if rrole else rtext
                        lab = Gtk.Label(label=line)
                        lab.set_wrap(True)
                        lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        lab.set_xalign(0)
                        lab.add_css_class("refs-preview-line")
                        mini.append(lab)

                    outer.append(mini)

                bubble.append(outer)
            has_text_content = bool(str(msg.get("content") or "").strip())

            if has_text_content or msg.get("i18n_key") or msg.get("error_key") or msg.get("status_key"):
                content = self.render_message_text(msg)

                title_block = self.extract_title_block(content)
                copy_block = self.extract_copy_block(content)
                apply_block = self.extract_apply_block(content)
                apply_ops = None
                apply_err = ""

                if apply_block is not None:
                    apply_ops, apply_err = self.parse_apply_ops(apply_block)

                apply_summary = ""
                if apply_ops:
                    apply_summary = self.build_apply_summary(apply_ops)

                if copy_block is not None:
                    code_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    code_container.add_css_class("code-block")
                    code_container.set_hexpand(True)
                    code_container.set_halign(Gtk.Align.FILL)

                    top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    top_row.set_hexpand(True)
                    top_row.set_halign(Gtk.Align.FILL)

                    if title_block:
                        title_label = Gtk.Label()
                        safe = GLib.markup_escape_text(title_block.strip())
                        title_label.set_markup(f"<b>{safe}</b>")
                        title_label.set_xalign(0)
                        title_label.set_halign(Gtk.Align.START)
                        title_label.set_hexpand(True)
                        title_label.set_wrap(True)
                        title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        title_label.set_max_width_chars(28)
                        top_row.append(title_label)

                    copy_btn = Gtk.Button(label=f"{self('o_Copy')}")
                    copy_btn.add_css_class("flat")
                    copy_btn.add_css_class("code-copy-btn")
                    copy_btn.set_focusable(False)
                    copy_btn.set_halign(Gtk.Align.END)
                    copy_btn.set_hexpand(False)

                    self._consume_click(copy_btn)
                    copy_btn.connect("clicked", lambda b, t=copy_block: self.copy_to_clipboard(t))

                    download_btn = Gtk.Button(label="💾")
                    download_btn.add_css_class("flat")
                    download_btn.add_css_class("code-copy-btn")
                    download_btn.set_focusable(False)
                    download_btn.set_halign(Gtk.Align.END)
                    download_btn.set_hexpand(False)
                    self.bind_i18n(download_btn, "tooltip", "o_Download")

                    self._consume_click(download_btn)

                    suggested_name = "content.txt"
                    if title_block:
                        base = title_block.strip().lower()
                        for ch in ["/", "\\", "\n", "\r", "\t", ":", "*", "?", "\"", "<", ">", "|"]:
                            base = base.replace(ch, "_")
                        suggested_name = (base[:40] or "content") + ".txt"

                    download_btn.connect(
                        "clicked",
                        lambda b, t=copy_block, n=suggested_name: self.download_copy_block_content(t, n)
                    )

                    copy_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                    copy_actions.set_halign(Gtk.Align.END)
                    copy_actions.set_hexpand(False)
                    copy_actions.append(copy_btn)
                    copy_actions.append(download_btn)

                    copy_wrap = Adw.Clamp()
                    copy_wrap.set_maximum_size(90)
                    copy_wrap.set_child(copy_actions)
                    top_row.append(copy_wrap)

                    code_label = Gtk.Label(label=copy_block)
                    code_label.set_wrap(True)
                    code_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    code_label.set_xalign(0)
                    code_label.set_halign(Gtk.Align.FILL)
                    code_label.set_hexpand(True)
                    code_label.set_width_chars(0)
                    code_label.set_max_width_chars(9999)
                    code_label.set_selectable(True)

                    code_container.append(top_row)
                    code_container.append(code_label)

                    bubble.append(code_container)

                else:
                    # 🔹 Normal mesaj
                    show_text = content

                    # Eğer apply ops varsa, bot mesajında content yerine özet satırı göster
                    if msg.get("role") != "user" and apply_ops and apply_summary:
                        show_text = apply_summary

                    label = Gtk.Label(label=show_text)
                    label.set_selectable(True)
                    label.set_wrap(True)
                    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    label.set_xalign(0)
                    label.set_halign(Gtk.Align.FILL)
                    label.set_width_chars(0)
                    label.set_max_width_chars(9999)
                    label.set_hexpand(True)

                    if "http://" in show_text or "https://" in show_text:
                        link_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                        link_box.set_halign(Gtk.Align.START)
                        self.append_text_with_links(link_box, show_text)
                        bubble.append(link_box)
                    else:
                        bubble.append(label)

                    # Eğer bu mesaj streaming placeholder ise, canlı güncelleme bu label'a yazılsın
                    if msg.get("role") != "user" and msg.get("streaming"):
                        self.streaming_label = label
                        self.streaming_row = row

                    if msg.get("streaming") and not show_text.strip():
                        row.set_visible(False)


            # Tekli image (legacy)
            if msg.get("image"):
                img_path = Path(msg["image"])
                if img_path.exists():
                    pic_overlay = self.build_zoomable_image_widget(str(img_path), 120, 80)
                    bubble.append(pic_overlay)

            # Çoklu images (yeni)
            imgs = msg.get("images")
            if isinstance(imgs, list) and imgs:
                row_imgs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row_imgs.set_halign(Gtk.Align.START)

                for ip in imgs:
                    p = Path(str(ip))
                    if not p.exists():
                        continue

                    pic_overlay = self.build_zoomable_image_widget(str(p), 120, 80)
                    row_imgs.append(pic_overlay)

                bubble.append(row_imgs)

            # Files (yeni)
            files = msg.get("files")
            if isinstance(files, list) and files:
                files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

                for f in files:
                    if not isinstance(f, dict):
                        continue

                    name = (f.get("name") or Path(str(f.get("path") or "")).name or "").strip()
                    if not name:
                        continue

                    is_editable = bool(f.get("edit") or f.get("editable"))

                    chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    chip.add_css_class("refs-preview")

                    icon_name = "document-edit-symbolic" if is_editable else "text-x-generic-symbolic"
                    icon = Gtk.Image.new_from_icon_name(icon_name)
                    chip.append(icon)

                    lab = Gtk.Label(label=name)
                    lab.set_xalign(0)
                    lab.set_max_width_chars(40)
                    lab.set_ellipsize(Pango.EllipsizeMode.END)
                    chip.append(lab)

                    files_box.append(chip)

                bubble.append(files_box)


            # Tek seçili balonda action butonları (copy + regenerate)
            show_actions = (len(self.selected_indexes) == 1 and i in self.selected_indexes)


            raw_text = self.render_message_text(msg).strip()
            short_msg = len(raw_text) <= 24 and "\n" not in raw_text and len(raw_text.split()) <= 4
            # kısa mesajlarda dışta, uzunlarda ortada overlay
            use_outside_actions = short_msg


            if msg.get("role") == "user":
                bubble.add_css_class("user-bubble")

                if msg.get("regen"):
                    bubble.add_css_class("user-bubble-regen")
                elif msg.get("used_refs"):
                    bubble.add_css_class("user-bubble-ref")

                row.set_halign(Gtk.Align.FILL)
                bubble_wrap.set_halign(Gtk.Align.END)
                bubble.set_halign(Gtk.Align.FILL)

                bubble_wrap.set_margin_start(0)

            else:
                bubble.add_css_class("bot-bubble")
                row.set_halign(Gtk.Align.FILL)
                bubble_wrap.set_halign(Gtk.Align.START)
                bubble.set_halign(Gtk.Align.FILL)

                bubble_wrap.set_margin_end(0)

            is_selected = i in self.selected_indexes

            if is_selected:
                bubble.add_css_class("selected")

            # ---------------- APPLY BUTTONS ----------------
            gen_st = msg.get("gen_status")
            if isinstance(gen_st, dict) and "status" in gen_st:
                gen_status = gen_st.get("status")
                gen_err = (gen_st.get("err") or "").strip()

                if gen_status == "cancel":
                    txt = f"⏭️ {self('o_Cancelled')}"
                elif gen_status == "fail":
                    txt = f"❌ {self('o_Generation_Failed')}: {gen_err}"
                else:
                    txt = ""

                if txt:
                    status_label = Gtk.Label(label=txt)
                    status_label.set_wrap(True)
                    status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    status_label.set_xalign(0)
                    status_label.add_css_class("refs-preview-line")
                    bubble.append(status_label)


            if msg.get("role") != "user":

                # daha önce sonuç yazıldıysa buton göstermeyelim, sadece feedback gösterelim
                st = msg.get("apply_status")
                if isinstance(st, dict) and "status" in st:
                    status = st.get("status")
                    err = (st.get("err") or "").strip()

                    if status == "ok":
                        txt = f"✅ {self('o_File_Changed')}"
                    elif status == "fail":
                        txt = self("o_Apply_Failed_Input", error=err)
                    elif status == "cancel":
                        txt = f"⏭️ {self('o_Cancelled')}"
                    else:
                        txt = ""

                    if txt:
                        status_label = Gtk.Label(label=txt)
                        status_label.set_wrap(True)
                        status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        status_label.set_xalign(0)
                        status_label.add_css_class("refs-preview-line")  # hafif/sönük görünsün istersen
                        bubble.append(status_label)

                else:
                    content_text = msg.get("content") or ""
                    apply_block = self.extract_apply_block(content_text)

                    usage_parent = bubble

                    if apply_block:
                        ops, parse_err = self.parse_apply_ops(apply_block)

                        # KRİTİK: ops varsa bile ancak path'ler gerçek dosyaysa buton göster
                        valid_ops = []
                        if isinstance(ops, list) and ops:
                            valid = True
                            for op in ops:
                                p_raw = str((op or {}).get("path") or "").strip()
                                p = Path(p_raw)
                                # absolute değilse veya dosya yoksa -> hiç buton gösterme
                                if (not p.is_absolute()) or (not p.exists()):
                                    valid = False
                                    break
                            if valid:
                                valid_ops = ops

                        if valid_ops:
                            apply_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                            apply_area.set_halign(Gtk.Align.START)

                            paths = []
                            for op in valid_ops:
                                p = str((op or {}).get("path") or "").strip()
                                if p:
                                    pp = Path(p)
                                    short = f"{pp.parent.name}/{pp.name}" if pp.parent.name else pp.name

                                    if short not in paths:
                                        paths.append(short)

                            if paths:
                                path_label = Gtk.Label(label="\n".join(paths))
                                path_label.set_xalign(0)
                                path_label.set_halign(Gtk.Align.START)
                                path_label.set_wrap(True)
                                path_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                                path_label.add_css_class("refs-preview-line")
                                apply_area.append(path_label)

                            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                            btn_row.set_halign(Gtk.Align.START)

                            apply_btn = Gtk.Button(label=f"{self('o_Apply')}")
                            apply_btn.add_css_class("suggested-action")
                            self._consume_click(apply_btn)

                            reject_btn = Gtk.Button(label=f"{self('o_Reject')}")
                            reject_btn.add_css_class("flat")
                            self._consume_click(reject_btn)

                            def _on_apply(_b, _ops=valid_ops, _idx=i):
                                apply_btn.set_sensitive(False)
                                reject_btn.set_sensitive(False)

                                ok, err = self.apply_ops_yes(_ops)

                                if ok:
                                    self._set_apply_status(_idx, "ok")
                                else:
                                    self._set_apply_status(_idx, "fail", err)
                                    GLib.idle_add(self.apply_failed_to_input, err)

                                self.load_chat()
                                self.scroll_to_bottom(force=True)

                            def _on_reject(_b, _idx=i):
                                self._set_apply_status(_idx, "cancel")
                                self.load_chat()
                                self.scroll_to_bottom(force=True)

                            apply_btn.connect("clicked", _on_apply)
                            reject_btn.connect("clicked", _on_reject)

                            btn_row.append(apply_btn)
                            btn_row.append(reject_btn)
                            apply_area.append(btn_row)

                            bubble.append(apply_area)
                            usage_parent = apply_area

                        # parse_err varsa bile input'a basma; sadece buton göstermemek daha doğru
                        # (çünkü her bot mesajında parse_err input’u kirletir)

                    # ------------------ WEB SEARCH REQUEST BUTTONS -------------------
                    
                    web_req = msg.get("web_search_request")

                    if isinstance(web_req, dict):
                        query = str(web_req.get("query") or "").strip()
                        status = str(web_req.get("status") or "pending").strip()

                        web_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                        web_area.set_halign(Gtk.Align.START)

                        info_label = Gtk.Label(label=self("o_Web_Search_Request_With_Query", query=query))
                        info_label.set_xalign(0)
                        info_label.set_wrap(True)
                        info_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        info_label.set_halign(Gtk.Align.START)
                        web_area.append(info_label)

                        if status == "pending":
                            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

                            search_btn = Gtk.Button(label=self("o_Search"))
                            search_btn.add_css_class("suggested-action")
                            self._consume_click(search_btn)
                            search_btn.connect(
                                "clicked",
                                lambda _b, idx=i: self.approve_web_search(idx)
                            )

                            cancel_btn = Gtk.Button(label=self("o_Cancel"))
                            cancel_btn.add_css_class("flat")
                            self._consume_click(cancel_btn)
                            cancel_btn.connect(
                                "clicked",
                                lambda _b, idx=i: self.cancel_web_search(idx)
                            )

                            btn_row.append(search_btn)
                            btn_row.append(cancel_btn)
                            web_area.append(btn_row)

                        elif status == "searching":
                            status_label = Gtk.Label(label="Searching...")
                            status_label.set_xalign(0)
                            status_label.add_css_class("refs-preview-line")
                            web_area.append(status_label)

                        elif status == "cancelled":
                            status_label = Gtk.Label(label=f"⏭️ {self('o_Web_Search_Cancelled')}")
                            status_label.set_xalign(0)
                            status_label.add_css_class("refs-preview-line")
                            web_area.append(status_label)

                        elif status == "searched":
                            status_label = Gtk.Label(label=f"✅ {self('o_Web_Search_Searched')}")
                            status_label.set_xalign(0)
                            status_label.add_css_class("refs-preview-line")
                            web_area.append(status_label)

                        elif status == "failed":
                            err = str(web_req.get("err") or "").strip()
                            txt = (
                                f"❌ {self('o_Web_Search_Error_With_Detail', error=err)}"
                                if err else
                                f"❌ {self('o_Web_Search_Failed')}"
                            )

                            status_label = Gtk.Label(label=txt)
                            status_label.set_xalign(0)
                            status_label.set_wrap(True)
                            status_label.add_css_class("refs-preview-line")
                            web_area.append(status_label)

                        elif status == "error":
                            err = str(web_req.get("err") or "").strip()
                            status_label = Gtk.Label(label=f"Web search error: {err}")
                            status_label.set_xalign(0)
                            status_label.set_wrap(True)
                            status_label.add_css_class("refs-preview-line")
                            web_area.append(status_label)

                        bubble.append(web_area)
                        usage_parent = web_area

                    # ---------------- GENERATED FILE DOWNLOAD BUTTONS ----------------
                    generated_files = msg.get("generated_files")

                    if isinstance(generated_files, list) and generated_files:
                        download_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                        download_area.set_halign(Gtk.Align.START)

                        for gf in generated_files:
                            if not isinstance(gf, dict):
                                continue

                            path = str(gf.get("path") or "").strip()
                            name = str(gf.get("name") or Path(path).name).strip()

                            if not path:
                                continue

                            p = Path(path)
                            if not p.exists() or not p.is_file():
                                continue

                            file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                            file_row.set_halign(Gtk.Align.START)

                            name_label = Gtk.Label(label=name)
                            name_label.set_xalign(0)
                            name_label.set_halign(Gtk.Align.START)
                            name_label.set_max_width_chars(36)
                            name_label.set_ellipsize(Pango.EllipsizeMode.END)
                            name_label.add_css_class("refs-preview-line")

                            download_btn = Gtk.Button(label="💾")
                            download_btn.add_css_class("flat")
                            download_btn.set_tooltip_text(name)
                            download_btn.set_size_request(34, 34)
                            self._consume_click(download_btn)
                            download_btn.connect(
                                "clicked",
                                lambda _b, src=path: self.download_generated_file(src)
                            )

                            file_row.append(download_btn)
                            file_row.append(name_label)
                            download_area.append(file_row)

                        if download_area.get_first_child() is not None:
                            bubble.append(download_area)
                            usage_parent = download_area


                    # BURASI if apply_block'UN DIŞINDA OLMALI
                    u = msg.get("usage")
                    if show_usage and isinstance(u, dict):
                        p = int(u.get("prompt_tokens", 0) or 0)
                        c = int(u.get("completion_tokens", 0) or 0)
                        t = int(u.get("total_tokens", 0) or 0)

                        usage_txt = (
                            f"{self('o_Input_Tokens')}: {p} | "
                            f"{self('o_Output_Tokens')}: {c} | "
                            f"{self('o_Total_Tokens')}: {t}"
                        )

                        usage_label = Gtk.Label(label=usage_txt)
                        usage_label.set_xalign(0)
                        usage_label.set_halign(Gtk.Align.START)
                        usage_label.set_wrap(True)
                        usage_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        usage_label.add_css_class("refs-preview-line")
                        usage_parent.append(usage_label)

                        # Token price her zaman en altta olsun
                        if show_token_value and token_value > 0 and t > 0:
                            # token_value = 1M token başına ücret
                            token_price = (t / 1_000_000) * token_value

                            price_txt = f"{self('o_Total_Token_Price')}: ${token_price:.6f}"

                            price_label = Gtk.Label(label=price_txt)
                            price_label.set_xalign(0)
                            price_label.set_halign(Gtk.Align.START)
                            price_label.set_wrap(True)
                            price_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                            price_label.add_css_class("refs-preview-line")
                            usage_parent.append(price_label)

            if use_outside_actions:
                outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                outer.set_hexpand(True)
                outer.set_halign(Gtk.Align.FILL)

                left_spacer = Gtk.Box()
                left_spacer.set_hexpand(True)

                right_spacer = Gtk.Box()
                right_spacer.set_hexpand(True)

                bubble_click = Gtk.GestureClick()
                bubble_click.connect("pressed", lambda g, n, x, y, idx=i: self.on_message_clicked(idx))
                bubble.add_controller(bubble_click)

                small_overlay = Gtk.Overlay()
                small_overlay.set_child(bubble_wrap)
                small_overlay.set_hexpand(False)
                small_overlay.set_vexpand(False)

                if show_actions:
                    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    actions.set_valign(Gtk.Align.CENTER)
                    actions.set_hexpand(False)
                    actions.set_vexpand(False)

                    copy_btn = Gtk.Button(label="📋")
                    copy_btn.add_css_class("flat")
                    copy_btn.add_css_class("action-center-btn")
                    copy_btn.set_size_request(28, 28)
                    copy_btn.set_can_target(True)
                    self._consume_click(copy_btn)
                    copy_btn.connect("clicked", lambda _b, idx=i: self.copy_message_by_index(idx))

                    regen_btn = Gtk.Button(label="♻")
                    regen_btn.add_css_class("flat")
                    regen_btn.add_css_class("action-center-btn")
                    regen_btn.set_size_request(28, 28)
                    regen_btn.set_can_target(True)
                    self._consume_click(regen_btn)
                    regen_btn.connect("clicked", lambda _b, idx=i: self.regenerate_by_index(idx))

                    actions.append(copy_btn)
                    actions.append(regen_btn)

                    if msg.get("role") == "user":
                        pass
                    else:
                        actions.set_halign(Gtk.Align.START)
                        actions.set_valign(Gtk.Align.CENTER)
                        actions.set_margin_start(2)
                        actions.set_margin_end(0)


                if msg.get("role") == "user":
                    outer.append(left_spacer)
                    user_pack = Gtk.Overlay()

                    user_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    user_line.set_hexpand(False)
                    user_line.set_vexpand(False)

                    # Butonlar için sadece yatay yer ayırır, yüksekliği büyütmez
                    action_space = Gtk.Box()
                    action_space.set_size_request(140, 1)
                    action_space.set_hexpand(False)
                    action_space.set_vexpand(False)

                    user_line.append(action_space)
                    user_line.append(small_overlay)

                    user_pack.set_child(user_line)
                    user_pack.set_hexpand(False)
                    user_pack.set_vexpand(False)

                    if show_actions:
                        actions.set_halign(Gtk.Align.START)
                        actions.set_valign(Gtk.Align.CENTER)
                        actions.set_margin_start(0)
                        actions.set_margin_end(0)

                        user_pack.add_overlay(actions)
                        user_pack.set_clip_overlay(actions, False)

                    outer.append(user_pack)

                else:
                    bot_pack = Gtk.Overlay()

                    bot_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    bot_line.set_hexpand(False)
                    bot_line.set_vexpand(False)

                    bot_line.append(small_overlay)

                    # Butonlar için sadece yatay alan ayırır, yüksekliği büyütmez
                    action_space = Gtk.Box()
                    action_space.set_size_request(140, 1)
                    action_space.set_hexpand(False)
                    action_space.set_vexpand(False)
                    bot_line.append(action_space)

                    bot_pack.set_child(bot_line)
                    bot_pack.set_hexpand(False)
                    bot_pack.set_vexpand(False)

                    if show_actions:
                        actions.set_halign(Gtk.Align.END)
                        actions.set_valign(Gtk.Align.CENTER)
                        actions.set_margin_start(0)
                        actions.set_margin_end(0)

                        bot_pack.add_overlay(actions)
                        bot_pack.set_clip_overlay(actions, False)

                    outer.append(bot_pack)
                    outer.append(right_spacer)

                row.append(outer)

            else:
                bubble_overlay = Gtk.Overlay()
                bubble_overlay.set_child(bubble_wrap)
                bubble_overlay.set_clip_overlay(bubble_wrap, False)
                if is_selected:
                    bubble.add_css_class("selected")

                bubble_overlay.set_halign(Gtk.Align.END if msg.get("role") == "user" else Gtk.Align.START)
                bubble_overlay.set_valign(Gtk.Align.FILL)
                bubble_overlay.set_hexpand(False)
                bubble_overlay.set_vexpand(False)

                wrapper_click = Gtk.GestureClick()
                wrapper_click.connect("pressed", lambda g, n, x, y, idx=i: self.on_message_clicked(idx))
                bubble_overlay.add_controller(wrapper_click)

                if show_actions:
                    dim = Gtk.Box()
                    dim.add_css_class("action-dim")
                    dim.set_halign(Gtk.Align.FILL)
                    dim.set_valign(Gtk.Align.FILL)
                    dim.set_hexpand(False)
                    dim.set_vexpand(False)

                    if msg.get("role") == "user":
                        dim.set_margin_end(10)
                    else:
                        dim.set_margin_start(10)

                    dim_click = Gtk.GestureClick()
                    dim_click.connect("pressed", lambda *_: self.clear_selection())
                    dim.add_controller(dim_click)

                    bubble_overlay.add_overlay(dim)

                    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                    actions.set_halign(Gtk.Align.CENTER)
                    actions.set_valign(Gtk.Align.CENTER)

                    copy_btn = Gtk.Button(label="📋")
                    copy_btn.add_css_class("flat")
                    copy_btn.add_css_class("action-center-btn")
                    copy_btn.set_size_request(36, 36)
                    self._consume_click(copy_btn)
                    copy_btn.connect("clicked", lambda _b, idx=i: self.copy_message_by_index(idx))

                    regen_btn = Gtk.Button(label="♻")
                    regen_btn.add_css_class("flat")
                    regen_btn.add_css_class("action-center-btn")
                    regen_btn.set_size_request(36, 36)
                    self._consume_click(regen_btn)
                    regen_btn.connect("clicked", lambda _b, idx=i: self.regenerate_by_index(idx))

                    actions.append(copy_btn)
                    actions.append(regen_btn)
                    bubble_overlay.add_overlay(actions)

                if msg.get("role") == "user":
                    long_outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                    long_outer.set_hexpand(True)
                    long_outer.set_halign(Gtk.Align.FILL)

                    left_spacer = Gtk.Box()
                    left_spacer.set_hexpand(True)

                    long_outer.append(left_spacer)
                    long_outer.append(bubble_overlay)

                    row.append(long_outer)
                else:
                    long_outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                    long_outer.set_hexpand(True)
                    long_outer.set_halign(Gtk.Align.FILL)

                    right_spacer = Gtk.Box()
                    right_spacer.set_hexpand(True)

                    long_outer.append(bubble_overlay)
                    long_outer.append(right_spacer)

                    row.append(long_outer)

            self.chat_box.append(row)


        GLib.idle_add(self.scroll_to_bottom)

    def load_older_messages(self):
        if getattr(self, "_loading_older_messages", False):
            return

        try:
            chat_data = self.load_chat_data_ui()
            total = len(chat_data["messages"])

            if total <= 0:
                return

            old_start = self.loaded_message_start

            if old_start is None:
                old_start = max(0, total - self.lazy_chat_initial_count)

            if old_start <= 0:
                return

            self._loading_older_messages = True
            self.auto_scroll_enabled = False

            adj = self.scroll.get_vadjustment()
            old_upper = adj.get_upper() if adj else 0
            old_value = adj.get_value() if adj else 0

            self.loaded_message_start = max(
                0,
                old_start - self.lazy_chat_batch_count
            )

            self.load_chat()

            def restore_after_prepend():
                try:
                    adj2 = self.scroll.get_vadjustment()
                    if adj2:
                        new_upper = adj2.get_upper()
                        delta = new_upper - old_upper
                        adj2.set_value(old_value + delta)
                except Exception:
                    pass

                self._loading_older_messages = False
                return False

            GLib.idle_add(restore_after_prepend)
            GLib.timeout_add(60, restore_after_prepend)

        except Exception:
            self._loading_older_messages = False

    def restore_scroll_position_later(self, old_scroll):
        if old_scroll is None:
            return

        def restore_scroll():
            try:
                adj = self.scroll.get_vadjustment()
                if adj:
                    max_val = max(0, adj.get_upper() - adj.get_page_size())
                    adj.set_value(min(old_scroll, max_val))
            except Exception:
                pass
            return False

        GLib.idle_add(restore_scroll)
        GLib.timeout_add(30, restore_scroll)
        GLib.timeout_add(80, restore_scroll)
        GLib.timeout_add(160, restore_scroll)

    def on_message_clicked(self, index):
        self.auto_scroll_enabled = False

        # Mevcut scroll pozisyonunu koru
        old_scroll = None
        try:
            adj = self.scroll.get_vadjustment()
            if adj:
                old_scroll = adj.get_value()
        except Exception:
            pass

        if not self.selection_mode:
            self.selection_mode = True
            self.selected_indexes.add(index)
        else:
            if index in self.selected_indexes:
                self.selected_indexes.remove(index)
            else:
                self.selected_indexes.add(index)

        if not self.selected_indexes:
            self.selection_mode = False

        self.update_selection_label()
        self.load_chat()

        # load_chat sonrası GTK focus/selectable label yüzünden zıplarsa geri al
        self.restore_scroll_position_later(old_scroll)

    def update_selection_label(self):
        if not self.selected_indexes:
            self.selection_label.set_text("")
            self.selection_row.set_opacity(0.0)
            self.selection_row.set_sensitive(False)
        else:
            self.selection_label.set_text(f"{len(self.selected_indexes)} {self('o_Reference_Message')}")
            self.selection_row.set_opacity(1.0)
            self.selection_row.set_sensitive(True)

    def is_at_bottom(self, slack=6):
        adj = self.scroll.get_vadjustment() if hasattr(self, "scroll") else None
        if not adj:
            return True
        return adj.get_value() >= (adj.get_upper() - adj.get_page_size() - slack)

    def _sync_scroll_btn_margin(self):
        try:
            preview_h = self.preview_row.get_allocated_height() if self.preview_row.get_visible() else 0
            input_h = self.input_card.get_allocated_height() if getattr(self, "input_card", None) else 0

            if input_h <= 0:
                input_h = 80

            self.scroll_btn.set_margin_bottom(input_h + preview_h + 20)
        except Exception:
            pass
        return False

    def scroll_to_bottom(self, force=False):
        if not self.auto_scroll_enabled and not force:
            return

        adj = self.scroll.get_vadjustment()
        if adj:
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def on_scroll_button_clicked(self, *_):
        self.auto_scroll_enabled = True
        self.scroll_to_bottom(force=True)
        self.scroll_btn.set_visible(False)

    def on_scroll_changed(self, adjustment):
        value = adjustment.get_value()

        # Yukarı yaklaşınca eski mesajları yükle
        if value < 300:
            GLib.idle_add(self.load_older_messages)

        # en altta mı kontrol et
        at_bottom = value >= (
            adjustment.get_upper() - adjustment.get_page_size() - 800
        )

        if at_bottom:
            self.scroll_btn.set_visible(False)
        else:
            self.scroll_btn.set_visible(True)

    # ---------------- VOICE INPUT -------------------

    def _detect_pw_desktop_target(self) -> str | None:
        """
        PipeWire/Pulse tarafında masaüstü sesini almak için
        monitor source bulmaya çalışır.
        """
        preferred = self._get_preferred_mic_from_config()
        if preferred:
            return preferred

        pactl = shutil.which("pactl")
        if pactl:
            try:
                p = subprocess.run(
                        [pactl, "list", "short", "sources"],
                        capture_output=True,
                        text=True
                        )
                if p.returncode == 0:
                    lines = (p.stdout or "").splitlines()

                    # Önce monitor source ara
                    for line in lines:
                        parts = line.split("\t")
                        if len(parts) < 2:
                            continue
                        source_name = parts[1].strip()
                        if source_name and self._is_monitor_like_name(source_name):
                            return source_name

                    # hiç bulunamazsa None dön
                    return None
            except Exception:
                pass

        return None




    def _get_use_desktop_voice(self) -> bool:
        cfg = ensure_base_config(self.load_config())
        return cfg["use_desktop_voice"]

    def _is_monitor_like_name(self, name: str) -> bool:
        s = str(name or "").strip().lower()
        bad_words = [
                "monitor",
                ".monitor",
                "loopback",
                "stereo mix",
                "what u hear",
                "mix monitor",
                "output"
                ]
        return any(w in s for w in bad_words)

    def _get_preferred_mic_from_config(self) -> str:
        cfg = self.load_config()
        return str(cfg.get("mic_device") or "").strip()

    def _detect_pw_mic_target(self) -> str | None:
        """
        PipeWire/Pulse tarafında gerçek mikrofonu bulmaya çalışır.
        Monitor/loopback kaynaklarını elemeye çalışır.
        """
        preferred = self._get_preferred_mic_from_config()
        if preferred:
            return preferred

        pactl = shutil.which("pactl")
        if pactl:
            try:
                p = subprocess.run(
                        [pactl, "list", "short", "sources"],
                        capture_output=True,
                        text=True
                        )
                if p.returncode == 0:
                    lines = (p.stdout or "").splitlines()

                    # Önce gerçek mikrofon ara
                    for line in lines:
                        parts = line.split("\t")
                        if len(parts) < 2:
                            continue
                        source_name = parts[1].strip()
                        if source_name and not self._is_monitor_like_name(source_name):
                            return source_name

                    # fallback olarak ilk source
                    for line in lines:
                        parts = line.split("\t")
                        if len(parts) < 2:
                            continue
                        source_name = parts[1].strip()
                        if source_name:
                            return source_name
            except Exception:
                pass

        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                p = subprocess.run(
                        [wpctl, "status"],
                        capture_output=True,
                        text=True
                        )
                if p.returncode == 0:
                    for line in (p.stdout or "").splitlines():
                        low = line.lower()
                        if "*" in line and "source" in low and "monitor" not in low:
                            parts = line.strip().split()
                            for token in parts:
                                token = token.strip(".")
                                if token.isdigit():
                                    return token
            except Exception:
                pass

        return None

    def _detect_arecord_mic_device(self) -> str | None:
        """
        arecord için hw/plughw cihazı bulmaya çalışır.
        En güvenlisi config'ten gelmesi ama fallback de ekliyoruz.
        """
        preferred = self._get_preferred_mic_from_config()
        if preferred:
            return preferred

        ar = shutil.which("arecord")
        if not ar:
            return None

        try:
            p = subprocess.run(
                    [ar, "-L"],
                    capture_output=True,
                    text=True
                    )
            if p.returncode != 0:
                return None

            lines = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]

            # pulse varsa önce onu kullanma; çünkü bazen monitor'a gidebilir
            # gerçek cihaz olan plughw/hw benzeri bir giriş daha güvenli
            for name in lines:
                low = name.lower()
                if low.startswith("plughw:") and "null" not in low:
                    return name

            for name in lines:
                low = name.lower()
                if low.startswith("hw:") and "null" not in low:
                    return name

            for name in lines:
                low = name.lower()
                if low == "default":
                    return name

        except Exception:
            pass

        return None

    def _reset_mic_button_ui(self):
        try:
            self.mic_btn.set_label("🎙️")
            self.bind_i18n(self.mic_btn, "tooltip", "o_Microphone")
        except Exception:
            pass

        self._voice_countdown_active = False


    def _stop_voice_recording_and_transcribe(self):
        proc = getattr(self, "_voice_proc", None)

        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._voice_proc = None
        self._reset_mic_button_ui()

        threading.Thread(
            target=self._transcribe_last_audio,
            daemon=True
        ).start()

        return False


    def _update_voice_timeout_tooltip(self):
        if not getattr(self, "_voice_countdown_active", False):
            return False

        proc = getattr(self, "_voice_proc", None)

        if not proc or proc.poll() is not None:
            self._reset_mic_button_ui()
            return False

        if not getattr(self, "_voice_use_timeout", False):
            return True

        elapsed = time.time() - float(getattr(self, "_voice_start_time", time.time()))
        timeout = float(getattr(self, "_voice_timeout_seconds", 10))
        remaining = int(timeout - elapsed)

        if remaining <= 0:
            self.mic_btn.set_tooltip_text("Timeout: 0")
            GLib.idle_add(self._stop_voice_recording_and_transcribe)
            return False

        self.mic_btn.set_tooltip_text(f"Timeout: {remaining}")
        return True


    def _watch_voice_silence(self):
        proc = getattr(self, "_voice_proc", None)

        if not proc or not proc.stderr:
            return

        started = time.time()

        try:
            while proc.poll() is None:
                line = proc.stderr.readline()

                if not line:
                    time.sleep(0.05)
                    continue

                low = line.decode("utf-8", errors="ignore").lower()

                if "silence_start" in low and (time.time() - started) >= 1.5:
                    GLib.idle_add(self._stop_voice_recording_and_transcribe)
                    return

        except Exception:
            pass

    def toggle_voice_input(self, *_):
        # Eğer kayıt açıksa: durdur + transcribe
        if self._voice_proc and self._voice_proc.poll() is None:
            self._stop_voice_recording_and_transcribe()
            return

        # Kayıt başlat
        tmp = Path(tempfile.gettempdir())
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._last_wav = tmp / f"capture-ai-mic-{stamp}.wav"

        pw = shutil.which("pw-record")
        ar = shutil.which("arecord")
        use_desktop_voice = self._get_use_desktop_voice()

        cfg = ensure_base_config(self.load_config())

        use_timeout = bool(cfg.get("use_stt_timeout", False))
        use_silence = bool(cfg.get("use_stt_silence", False))

        try:
            timeout_seconds = float(str(cfg.get("stt_timeout", "10")).replace(",", "."))
        except Exception:
            timeout_seconds = 10.0

        try:
            silence_duration = float(str(cfg.get("stt_silence_duration", "2")).replace(",", "."))
        except Exception:
            silence_duration = 2.0

        ffmpeg = shutil.which("ffmpeg")

        if use_silence and ffmpeg:
            cmd = [
                ffmpeg,
                "-nostdin",
                "-y",
                "-f", "pulse",
                "-i", "default",
                "-ac", "1",
                "-ar", "16000",
                "-af", f"silencedetect=n=-45dB:d={silence_duration}",
                str(self._last_wav)
            ]

        elif pw:
            if use_desktop_voice:
                target = self._detect_pw_desktop_target()
                if not target:
                    self.handle_ai_error({
                        "role": "bot",
                        "error_key": "o_Desktop_Audio_Monitor_Not_Found",
                        "error_params": {}
                    })
                    return
            else:
                target = self._detect_pw_mic_target()
                if not target:
                    self.handle_ai_error({
                        "role": "bot",
                        "error_key": "o_Microphone_Source_Not_Found",
                        "error_params": {}
                    })
                    return

            cmd = [pw, "--rate", "16000", "--channels", "1", "--target", str(target), str(self._last_wav)]

        elif ar:
            # arecord tarafında masaüstü sesi yakalama her sistemde stabil değil.
            # Burada mikrofon için normal davranıyoruz.
            # Desktop sesi gerekiyorsa PipeWire/pw-record tercih edilmeli.
            if use_desktop_voice:
                self.handle_ai_error({
                    "role": "bot",
                    "error_key": "o_Desktop_Audio_Requires_PWRecord",
                    "error_params": {}
                })
                return

            device = self._detect_arecord_mic_device()
            if not device:
                self.handle_ai_error({
                    "role": "bot",
                    "error_key": "o_Microphone_Device_Not_Found",
                    "error_params": {}
                })
                return

            cmd = [ar, "-D", str(device), "-f", "S16_LE", "-r", "16000", "-c", "1", str(self._last_wav)]

        else:
            self.handle_ai_error({
                "role": "bot",
                "error_key": "o_Audio_Recorder_Not_Found",
                "error_params": {}
            })
            return

        try:
            self._voice_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if (use_silence and ffmpeg) else subprocess.DEVNULL
            )
        except Exception as e:
            self._voice_proc = None
            self.handle_ai_error({
                "role": "bot",
                "error_key": "o_Audio_Record_Start_Failed",
                "error_params": {
                    "error": str(e)
                }
            })
            return

        self.mic_btn.set_label("⏹")

        self._voice_start_time = time.time()
        self._voice_timeout_seconds = timeout_seconds
        self._voice_use_timeout = use_timeout
        self._voice_countdown_active = True

        if use_timeout:
            self.mic_btn.set_tooltip_text(f"Timeout: {int(timeout_seconds)}")
        else:
            self.bind_i18n(self.mic_btn, "tooltip", "o_Stop_Recording")

        GLib.timeout_add_seconds(1, self._update_voice_timeout_tooltip)

        if use_silence and ffmpeg:
            threading.Thread(
                target=self._watch_voice_silence,
                daemon=True
            ).start()

    def _normalize_stt_text(self, text: str) -> str:
        t = str(text or "").strip()

        if not t:
            return "__NO_SPEECH__"

        normalized = (
            t.replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
            .strip()
        )

        low = normalized.lower()

        if "empty_audio" in low:
            return "__NO_SPEECH__"

        bad_patterns = [
            "blank_audio",
            "blank voice",
            "blank_voice",
            "sound_voice",
            "sound voice",
            "music_audio",
            "music voice",
            "i', sorry",
            "i'm here",
            "i am here",
            "please upload",
            "please provide",
            "provide the audio",
            "share the audio",
            "i can transcribe",
            "i will transcribe",
            "i'll transcribe",
            "upload the audio",
            "audio file",
            "please provide the audio",
            "no audio",
            "cannot transcribe",
            "can't transcribe",
            "no speech",
            "no clear speech",
            "there's no clear speech detected",
            "there is no clear speech detected",
            "silence",
            "only silence",
            "noise",
            "only noise",
            "background sounds",
            "music only",
            "please speak when you're ready",
            "please speak when you are ready",
            "i'll transcribe your speech",
            "i will transcribe your speech",
            "ready, and i'll transcribe",
            "ready, and i will transcribe",
            "microphone input",
            "sure. please speak",
            "speak when you're ready",
            "speak when you are ready",
            "could you please repeat",
            "repeat the part of the sentence",
            "so that i can transcribe it accurately",
            "something might be unclear or incomplete",
            "it seems like something might be unclear or incomplete",

        ]

        if any(p in low for p in bad_patterns):
            return "__NO_SPEECH__"

        junk_exact = {
            "blank_audio",
            "music_audio",
            "music only",
            "background music",
            "speech not detected",
            "voice not detected",
            "empty audio",
            "empty_audio",
            "no speech detected",
            "no clear speech detected",
        }

        if low in junk_exact:
            return "__NO_SPEECH__"

        return t

    def _append_to_input(self, text: str):
        buf = self.textview.get_buffer()
        it = buf.get_iter_at_mark(buf.get_insert())
        if buf.get_char_count() > 0:
            buf.insert(it, " ")
        buf.insert(it, text)

    def _transcribe_last_audio(self):
        wav = self._last_wav
        if not wav or not Path(wav).exists():
            return

        cfg = ensure_base_config(self.load_config())
        is_online = cfg["is_mic_online"]

        try:
            if is_online:
                text = self._stt_online_openrouter(wav, cfg)
            else:
                text = self._stt_offline_whisper_cpp(wav, cfg)
        except Exception as e:
            GLib.idle_add(
                self.handle_ai_error,
                {
                    "role": "bot",
                    "error_key": "o_STT_Error",
                    "error_params": {
                        "error": str(e)
                    }
                }
            )
            return

        print("STT MODE:", "online" if is_online else "offline")
        print("STT RAW TEXT:", repr(text))

        if text == "__NO_SPEECH__":
            GLib.idle_add(
                self.handle_ai_error,
                {
                    "role": "bot",
                    "error_key": "o_No_Speech_Detected",
                    "error_params": {}
                }
            )
            return

        if text:
            GLib.idle_add(self._append_to_input, text.strip())

    def _stt_online_openrouter(self, wav_path: Path, cfg: dict) -> str:
        import requests, json

        key = (cfg.get("open_router_key") or "").strip()
        if not key:
            raise RuntimeError(self("o_OpenRouter_STT_Key_Missing"))

        model = (cfg.get("stt_model_online") or "openai/gpt-audio-mini").strip()

        audio_b64 = base64.b64encode(Path(wav_path).read_bytes()).decode("utf-8")

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text":
                         "Speech-to-text only. "
                         "Return only verbatim transcription. "
                         "No replies, no explanations, no apologies, no labels. "
                         "If speech is unclear or missing, return exactly EMPTY_AUDIO."
                        },
                        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
                    ],
                }
            ],
            "stream": False,
        }

        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "capture-ai",
            },
            json=payload,
            timeout=120,
        )

        if r.status_code != 200:
            raise RuntimeError(r.text[:500])

        data = r.json()
        raw_text = (data["choices"][0]["message"]["content"] or "").strip()
        return self._normalize_stt_text(raw_text)

    def _stt_offline_whisper_cpp(self, wav_path: Path, cfg: dict) -> str:
        import tempfile

        bin_path = (cfg.get("whisper_cpp_bin") or "").strip()
        model_path = (cfg.get("whisper_cpp_model") or "").strip()

        if not bin_path or not Path(bin_path).exists():
            raise RuntimeError(self("o_Whisper_Bin_Not_Found", path=bin_path))
        if not model_path or not Path(model_path).exists():
            raise RuntimeError(self("o_Whisper_Model_Not_Found", path=model_path))

        outdir = Path(tempfile.gettempdir()) / "capture-ai-stt"
        outdir.mkdir(parents=True, exist_ok=True)
        outbase = outdir / Path(wav_path).stem

        cmd = [
            bin_path,
            "-m", model_path,
            "-f", str(wav_path),
            "-l", "tr",
            "-otxt",
            "-of", str(outbase),
        ]

        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout or self("o_Whisper_Failed"))[:800])

        txt = Path(str(outbase) + ".txt")
        if not txt.exists():
            # bazı sürümlerde .txt yerine stdout'a basabilir—fallback
            s = (p.stdout or "").strip()
            return self._normalize_stt_text(s)

        raw_text = txt.read_text(encoding="utf-8", errors="ignore").strip()
        return self._normalize_stt_text(raw_text)

    # ---------------- CHANGE CODE FILES -------------

    def apply_failed_to_input(self, err: str):
        if not err:
            err = self("o_Unknown_Error")

        buf = self.textview.get_buffer()
        # input'u temizle ve hata bas (istersen temizleme, sadece append de yapabilirsin)
        buf.set_text(self("o_Apply_Failed_Input", error=err))
        self.textview.grab_focus()

    def is_file_editable(self, op):
        op = op or {}

        # 1. AI explicit flag
        if "editable" in op:
            return bool(op["editable"])

        # 2. patch / diff varsa editable say
        if op.get("diff") or op.get("patch"):
            return True

        # 3. uzantıya göre fallback
        p = str(op.get("path") or "").lower()
        editable_exts = (".py", ".js", ".ts", ".json", ".cpp", ".c", ".h", ".css", ".html")

        return p.endswith(editable_exts)

    def _apply_edit_op(self, file_path: str, find: str, mode: str, text: str) -> tuple[bool, str]:
        """
        mode: "before" | "after" | "replace"
        find: dosyada bulunacak anchor string
        text: eklenecek / değiştirilecek içerik
        return: (ok, err)
        """
        try:
            p = Path(file_path)
            if not p.exists():
                return False, self("o_Apply_File_Not_Found", path=p)

            original = p.read_text(encoding="utf-8", errors="ignore")

            idx = original.find(find)
            if idx < 0:
                return False, self("o_Apply_Find_Failed", name=p.name)

            if mode == "before":
                new_content = original[:idx] + text + original[idx:]
            elif mode == "after":
                new_content = original[:idx + len(find)] + text + original[idx + len(find):]
            elif mode == "replace":
                new_content = original.replace(find, text, 1)  # sadece ilk eşleşme
            else:
                return False, self("o_Apply_Unknown_Mode", mode=mode)

            if new_content == original:
                return False, self("o_Apply_No_Changes")

            p.write_text(new_content, encoding="utf-8")
            return True, ""
        except Exception as e:
            return False, self("o_Apply_Exception", error=e)

    def apply_ops_yes(self, ops) -> tuple[bool, str]:
        """
        ops: parse edilmiş operasyon listesi
        return: (ok, err)
        """
        if not isinstance(ops, list) or not ops:
            return False, self("o_Apply_No_Ops")

        for op in ops:
            path = str(op.get("path") or "").strip()
            find = str(op.get("find") or "")
            mode = str(op.get("mode") or "").strip().lower()
            text = str(op.get("text") or "")

            ok, err = self._apply_edit_op(path, find, mode, text)
            if not ok:
                return False, err

        return True, ""
    
    def _set_apply_status(self, msg_index: int, status: str, err: str = ""):
        try:
            chat_data = self.load_chat_data_ui()
            messages = chat_data["messages"]
        except Exception:
            return

        if not (0 <= msg_index < len(messages)):
            return

        messages[msg_index]["apply_status"] = {
            "status": status,
            "err": (err or "").strip()
        }

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data)
        except Exception:
            return

    # ---------------- ASK WEB SEARCH -----------------

    def _set_web_search_status(self, msg_index: int, status: str, err: str = ""):
        try:
            chat_data = self.load_chat_data_ui()
            messages = chat_data["messages"]
        except Exception:
            return

        if not (0 <= msg_index < len(messages)):
            return

        req = messages[msg_index].get("web_search_request")

        if not isinstance(req, dict):
            return

        req["status"] = status
        req["err"] = (err or "").strip()
        messages[msg_index]["web_search_request"] = req

        try:
            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data)
        except Exception:
            return

    def mark_latest_web_search_done(self, chat_path, ok: bool, err: str = ""):
        try:
            chat_data = self.load_chat_data_ui(chat_path)
            messages = chat_data["messages"]

            for msg in reversed(messages):
                req = msg.get("web_search_request") if isinstance(msg, dict) else None

                if isinstance(req, dict) and req.get("status") == "searching":
                    req["status"] = "searched" if ok else "failed"
                    req["err"] = str(err or "").strip()
                    msg["web_search_request"] = req
                    break

            chat_data["messages"] = messages
            self.save_chat_data_ui(chat_data, chat_path)

            if Path(self.current_chat).resolve() == Path(chat_path).resolve():
                self.load_chat()

        except Exception:
            pass

        return False

    def approve_web_search(self, msg_index: int):
        try:
            chat_data = self.load_chat_data_ui()
            messages = chat_data["messages"]
        except Exception:
            return

        if not (0 <= msg_index < len(messages)):
            return

        msg = messages[msg_index]
        req = msg.get("web_search_request")

        if not isinstance(req, dict):
            return

        query = str(req.get("query") or "").strip()

        if not query:
            self._set_web_search_status(
                msg_index,
                "error",
                self("o_Empty_Search_Query")
            )
            self.load_chat()
            return

        self._set_web_search_status(msg_index, "searching")
        self.load_chat()

        chat_path_for_ai = Path(self.current_chat)

        selected_arg = ""
        if self.selected_indexes:
            selected_arg = ",".join(map(str, sorted(self.selected_indexes)))

        threading.Thread(
            target=self.call_ai,
            args=(selected_arg, chat_path_for_ai, query),
            daemon=True
        ).start()

    def cancel_web_search(self, msg_index: int):
        self._set_web_search_status(msg_index, "cancelled")
        self.load_chat()

    # ---------------- STOP GENERATE -----------------

    def stop_ai_generation(self, chat_path=None):
        chat_path = Path(chat_path or self.current_chat)
        key = self._chat_key(chat_path)

        self.ai_stop_requested_chats.add(key)

        proc = self.ai_procs.get(key)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        self.stream_active_chats.discard(key)
        self.stream_ui_ready_chats.discard(key)

        if Path(self.current_chat).resolve() == chat_path.resolve():
            self.typing_active = False
            try:
                self.typing_row.set_visible(False)
            except Exception:
                pass

        self.set_generating_state(False, chat_path)

        try:
            chat_data = self.load_chat_data_ui(chat_path)
            messages = chat_data["messages"]

            changed = False

            if messages and isinstance(messages[-1], dict):
                last = messages[-1]
                if last.get("role") in ("bot", "assistant") and last.get("streaming"):
                    content = (last.get("content") or "").strip()
                    if not content:
                        messages.pop()
                        changed = True

            for j in range(len(messages) - 1, -1, -1):
                msg = messages[j]
                if msg.get("role") == "user":
                    msg["gen_status"] = {"status": "cancel", "err": ""}
                    changed = True
                    break

            if changed:
                chat_data["messages"] = messages
                self.save_chat_data_ui(chat_data, chat_path)

        except Exception:
            pass

        self.ai_procs.pop(key, None)

        if Path(self.current_chat).resolve() == chat_path.resolve():
            self.load_chat()
            self.streaming_row = None
            self.streaming_label = None
            self.scroll_to_bottom(force=True)

    def on_send_button_clicked(self, *_):
        if self.is_chat_generating(self.current_chat):
            self.stop_ai_generation(self.current_chat)
        else:
            self.send_message(None)

    def _chat_key(self, chat_path=None):
        return str(Path(chat_path or self.current_chat).resolve())

    def is_chat_generating(self, chat_path=None) -> bool:
        return self._chat_key(chat_path) in self.generating_chats

    def refresh_current_generating_ui(self):
        self.set_generating_state(
            self.is_chat_generating(self.current_chat),
            self.current_chat
        )

    def set_generating_state(self, generating: bool, chat_path=None):
        chat_path = Path(chat_path or self.current_chat)
        key = self._chat_key(chat_path)

        if generating:
            self.generating_chats.add(key)
        else:
            self.generating_chats.discard(key)

        # Eski kodlarla uyumluluk için:
        self.is_generating = self.is_chat_generating(self.current_chat)

        # Sadece ekrandaki aktif chat için send butonunu değiştir
        if Path(self.current_chat).resolve() != chat_path.resolve():
            return

        if hasattr(self, "send_btn") and self.send_btn:
            if self.is_chat_generating(chat_path):
                self.send_btn.set_label("☐")
                self.bind_i18n(self.send_btn, "tooltip", "o_Stop_Button")
            else:
                self.send_btn.set_label("↑")
                self.bind_i18n(self.send_btn, "tooltip", "o_Send_Button")

    # ---------------- SEND / CALL AI ----------------

    def send_message(self, widget):
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        message = buffer.get_text(start, end, True).strip()
        if not message:
            return

        chat_path_for_ai = Path(self.current_chat)

        if self.is_chat_generating(chat_path_for_ai):
            return

        chat_data = self.load_chat_data_ui()
        messages = chat_data["messages"]

        new_message = {"role": "user", "content": message}

        if self.pending_images:
            new_message["images"] = [str(Path(p).resolve()) for p in self.pending_images if Path(p).exists()]

        if self.pending_files:
            new_message["files"] = []
            for f in self.pending_files:
                fp = Path(str(f.get("path","")))
                if fp.exists():
                    new_message["files"].append({
                        "path": str(fp.resolve()),
                        "name": fp.name,
                        "edit": bool(f.get("edit")),  # <-- önemli
                    })

        # referansla gönderildiyse işaretle
        # referansla gönderildiyse: indeks + snapshot önizleme kaydet
        if self.selected_indexes:
            selected = sorted(self.selected_indexes)

            # AI'ya da gitsin diye zinciri genişlet (fotoğraf vs kaybolmasın)
            expanded = set()
            for ridx in selected:
                if 0 <= ridx < len(messages):
                    expanded |= self.expand_ref_chain(messages, ridx)

            new_message["used_refs"] = sorted(expanded)

            # Görünüm için: her seçilen referans kendi balonu gibi bir "group" olsun
            # group = (o mesajın eski refs_preview'si) + (o mesajın kendisi)
            groups = []
            for ridx in selected:
                if not (0 <= ridx < len(messages)):
                    continue
                rmsg = messages[ridx]

                group_items = []

                # Eğer bu referans mesajı daha önce referansla gönderildiyse,
                # onun içindeki refs_groups'u (veya eski refs_preview'u) da gösterelim
                rg = rmsg.get("refs_groups")
                if isinstance(rg, list) and rg:
                    # refs_groups varsa, içindeki tüm item’ları düzleştirip ekleyelim
                    for g in rg:
                        items = g.get("items") if isinstance(g, dict) else None
                        if isinstance(items, list):
                            for it in items:
                                if isinstance(it, dict) and it.get("text"):
                                    group_items.append({"role": it.get("role",""), "text": it.get("text","")})
                else:
                    # Eski sürümden kalan refs_preview varsa onu da ekle
                    rp = rmsg.get("refs_preview")
                    if isinstance(rp, list) and rp:
                        for it in rp:
                            if isinstance(it, dict) and it.get("text"):
                                group_items.append({"role": it.get("role",""), "text": it.get("text","")})

                # en sona referans mesajın kendisini ekle (bağlam bütünlüğü için)
                group_items.append(self.pack_preview_item(rmsg))

                groups.append({"items": group_items})

            new_message["refs_groups"] = groups

        chat_data = self.load_chat_data_ui()
        messages = chat_data["messages"]

        messages.append(new_message)
        messages.append({"role": "bot", "content": "", "streaming": True})

        chat_data["messages"] = messages
        self.save_chat_data_ui(chat_data)

        # Streaming için placeholder bot mesajı ekle (UI bu balonu canlı günceller)
        self._streaming_bot_idx = len(messages) - 1

        chat_data["messages"] = messages
        self.save_chat_data_ui(chat_data)

        buffer.set_text("")
        # preview temizle
        self.pending_images.clear()
        self.pending_files.clear()
        self.refresh_attachments_preview()

        selected_arg = ""
        if self.selected_indexes:
            # aynı expanded used_refs setini yeniden hesapla (ya da new_message["used_refs"]'ten al)
            ref_idxs = new_message.get("used_refs") or []
            if isinstance(ref_idxs, list) and ref_idxs:
                selected_arg = ",".join(str(i) for i in ref_idxs if isinstance(i, int))

        self.selected_indexes.clear()
        self.selection_mode = False
        self.update_selection_label()

        self.load_chat()
        self.show_typing_indicator(chat_path_for_ai)

        key = self._chat_key(chat_path_for_ai)

        self.ai_stop_requested_chats.discard(key)
        self.set_generating_state(True, chat_path_for_ai)
        self.stream_ui_ready_chats.discard(key)
        self.stream_active_chats.discard(key)

        threading.Thread(
            target=self.call_ai,
            args=(selected_arg, chat_path_for_ai),
            daemon=True
        ).start()

    def _consume_click(self, widget: Gtk.Widget):
        """
        Bu widget'a tıklanınca click event'ini yukarı (bubble click handler'ına) taşırma.
        """
        g = Gtk.GestureClick()
        g.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_pressed(gesture, n_press, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        g.connect("pressed", on_pressed)
        widget.add_controller(g)

    def extract_title_block(self, text: str):
        lines = text.splitlines()

        start = None
        end = None

        for i, line in enumerate(lines):
            if line.strip() == "title":
                if start is None:
                    start = i
                else:
                    end = i
                    break

        if start is not None and end is not None and end > start:
            return "\n".join(lines[start + 1:end]).strip()

        return None

    def extract_copy_block(self, text: str):
        lines = text.splitlines()

        start = None
        end = None

        for i, line in enumerate(lines):
            if line.strip() == "copy":
                if start is None:
                    start = i
                else:
                    end = i
                    break

        if start is not None and end is not None and end > start:
            return "\n".join(lines[start + 1:end]).strip()

        return None

    def extract_apply_block(self, text: str):
        lines = text.splitlines()

        start = None
        end = None

        for i, line in enumerate(lines):
            if line.strip() == "apply":
                if start is None:
                    start = i
                else:
                    end = i
                    break

        if start is not None and end is not None and end > start:
            return "\n".join(lines[start + 1:end]).strip()

        return None

    def parse_apply_ops(self, apply_text: str):
        """
        apply bloğunun içindeki JSON’u parse eder.
        Kabul edilen formatlar:
          - Tek dict: {"path":..., "find":..., "mode":..., "text":...}
          - Liste: [{"path":...}, {...}]
        Return: (ops_list, err_str)
        """
        try:
            raw = (apply_text or "").strip()
            if not raw:
                return None, "empty apply block"

            data = json.loads(raw)

            if isinstance(data, dict):
                ops = [data]
            elif isinstance(data, list):
                ops = data
            else:
                return None, "apply JSON must be object or array"

            # minimum alan doğrulama
            clean = []
            for i, op in enumerate(ops):
                if not isinstance(op, dict):
                    return None, f"op #{i} is not an object"
                clean.append(op)

            return clean, ""
        except Exception as e:
            return None, f"invalid apply JSON: {e}"

    def _short(self, s: str, n: int = 120) -> str:
        s = (s or "").replace("\n", "\\n")
        return s if len(s) <= n else (s[:n] + "...")

    def build_apply_summary(self, ops: list[dict]) -> str:
        """
        ops -> 1 satırlık özet üretir.
        (Şimdilik ilk op'u baz alıyoruz)
        """
        if not isinstance(ops, list) or not ops or not isinstance(ops[0], dict):
            return ""

        op = ops[0]
        mode = str(op.get("mode") or "").strip().lower()
        find = str(op.get("find") or "")
        text = str(op.get("text") or "")

        if mode not in ("replace", "before", "after"):
            mode = "replace"

        # "Bu satırı replace ediyorum: <find> -> <text>"
        mode_text = self(f"o_Apply_Mode_{mode}")

        return f"{mode_text}: {self._short(find)}  ->  {self._short(text)}"

    def _load_scaled_texture(self, image_path: str, width: int, height: int):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                image_path,
                max(1, int(width)),
                max(1, int(height)),
                True
            )
            return Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception as e:
            print("scaled texture load error:", e)
            return None

    def build_zoomable_image_widget(self, image_path: str, width: int, height: int):
        p = Path(str(image_path))
        overlay = Gtk.Overlay()

        pic = Gtk.Picture()
        pic.set_can_shrink(True)
        pic.set_keep_aspect_ratio(True)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_size_request(width, height)

        try:
            texture = self._load_scaled_texture(str(p), width, height)
            if texture is None:
                raise RuntimeError("texture is None")
            pic.set_paintable(texture)
        except Exception as e:
            print("image preview load error:", e)
            fallback = Gtk.Label(label=self("o_Image"))
            fallback.set_size_request(width, height)
            fallback.set_wrap(True)
            fallback.set_xalign(0.5)
            fallback.set_yalign(0.5)
            overlay.set_child(fallback)
        else:
            overlay.set_child(pic)

        zoom_btn = Gtk.Button(label="⛶")
        zoom_btn.add_css_class("flat")
        zoom_btn.add_css_class("image-zoom-btn")
        zoom_btn.set_focusable(False)
        zoom_btn.set_halign(Gtk.Align.START)
        zoom_btn.set_valign(Gtk.Align.START)
        zoom_btn.set_margin_top(4)
        zoom_btn.set_margin_start(4)
        zoom_btn.set_size_request(26, 26)
        self.bind_i18n(zoom_btn, "tooltip", "o_Zoom_Attachments")

        self._consume_click(zoom_btn)
        zoom_btn.connect("clicked", lambda *_: self.open_image_viewer(str(p)))

        overlay.add_overlay(zoom_btn)
        return overlay

    def open_image_viewer(self, image_path: str):
        p = Path(str(image_path))
        if not p.exists():
            return

        win = Gtk.Window()
        win.set_transient_for(self.win)
        win.set_modal(True)
        win.set_title(p.name)
        win.set_default_size(1100, 800)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title = Gtk.Label(label=p.name)
        title.set_xalign(0)
        title.set_hexpand(True)
        header.append(title)

        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("flat")
        self.bind_i18n(close_btn, "tooltip", "o_Close_Attachments")
        close_btn.connect("clicked", lambda *_: win.close())
        header.append(close_btn)

        outer.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)

        pic = Gtk.Picture()
        pic.set_can_shrink(True)
        pic.set_keep_aspect_ratio(True)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_hexpand(True)
        pic.set_vexpand(True)
        pic.set_halign(Gtk.Align.CENTER)
        pic.set_valign(Gtk.Align.CENTER)

        try:
            texture = Gdk.Texture.new_from_filename(str(p))
            pic.set_paintable(texture)
        except Exception as e:
            print("image viewer load error:", e)
            fallback = Gtk.Label(
                label=self("o_Image_Could_Not_Be_Loaded", path=p.name)
            )
            fallback.set_wrap(True)
            fallback.set_xalign(0.5)
            fallback.set_yalign(0.5)
            scroller.set_child(fallback)
        else:
            scroller.set_child(pic)

        win.set_child(outer)

        key_controller = Gtk.EventControllerKey()

        def on_key_pressed(_controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False

        key_controller.connect("key-pressed", on_key_pressed)
        win.add_controller(key_controller)

        outer.append(scroller)

        win.set_child(outer)
        win.present()

    def copy_to_clipboard(self, text: str):
        display = Gdk.Display.get_default()
        if not display:
            return
        clipboard = display.get_clipboard()
        clipboard.set(text)
    
    def call_ai(self, selected_arg="", chat_path_for_ai=None, approved_web_search_query=None):
        chat_path_for_ai = Path(chat_path_for_ai or self.current_chat)
        context_mode = self.get_context_mode(chat_path_for_ai)

        cmd = [
            sys.executable,
            "-u",
            AI_SCRIPT,
            str(chat_path_for_ai),
            selected_arg or "",
            "--context-mode",
            context_mode,
        ]

        if approved_web_search_query:
            cmd += [
                "--approved-web-search-query",
                str(approved_web_search_query)
            ]

        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                text=False,
                env=env
            )
            key = self._chat_key(chat_path_for_ai)
            self.ai_procs[key] = proc
            
            if proc.stdout is None or proc.stderr is None:
                raise RuntimeError("Popen stdout/stderr PIPE değil (None geldi)")

            import select

            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            partial = ""
            stderr_chunks = []
            json_mode = False

            out_fd = proc.stdout.fileno()
            err_fd = proc.stderr.fileno()

            stream_buffer = []
            stream_lock = threading.Lock()
            stream_timer_active = True
            stream_failed = False

            STREAM_FLUSH_MS = 10  # 60-120 arası iyi. Daha yavaş istersen 120 yap.
            STREAM_CHARS_PER_FLUSH = 8

            def flush_stream_buffer():
                nonlocal stream_timer_active

                if key in self.ai_stop_requested_chats:
                    return False

                if (not stream_timer_active) or stream_failed:
                    return False

                if json_mode:
                    return True

                with stream_lock:
                    pending = "".join(stream_buffer)

                    if not pending:
                        return True

                    chunk = pending[:STREAM_CHARS_PER_FLUSH]
                    rest = pending[STREAM_CHARS_PER_FLUSH:]

                    stream_buffer.clear()

                    if rest:
                        stream_buffer.append(rest)

                if chunk:
                    GLib.idle_add(
                        self.update_stream_text,
                        chunk,
                        chat_path_for_ai
                    )

                return True

            GLib.timeout_add(STREAM_FLUSH_MS, flush_stream_buffer)

            # proc çalıştığı sürece stdout/stderr hazır oldukça oku
            while True:
                rlist, _, _ = select.select([out_fd, err_fd], [], [], 0.1)

                if out_fd in rlist:
                    data = os.read(out_fd, 4096)

                    if data:
                        text = decoder.decode(data)

                        if text:
                            partial += text

                            stripped = partial.lstrip()

                            # JSON/file output ise stream göstermiyoruz.
                            # Çünkü file_create / generated_files gibi çıktılar UI'de parça parça görünmemeli.
                            if (
                                stripped.startswith('{"type":')
                                or stripped.startswith('{"content":')
                                or stripped.startswith('{"generated_files":')
                                or stripped.startswith('file_create')
                            ):
                                json_mode = True

                            if not json_mode:
                                with stream_lock:
                                    stream_buffer.append(text)

                if err_fd in rlist:
                    err_data = os.read(err_fd, 4096)
                    if err_data:
                        stderr_chunks.append(err_data.decode("utf-8", errors="replace"))

                if proc.poll() is not None:
                    break

            # decoder tail
            tail = decoder.decode(b"", final=True)

            if tail:
                partial += tail

                if not json_mode:
                    with stream_lock:
                        stream_buffer.append(tail)

            # Son kalan buffer'ı hemen bas
            if not json_mode:
                with stream_lock:
                    if stream_buffer:
                        final_chunk = "".join(stream_buffer)
                        stream_buffer.clear()
                    else:
                        final_chunk = ""

                if final_chunk:
                    GLib.idle_add(
                        self.update_stream_text,
                        final_chunk,
                        chat_path_for_ai
                    )
            def stop_stream_when_empty():
                nonlocal stream_timer_active

                with stream_lock:
                    empty = not bool(stream_buffer)

                if empty:
                    stream_timer_active = False
                    return False

                return True

            GLib.timeout_add(STREAM_FLUSH_MS, stop_stream_when_empty)

            proc.wait()
            rc = proc.returncode
            self.ai_procs.pop(key, None)

            stderr_text = "".join(stderr_chunks).strip()

            if key in self.ai_stop_requested_chats:
                stream_failed = True
                stream_timer_active = False

                with stream_lock:
                    stream_buffer.clear()

                GLib.idle_add(self.after_ai_response, chat_path_for_ai)
                return

            if rc != 0:
                stream_failed = True
                stream_timer_active = False

                with stream_lock:
                    stream_buffer.clear()

                if key in self.ai_stop_requested_chats:
                    GLib.idle_add(self.after_ai_response, chat_path_for_ai)
                    return

                GLib.idle_add(
                    self.handle_ai_error,
                    stderr_text or self("o_AI_Process_Failed"),
                    chat_path_for_ai
                )
                return

            else:
                GLib.idle_add(self.finalize_ai_response, partial, chat_path_for_ai)

                if approved_web_search_query:
                    GLib.idle_add(
                        self.mark_latest_web_search_done,
                        chat_path_for_ai,
                        True,
                        ""
                    )

                GLib.idle_add(self.after_ai_response, chat_path_for_ai)

        except Exception as e:
            try:
                stream_timer_active = False
                stream_failed = True

                with stream_lock:
                    stream_buffer.clear()
            except Exception:
                pass

            if approved_web_search_query:
                GLib.idle_add(
                    self.mark_latest_web_search_done,
                    chat_path_for_ai,
                    False,
                    str(e)
                )

            GLib.idle_add(
                self.handle_ai_error,
                str(e),
                chat_path_for_ai
            )


if __name__ == "__main__":
    image_arg = None
    if len(sys.argv) >= 2:
        image_arg = sys.argv[1]

    app = ChatApp(image_arg)
    app.run()
