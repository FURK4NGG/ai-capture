#!/usr/bin/env python3
import os
import sys
import json
import base64
import shutil
import termios
import tty
import tempfile
import subprocess
import codecs
import threading
import curses
import select
import time
import signal
import re
import webbrowser
from pathlib import Path
from datetime import datetime

CONFIG_PATH = Path.home() / ".config" / "capture-ai" / "config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

BASE_DIR = Path.home() / ".cache" / "capture-ai"
CHAT_DIR = BASE_DIR / "chats"
AI_SCRIPT = str(Path.home() / "capture-ai" / "ai.py")
GENERATED_DIR = BASE_DIR / "generated_images"
GENERATED_FILES_DIR = BASE_DIR / "generated_files"
LANG_DIR = Path.home() / "capture-ai" / "language"

GENERATED_FILES_DIR.mkdir(parents=True, exist_ok=True)

CHAT_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def fix_mojibake(s: str) -> str:
    if not s:
        return s
    if ("Ã" not in s) and ("Å" not in s) and ("â" not in s):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s

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

class ChatCLI:
    def __init__(self):
        self.pending_images = []
        self.pending_files = []
        self.pending_voices = []

        self.selected_ref_indexes = set()

        self.current_ai_process = None
        self.ai_cancel_requested = False

        cfg = self.load_config()
        last_chat_name = str(cfg.get("last_chat", "default.json") or "default.json").strip()
        if not last_chat_name:
            last_chat_name = "default.json"

        self.current_chat = CHAT_DIR / last_chat_name
        if not self.current_chat.exists():
            self.current_chat.write_text(
                json.dumps({
                    "summary": "",
                    "messages": [],
                    "code_context": {},
                    "memory_chunks": []
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        self.ensure_base_config()
        self.active_model = self.get_chat_model(self.current_chat)

    def clear_screen(self):
        os.system("clear")

    def _read_input(self, prompt=""):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def _get_clipboard_text(self) -> str:
        cmds = [
            ["wl-paste"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ]

        for cmd in cmds:
            if not shutil.which(cmd[0]):
                continue

            try:
                p = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=2
                )

                if p.returncode == 0 and p.stdout:
                    return p.stdout
            except Exception:
                pass

        return ""

    def _read_input_with_ctrl_shortcuts(self, prompt="") -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()

        buf = ""
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        try:
            tty.setraw(fd)

            while True:
                ch = sys.stdin.read(1)

                # ENTER
                if ch in ("\r", "\n"):
                    print()
                    return buf

                # CTRL+C
                if ch == "\x03":
                    raise KeyboardInterrupt

                # CTRL+A -> başa git
                if ch == "\x01":
                    sys.stdout.write("\r")
                    sys.stdout.write(prompt)
                    sys.stdout.flush()
                    continue

                # CTRL+X -> satırı kes
                if ch == "\x18":
                    sys.stdout.write("\r")
                    sys.stdout.write(" " * (len(prompt) + len(buf)))
                    sys.stdout.write("\r")
                    sys.stdout.write(prompt)
                    sys.stdout.flush()
                    buf = ""
                    continue

                # CTRL+V -> yapıştır
                if ch == "\x16":
                    clip = self._get_clipboard_text()
                    if clip:
                        buf += clip
                        sys.stdout.write(clip)
                        sys.stdout.flush()
                    continue

                # BACKSPACE
                if ch in ("\x08", "\x7f"):
                    if buf:
                        buf = buf[:-1]
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue

                # CTRL+C benzeri karakterleri engelle
                if ord(ch) < 32:
                    continue

                # normal karakter
                buf += ch
                sys.stdout.write(ch)
                sys.stdout.flush()

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _is_escape_input(self, s: str) -> bool:
        s = str(s or "")

        if not s:
            print(f"{self('o_Empty_Input')}")
            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        return s == "\x1b" or s.strip().lower() in {"esc", ":q"}

    def yes_no_prompt(self, label: str) -> bool:
        yes = str(self("o_Yes") or "yes").strip().lower()
        no = str(self("o_No") or "no").strip().lower()

        valid_yes = {"y", "yes"}
        valid_no = {"n", "no"}

        if yes:
            valid_yes.add(yes)
            valid_yes.add(yes[0])

        if no:
            valid_no.add(no)
            valid_no.add(no[0])

        ans = self._read_input(
            f"{label} ({self('o_Yes')}/{self('o_No')}): "
        ).strip().lower()

        return ans in valid_yes

    # ---------------- CONFIG ----------------

    def load_config(self):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception as e:
            print(f"config read error: {e}")
        return {}

    def save_config(self, cfg: dict):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"config save error: {e}")

    def ensure_base_config(self):
        cfg = self.load_config()
        changed = False

        defaults = {
            "ui_language": "en",
            "last_chat": "default.json",
            "open_router_key": "",
            "tavily_api_key": "",
            "ask_for_web_search": True,
            "prompt_chooser_blocks": ["copyable"],
            "response_style": "normal",
            "use_image_settings": False,
            "image_resolution": "1920x1080",
            "image_aspect_ratio": "16:9",
            "image_quality": "medium",
            "image_style": "",
            "show_usage": True,
            "show_token_value": False,
            "token_value": "2.0",
            "force_ui_language": False,
            "pinned_chats": [],
            "chat_rag_switch": {"default.json": True},
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
            "is_mic_online": True,
            "use_desktop_voice": False,
            "stt_model_online": "openai/gpt-audio-mini",
            "whisper_cpp_bin": "",
            "whisper_cpp_model": "",
            "use_stt_timeout": False,
            "stt_timeout": "10",
            "use_stt_silence": False,
            "stt_silence_duration": "2",
        }

        bool_keys = [
            "ask_for_web_search",
            "use_image_settings",
            "show_usage",
            "show_token_value",
            "force_ui_language",
            "is_mic_online",
            "use_desktop_voice",
            "use_stt_timeout",
            "use_stt_silence",
        ]

        for key, default in defaults.items():
            if key not in cfg:
                cfg[key] = default
                changed = True
                continue

            if key in bool_keys:
                fixed = ensure_bool_strict(cfg.get(key), default)
                if fixed != cfg.get(key):
                    cfg[key] = fixed
                    changed = True

        # ai_models normalize: string -> dict
        raw_models = cfg.get("ai_models", [])
        if not isinstance(raw_models, list):
            raw_models = []

        fixed_models = []
        seen = set()

        for item in raw_models:
            if isinstance(item, dict):
                mid = str(item.get("id") or "").strip()
                is_local = ensure_bool_strict(item.get("local"), False)
            else:
                mid = str(item or "").strip()
                is_local = False

            if not mid or mid in seen:
                continue

            seen.add(mid)
            fixed_models.append({
                "id": mid,
                "local": is_local
            })

        if not fixed_models:
            fixed_models = list(defaults["ai_models"])

        if fixed_models != cfg.get("ai_models"):
            cfg["ai_models"] = fixed_models
            changed = True

        # chat_models normalize
        raw_chat_models = cfg.get("chat_models", {})
        if not isinstance(raw_chat_models, dict):
            raw_chat_models = {}

        model_ids = {m["id"] for m in fixed_models}
        fixed_chat_models = {}

        for chat_name, model in raw_chat_models.items():
            chat_key = str(chat_name or "").strip()
            if not chat_key:
                continue

            if isinstance(model, dict):
                mid = str(model.get("id") or "").strip()
                is_local = ensure_bool_strict(model.get("local"), False)
            else:
                mid = str(model or "").strip()
                is_local = False

            if not mid or mid not in model_ids:
                mid = fixed_models[0]["id"]
                is_local = bool(fixed_models[0].get("local", False))
            else:
                for m in fixed_models:
                    if m["id"] == mid:
                        is_local = bool(m.get("local", False))
                        break

            fixed_chat_models[chat_key] = {
                "id": mid,
                "local": is_local
            }

        if not fixed_chat_models:
            fixed_chat_models = dict(defaults["chat_models"])

        if fixed_chat_models != cfg.get("chat_models"):
            cfg["chat_models"] = fixed_chat_models
            changed = True

        # rag_settings normalize - UI ile birebir
        rag_defaults = defaults["rag_settings"]

        if not isinstance(cfg.get("rag_settings"), dict):
            cfg["rag_settings"] = dict(rag_defaults)
            changed = True
        else:
            fixed_rag = dict(rag_defaults)
            raw_rag = cfg.get("rag_settings", {})

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

            if fixed_rag != cfg.get("rag_settings"):
                cfg["rag_settings"] = fixed_rag
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

        raw_providers = cfg.get("local_providers", {})
        if not isinstance(raw_providers, dict):
            raw_providers = {}

        fixed_providers = {}

        for provider_name, provider_cfg in raw_providers.items():
            pname = str(provider_name or "").strip()
            if not pname:
                continue

            if not isinstance(provider_cfg, dict):
                provider_cfg = {}

            fixed = dict(provider_defaults)
            fixed["enabled"] = ensure_bool_strict(provider_cfg.get("enabled"), False)

            for k in provider_defaults:
                if k == "enabled":
                    continue
                val = provider_cfg.get(k, provider_defaults[k])
                fixed[k] = str(val) if val is not None else ""

            if not fixed["base_url"].strip():
                fixed["base_url"] = provider_defaults["base_url"]

            fixed_providers[pname] = fixed

        if not fixed_providers:
            fixed_providers = dict(defaults["local_providers"])

        if fixed_providers != cfg.get("local_providers"):
            cfg["local_providers"] = fixed_providers
            changed = True

        if changed:
            self.save_config(cfg)

    def get_ui_language(self) -> str:
        cfg = self.load_config()
        lang = str(cfg.get("ui_language", "en") or "en").strip().lower()
        return lang if lang else "en"

    def set_ui_language(self, lang: str):
        lang = str(lang or "").strip().lower()
        if not lang:
            return

        cfg = self.load_config()
        cfg["ui_language"] = lang
        self.save_config(cfg)

    def get_ui_lang_map(self, lang: str | None = None) -> dict:
        lang = str(lang or self.get_ui_language() or "en").strip().lower()

        lang_path = LANG_DIR / f"{lang}.json"
        fallback_path = LANG_DIR / "en.json"

        data = {}

        try:
            if lang_path.exists():
                raw = json.loads(lang_path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    data = raw
        except Exception as e:
            print(f"language file read error ({lang_path.name}): {e}")

        if not data:
            try:
                if fallback_path.exists():
                    raw = json.loads(fallback_path.read_text(encoding="utf-8")) or {}
                    if isinstance(raw, dict):
                        data = raw
            except Exception as e:
                print(f"fallback language file read error ({fallback_path.name}): {e}")

        return data if isinstance(data, dict) else {}

    def __call__(self, key: str, **kwargs) -> str:
        lang_map = self.get_ui_lang_map()
        text = str(lang_map.get(key, key))

        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def get_available_ui_languages(self) -> list[str]:
        out = []

        try:
            if LANG_DIR.exists():
                for p in LANG_DIR.glob("*.json"):
                    name = p.stem.strip().lower()
                    if name:
                        out.append(name)
        except Exception as e:
            print(f"language directory read error: {e}")

        return sorted(set(out))

    def _normalize_model_entry(self, item):
        if isinstance(item, dict):
            mid = str(item.get("id") or "").strip()
            if not mid:
                return None
            return {
                "id": mid,
                "local": ensure_bool_strict(item.get("local"), False)
            }

        mid = str(item or "").strip()
        if not mid:
            return None

        return {
            "id": mid,
            "local": False
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

            if norm["id"] in seen:
                continue

            seen.add(norm["id"])
            out.append(norm)

        return out

    def _ensure_min_one_model(self, cfg: dict) -> list[dict]:
        models = self._get_models_list(cfg)

        if not models:
            models = [{
                "id": "google/gemini-2.5-flash-image",
                "local": False
            }]
            cfg["ai_models"] = models
            self.save_config(cfg)

        return models

    def _find_model_entry(self, cfg: dict, model_id: str):
        model_id = str(model_id or "").strip()

        for item in self._get_models_list(cfg):
            if item["id"] == model_id:
                return item

        return None

    def get_chat_model_entry(self, chat_path: Path) -> dict:
        cfg = self.load_config()
        self.ensure_base_config()
        cfg = self.load_config()

        models = self._ensure_min_one_model(cfg)
        model_ids = {m["id"] for m in models}

        chat_models = cfg.get("chat_models", {})
        if not isinstance(chat_models, dict):
            chat_models = {}

        selected = chat_models.get(chat_path.name)

        if isinstance(selected, dict):
            model_id = str(selected.get("id") or "").strip()
        else:
            model_id = str(selected or "").strip()

        if not model_id or model_id not in model_ids:
            chosen = models[0]
        else:
            chosen = self._find_model_entry(cfg, model_id) or models[0]

        fixed = {
            "id": chosen["id"],
            "local": bool(chosen.get("local", False))
        }

        if selected != fixed:
            chat_models[chat_path.name] = fixed
            cfg["chat_models"] = chat_models
            self.save_config(cfg)

        return fixed

    def get_chat_model(self, chat_path: Path) -> str:
        return self.get_chat_model_entry(chat_path)["id"]

    def set_chat_model(self, chat_path: Path, model_id: str, is_local: bool | None = None):
        model_id = str(model_id or "").strip()
        if not model_id:
            return

        cfg = self.load_config()
        self.ensure_base_config()
        cfg = self.load_config()

        models = self._ensure_min_one_model(cfg)

        found = None
        rest = []

        for item in models:
            if item["id"] == model_id and found is None:
                found = {
                    "id": model_id,
                    "local": bool(item.get("local", False)) if is_local is None else bool(is_local)
                }
            else:
                rest.append(item)

        if found is None:
            found = {
                "id": model_id,
                "local": bool(is_local) if is_local is not None else False
            }

        cfg["ai_models"] = [found] + rest

        chat_models = cfg.get("chat_models", {})
        if not isinstance(chat_models, dict):
            chat_models = {}

        chat_models[chat_path.name] = {
            "id": found["id"],
            "local": bool(found.get("local", False))
        }

        cfg["chat_models"] = chat_models
        self.save_config(cfg)

        self.active_model = found["id"]

    def get_context_mode(self, chat_path=None) -> str:
        chat_path = Path(chat_path or self.current_chat)

        cfg = self.load_config()
        self.ensure_base_config()
        cfg = self.load_config()

        chat_rag_switch = cfg.get("chat_rag_switch", {})
        if not isinstance(chat_rag_switch, dict):
            chat_rag_switch = {}

        # True = direct, False = rag
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

        cfg = self.load_config()
        self.ensure_base_config()
        cfg = self.load_config()

        chat_rag_switch = cfg.get("chat_rag_switch", {})
        if not isinstance(chat_rag_switch, dict):
            chat_rag_switch = {}

        chat_rag_switch[chat_path.name] = (mode == "direct")
        cfg["chat_rag_switch"] = chat_rag_switch

        self.save_config(cfg)

    # ---------------- CHAT FILES ----------------

    def load_chat_data_cli(self, path=None):
        path = Path(path or self.current_chat)

        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or []
        except Exception:
            raw = []

        if isinstance(raw, list):
            return {
                "summary": "",
                "messages": raw,
                "code_context": {},
                "memory_chunks": []
            }

        if not isinstance(raw, dict):
            raw = {}

        raw.setdefault("summary", "")
        raw.setdefault("messages", [])
        raw.setdefault("code_context", {})
        raw.setdefault("memory_chunks", [])

        if not isinstance(raw["messages"], list):
            raw["messages"] = []

        return raw

    def save_chat_data_cli(self, data, path=None):
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

    def load_chat_messages(self):
        return self.load_chat_data_cli().get("messages", [])

    def save_chat_messages(self, messages):
        data = self.load_chat_data_cli()
        data["messages"] = messages
        self.save_chat_data_cli(data)

    def list_chat_files(self):
        cfg = self.load_config()
        pinned = cfg.get("pinned_chats", [])
        if not isinstance(pinned, list):
            pinned = []

        files = list(CHAT_DIR.glob("*.json"))

        def mtime(p):
            try:
                return p.stat().st_mtime
            except Exception:
                return 0.0

        def sort_key(p):
            return (0 if p.name in pinned else 1, -mtime(p), p.name.lower())

        return sorted(files, key=sort_key)

    def create_chat(self):
        i = 1
        while True:
            name = f"chat_{i}.json"
            path = CHAT_DIR / name
            if not path.exists():
                break
            i += 1

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

        cfg = self.load_config()
        cfg["last_chat"] = path.name

        models = self._ensure_min_one_model(cfg)
        chat_models = cfg.get("chat_models", {})
        if not isinstance(chat_models, dict):
            chat_models = {}
        
        chat_models[path.name] = {
            "id": models[0]["id"],
            "local": bool(models[0].get("local", False))
        }
        cfg["chat_models"] = chat_models
        self.save_config(cfg)

        self.active_model = self.get_chat_model(self.current_chat)
        print(f"\n✔ {self('o_New_Chat')}: {path.stem}")
        self._read_input(f"\n{self('o_to_Menu')}")

    def delete_chat(self, file_path: Path):
        if not file_path.exists():
            print(self("o_Chat_Not_Found"))
            return

        try:
            file_path.unlink()
        except Exception as e:
            print(self("o_Delete_Error", error=e))
            return

        cfg = self.load_config()

        pinned = cfg.get("pinned_chats", [])
        if isinstance(pinned, list) and file_path.name in pinned:
            cfg["pinned_chats"] = [x for x in pinned if x != file_path.name]

        chat_models = cfg.get("chat_models", {})
        if isinstance(chat_models, dict) and file_path.name in chat_models:
            del chat_models[file_path.name]
            cfg["chat_models"] = chat_models

        if cfg.get("last_chat") == file_path.name:
            cfg["last_chat"] = "default.json"

        self.save_config(cfg)

        if self.current_chat == file_path:
            chats = self.list_chat_files()
            if chats:
                self.current_chat = chats[0]
            else:
                default_chat = CHAT_DIR / "default.json"
                default_chat.write_text("[]", encoding="utf-8")
                self.current_chat = default_chat

            cfg = self.load_config()
            cfg["last_chat"] = self.current_chat.name
            self.save_config(cfg)

        self.active_model = self.get_chat_model(self.current_chat)
        print(f"\n✔ {self('o_Delete_Chat')}: {file_path.stem}")

    def rename_chat(self, old_path: Path, new_name_raw: str):
        new_name = (new_name_raw or "").strip()
        if not new_name:
            print(self("o_Invalid_Name"))
            return

        for ch in ["/", "\\", "\n", "\r", "\t"]:
            new_name = new_name.replace(ch, "_")

        new_path = old_path.with_name(new_name + ".json")

        if new_path == old_path:
            print(f"{self('o_Chat_Rename_Fail')}")
            return

        if new_path.exists():
            print(self("o_Chat_Name_Exists"))
            return

        try:
            old_path.rename(new_path)
        except Exception as e:
            print(self("o_Rename_Error", error=e))
            return

        cfg = self.load_config()

        plist = cfg.get("pinned_chats", [])
        if isinstance(plist, list) and old_path.name in plist:
            cfg["pinned_chats"] = [new_path.name if x == old_path.name else x for x in plist]

        if cfg.get("last_chat") == old_path.name:
            cfg["last_chat"] = new_path.name

        chat_models = cfg.get("chat_models", {})
        if isinstance(chat_models, dict) and old_path.name in chat_models:
            chat_models[new_path.name] = chat_models.pop(old_path.name)
            cfg["chat_models"] = chat_models

        self.save_config(cfg)

        if self.current_chat == old_path:
            self.current_chat = new_path
            self.active_model = self.get_chat_model(self.current_chat)

        print(f"\n{self('o_New_Name')} {new_path.stem}")

    def toggle_pin_current_chat(self):
        cfg = self.load_config()

        pinned = cfg.get("pinned_chats", [])
        if not isinstance(pinned, list):
            pinned = []

        current_name = self.current_chat.name

        if current_name in pinned:
            cfg["pinned_chats"] = [x for x in pinned if x != current_name]
            self.save_config(cfg)
            print(f"\n✔ {self('o_Unpin_Current_Chat')}: {self.current_chat.stem}")
        else:
            pinned.append(current_name)
            cfg["pinned_chats"] = pinned
            self.save_config(cfg)
            print(f"\n✔ {self('o_Pin_Current_Chat')}: {self.current_chat.stem}")

    def select_chat(self):
        chats = self.list_chat_files()
        if not chats:
            print(self("o_No_Chats"))
            return

        cfg = self.load_config()
        pinned = cfg.get("pinned_chats", [])
        if not isinstance(pinned, list):
            pinned = []

        print(f"\n{self('o_Chats')}:")
        for i, p in enumerate(chats, 1):
            mark = "*" if p == self.current_chat else " "
            pin_mark = "📌 " if p.name in pinned else ""
            print(f"{i}) {mark} {pin_mark}{p.stem}")

        choice = self._read_input(f"\n{self('o_Selection')}: ").strip()
        if not choice.isdigit():
            return

        idx = int(choice) - 1
        if not (0 <= idx < len(chats)):
            return

        self.current_chat = chats[idx]
        cfg = self.load_config()
        cfg["last_chat"] = self.current_chat.name
        self.save_config(cfg)
        self.active_model = self.get_chat_model(self.current_chat)

        print(f"\n{self('o_Active_Chat')}: {self.current_chat.stem}")
        self._read_input(f"\n{self('o_to_Menu')}")

    # ---------------- LOCAL PROVIDERS ---------------

    def menu_local_providers(self):
        while True:
            self.clear_screen()
            cfg = self.load_config()
            providers = cfg.get("local_providers", {})
            if not isinstance(providers, dict):
                providers = {}

            names = list(providers.keys())

            print(f"\n{self('o_AI_Providers')}")

            for i, name in enumerate(names, 1):
                pcfg = providers.get(name, {})
                enabled = self("o_On") if pcfg.get("enabled") else self("o_Off")
                base_url = pcfg.get("base_url", "")
                print(f"{i}) {name} [{enabled}] {base_url}")

            print(f"{len(names) + 1}) {self('o_Add_Provider')}")
            print(f"{len(names) + 2}) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not choice.isdigit():
                continue

            num = int(choice)

            if num == len(names) + 2:
                return

            if num == len(names) + 1:
                name = self._read_input(f"{self('o_Provider_Name')}: ").strip()
                if not name:
                    continue

                providers[name] = {
                    "enabled": True,
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

                cfg["local_providers"] = providers
                self.save_config(cfg)
                continue

            idx = num - 1
            if not (0 <= idx < len(names)):
                continue

            self.menu_edit_local_provider(names[idx])

    def menu_edit_local_provider(self, provider_name: str):
        keys = [
            "enabled",
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
        ]

        while True:
            self.clear_screen()

            cfg = self.load_config()
            providers = cfg.get("local_providers", {})
            pcfg = providers.get(provider_name, {})

            print(f"\n={self('o_AI_Providers')}: {provider_name}=")

            for i, key in enumerate(keys, 1):
                print(f"{i}) {key}: {pcfg.get(key, '')}")

            stop_num = len(keys) + 1
            delete_num = len(keys) + 2
            back_num = len(keys) + 3

            print(f"{stop_num}) {self('o_Stop_Provider')}")
            print(f"{delete_num}) {self('o_Delete_Provider')}")
            print(f"{back_num}) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not choice.isdigit():
                continue

            num = int(choice)

            if num == back_num:
                return

            if num == stop_num:
                cmd = str(pcfg.get("stop_command") or "").strip()

                if not cmd:
                    print(self("o_Provider_Command_No_Command", provider=provider_name))
                    self._read_input(f"\n{self('o_to_Menu')}")
                    continue

                try:
                    p = subprocess.run(
                        cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )

                    if p.returncode == 0:
                        print(self("o_Provider_Command_Success", provider=provider_name, command=cmd))
                    else:
                        print(self("o_Provider_Command_Failed", provider=provider_name, command=cmd, code=p.returncode))

                    self._read_input(f"\n{self('o_to_Menu')}")

                except subprocess.TimeoutExpired:
                    print(self("o_Provider_Command_Timeout", provider=provider_name, command=cmd))
                    self._read_input(f"\n{self('o_to_Menu')}")

                except Exception as e:
                    print(self("o_Provider_Command_Error", provider=provider_name, error=e))
                    self._read_input(f"\n{self('o_to_Menu')}")

                continue

            if num == delete_num:
                if self.yes_no_prompt(f"{self('o_Delete_Provider')}?"):
                    providers.pop(provider_name, None)
                    cfg["local_providers"] = providers
                    self.save_config(cfg)
                    return

                continue

            idx = num - 1

            if not (0 <= idx < len(keys)):
                continue

            key = keys[idx]

            if key == "enabled":
                pcfg["enabled"] = not bool(pcfg.get("enabled", False))
            else:
                new_val = self._read_input(f"{self('o_New_Value')} ")
                pcfg[key] = new_val

            providers[provider_name] = pcfg
            cfg["local_providers"] = providers
            self.save_config(cfg)

    # ---------------- PROMPT CHOOSER ----------------

    def get_prompt_chooser_blocks(self) -> list[str]:
        cfg = self.load_config()
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
        cfg = self.load_config()
        cfg["prompt_chooser_blocks"] = list(blocks)
        self.save_config(cfg)

    def menu_prompt_chooser(self):
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

        while True:
            self.clear_screen()
            selected = set(self.get_prompt_chooser_blocks())

            print(f"\n={self('o_Prompt_Chooser')}=")
            for i, (key, label_key) in enumerate(items, 1):
                mark = "✅" if key in selected else "⬜"
                print(f"{i}) {mark} {self(label_key)}")

            print(f"{len(items) + 1}) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not choice.isdigit():
                continue

            num = int(choice)

            if num == len(items) + 1:
                return

            idx = num - 1
            if not (0 <= idx < len(items)):
                continue

            key = items[idx][0]

            if key in selected:
                selected.remove(key)
            else:
                selected.add(key)

            self.save_prompt_chooser_blocks([k for k, _ in items if k in selected])

    # -------------------- COPYABLE ------------------

    def extract_named_block(self, text: str, name: str) -> str | None:
        import re

        pattern = rf"(?ms)^\s*{re.escape(name)}\s*\n(.*?)\n\s*{re.escape(name)}\s*$"
        m = re.search(pattern, text or "")
        return m.group(1).strip() if m else None


    def extract_apply_block(self, text: str) -> str | None:
        return self.extract_named_block(text, "apply")


    def parse_apply_ops(self, apply_block: str):
        try:
            ops = json.loads(apply_block)
            if isinstance(ops, dict):
                ops = [ops]
            if not isinstance(ops, list):
                return [], "apply JSON list değil."
            return ops, ""
        except Exception as e:
            return [], str(e)


    def apply_edit_op_cli(self, file_path: str, find: str, mode: str, text: str):
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
                new_content = original.replace(find, text, 1)
            else:
                return False, self("o_Apply_Unknown_Mode", mode=mode)

            if new_content == original:
                return False, self("o_Apply_No_Changes")

            p.write_text(new_content, encoding="utf-8")
            return True, ""

        except Exception as e:
            return False, self("o_Apply_Exception", error=e)


    def apply_ops_yes_cli(self, ops):
        if not isinstance(ops, list) or not ops:
            return False, self("o_Apply_No_Ops")

        for op in ops:
            if not isinstance(op, dict):
                continue

            path = str(op.get("path") or "").strip()
            find = str(op.get("find") or "")
            mode = str(op.get("mode") or "").strip().lower()
            text = str(op.get("text") or "")

            ok, err = self.apply_edit_op_cli(path, find, mode, text)
            if not ok:
                return False, err

        return True, ""


    def set_apply_status_cli(self, msg_index: int, status: str, err: str = ""):
        data = self.load_chat_data_cli()
        messages = data.get("messages", [])

        if not (0 <= msg_index < len(messages)):
            return

        files = []
        old_status = messages[msg_index].get("apply_status")

        if isinstance(old_status, dict) and isinstance(old_status.get("files"), list):
            files = old_status.get("files")

        messages[msg_index]["apply_status"] = {
            "status": status,
            "err": str(err or "").strip(),
            "files": files
        }

        data["messages"] = messages
        self.save_chat_data_cli(data)


    def find_pending_apply_request(self):
        messages = self.load_chat_messages()

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if not isinstance(msg, dict):
                continue

            if msg.get("role") == "user":
                break

            if isinstance(msg.get("apply_status"), dict):
                continue

            content = str(msg.get("content") or "")
            apply_block = self.extract_apply_block(content)

            if not apply_block:
                continue

            ops, err = self.parse_apply_ops(apply_block)
            if err or not ops:
                continue

            valid_ops = []
            valid = True

            for op in ops:
                p_raw = str((op or {}).get("path") or "").strip()
                p = Path(p_raw)

                if (not p.is_absolute()) or (not p.exists()):
                    valid = False
                    break

                valid_ops.append(op)

            if valid and valid_ops:
                return i, msg, valid_ops

        return None, None, None

    def apply_op_summary_line_cli(self, op: dict) -> str:
        mode = str(op.get("mode") or "").strip().lower()
        find = str(op.get("find") or "").strip()
        text = str(op.get("text") or "").strip()

        if len(find) > 80:
            find = find[:80] + "..."

        if len(text) > 80:
            text = text[:80] + "..."

        if mode == "before":
            mode_text = self("o_Apply_Mode_before")
        elif mode == "after":
            mode_text = self("o_Apply_Mode_after")
        elif mode == "replace":
            mode_text = self("o_Apply_Mode_replace")
        else:
            mode_text = mode or "-"

        return f"{mode_text}: {find}  ->  {text}"

    def handle_pending_apply_requests(self) -> bool:
        msg_index, msg, ops = self.find_pending_apply_request()

        if not ops:
            return False

        print(f"\n{self('o_Apply_Changes_Request')}")

        paths = []
        for op in ops:
            p = Path(str(op.get("path") or ""))
            short = f"{p.parent.name}/{p.name}" if p.parent.name else p.name
            if short not in paths:
                paths.append(short)

        try:
            data = self.load_chat_data_cli()
            messages = data.get("messages", [])

            if 0 <= msg_index < len(messages):
                old = messages[msg_index].get("apply_status")
                if not isinstance(old, dict):
                    old = {}

                old["files"] = paths
                messages[msg_index]["apply_status"] = old
                data["messages"] = messages
                self.save_chat_data_cli(data)
        except Exception:
            pass

        print()
        for op in ops:
            print(self.apply_op_summary_line_cli(op))

        print()
        print(f"{self('o_Files')}:")
        for p in paths:
            print(f"- {p}")

        print()
        print(f"1) {self('o_Apply')}")
        print(f"2) {self('o_Reject')}")

        choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

        if choice == "1":
            ok, err = self.apply_ops_yes_cli(ops)

            if ok:
                self.set_apply_status_cli(msg_index, "ok")
                print(f"\n✅ {self('o_File_Changed')}")
            else:
                self.set_apply_status_cli(msg_index, "fail", err)
                print(f"\n❌ {self('o_Apply_Failed_Input', error=err)}")

            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        elif choice == "2":
            self.set_apply_status_cli(msg_index, "cancel")
            print(f"\n⏭️ {self('o_Reject')}")
            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        return True

    # ------------------ WEB SEARCH ------------------

    def find_pending_web_search_request(self):
        messages = self.load_chat_messages()

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if not isinstance(msg, dict):
                continue

            req = msg.get("web_search_request")
            if isinstance(req, dict) and req.get("status") == "pending":
                return i, msg, req

        return None, None, None


    def set_web_search_status_cli(self, msg_index: int, status: str, err: str = ""):
        data = self.load_chat_data_cli()
        messages = data.get("messages", [])

        if not (0 <= msg_index < len(messages)):
            return

        req = messages[msg_index].get("web_search_request")
        if not isinstance(req, dict):
            return

        req["status"] = status
        req["err"] = str(err or "").strip()
        messages[msg_index]["web_search_request"] = req

        data["messages"] = messages
        self.save_chat_data_cli(data)


    def call_ai_with_web_search_approval(self, query: str):
        cmd = [
            sys.executable,
            "-u",
            AI_SCRIPT,
            str(self.current_chat),
            "--approved-web-search-query",
            query
        ]

        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=False,
                env=env
            )

            stdout_text = proc.stdout.decode("utf-8", errors="replace")
            stderr_text = proc.stderr.decode("utf-8", errors="replace")

            stdout_text = fix_mojibake(stdout_text)
            stderr_text = fix_mojibake(stderr_text)

            if proc.returncode != 0:
                err = (stderr_text or stdout_text or self("o_Unknown_Error")).strip()
                self.handle_ai_error(err)
                return None

            return self.finalize_ai_response(stdout_text)

        except Exception as e:
            self.handle_ai_error(str(e))
            return None


    def handle_pending_web_search_requests(self) -> bool:
        msg_index, msg, req = self.find_pending_web_search_request()

        if not isinstance(req, dict):
            return False

        query = str(req.get("query") or "").strip()

        print()
        print(self("o_Web_Search_Request_With_Query", query=query))
        print()
        print(f"1) {self('o_Search')}")
        print(f"2) {self('o_Cancel')}")

        choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

        if choice == "1":
            self.set_web_search_status_cli(msg_index, "searching")
            bot_msg = self.call_ai_with_web_search_approval(query)

            if bot_msg:
                self.set_web_search_status_cli(msg_index, "searched")
                self.print_last_bot_message(bot_msg)

            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        elif choice == "2":
            self.set_web_search_status_cli(msg_index, "cancelled")
            print(f"\n⏭️ {self('o_Cancelled')}")
            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        return True

    # ----------------- STT SETTINGS -----------------

    def menu_stt_settings(self):
        while True:
            self.clear_screen()
            cfg = self.load_config()

            is_online = bool(cfg.get("is_mic_online", True))
            use_desktop = bool(cfg.get("use_desktop_voice", False))

            stt_model = str(
                cfg.get("stt_model_online", "openai/gpt-audio-mini") or ""
            )

            whisper_bin = str(cfg.get("whisper_cpp_bin", "") or "")
            whisper_model = str(cfg.get("whisper_cpp_model", "") or "")

            use_timeout = bool(cfg.get("use_stt_timeout", False))
            use_silence = bool(cfg.get("use_stt_silence", False))
            timeout = str(cfg.get("stt_timeout", "10") or "10")
            silence = str(cfg.get("stt_silence_duration", "2") or "2")

            print(f"\n{self('o_STT_Settings')}")

            print(
                f"1) {self('o_Online_STT_Text')}: "
                f"{'✅' if is_online else '❌'}"
            )

            print(
                f"2) {self('o_Use_Desktop_Audio_Text')}: "
                f"{'✅' if use_desktop else '❌'}"
            )

            print(
                f"3) {self('o_Online_STT_Model')}: "
                f"{stt_model}"
            )

            print(
                f"4) whisper.cpp {self('o_Binary_Path')}: "
                f"{whisper_bin}"
            )

            print(
                f"5) whisper.cpp {self('o_Model_Path')}: "
                f"{whisper_model}"
            )

            print(
                f"6) {self('o_Use_Timeout')}: "
                f"{'✅' if use_timeout else '❌'}"
            )

            print(f"7) {self('o_Timeout')}: {timeout}")

            print(f"8) {self('o_Use_Silence_Auto_Stop')}: {'✅' if use_silence else '❌'}")
            print(f"9) {self('o_Silence_Duration')}: {silence}")
            print(f"10) {self('o_Go_Back')}")

            choice = self._read_input(
                f"\n{self('o_Selection')}: "
            ).strip()

            if choice == "1":
                cfg["is_mic_online"] = not is_online
                self.save_config(cfg)

            elif choice == "2":
                cfg["use_desktop_voice"] = not use_desktop
                self.save_config(cfg)

            elif choice == "3":
                val = self._read_input(
                    f"{self('o_New_Value')} "
                ).strip()

                if not self._is_escape_input(val):
                    cfg["stt_model_online"] = val
                    self.save_config(cfg)

            elif choice == "4":
                val = self._read_input(
                    f"{self('o_New_Value')} "
                ).strip()

                if not self._is_escape_input(val):
                    cfg["whisper_cpp_bin"] = val
                    self.save_config(cfg)

            elif choice == "5":
                val = self._read_input(
                    f"{self('o_New_Value')} "
                ).strip()

                if not self._is_escape_input(val):
                    cfg["whisper_cpp_model"] = val
                    self.save_config(cfg)

            elif choice == "6":
                cfg["use_stt_timeout"] = not use_timeout
                self.save_config(cfg)

            elif choice == "7":
                val = self._read_input(
                    f"{self('o_New_Value')} "
                ).strip()

                if not self._is_escape_input(val):
                    cfg["stt_timeout"] = val
                    self.save_config(cfg)

            elif choice == "8":
                cfg["use_stt_silence"] = not use_silence
                self.save_config(cfg)

            elif choice == "9":
                val = self._read_input(
                    f"{self('o_New_Value')} "
                ).strip()

                if not self._is_escape_input(val):
                    cfg["stt_silence_duration"] = val
                    self.save_config(cfg)

            elif choice == "10":
                return

    # ----------------- RAG SETTINGS------------------

    def menu_rag_settings(self):
        int_settings = [
            ("recent_message_count", "o_RAG_Recent_Count"),
            ("retrieved_chunk_count", "o_RAG_Retrieved_Count"),
            ("summary_update_every", "o_RAG_Summary_Every"),
            ("memory_chunk_max_chars", "o_RAG_Memory_Chunk_Max"),
            ("summary_max_chars", "o_RAG_Summary_Max"),
            ("code_context_max_chars", "o_RAG_Code_Context_Max"),
        ]

        bool_settings = [
            ("use_summary", "o_RAG_Use_Summary"),
            ("use_recent_messages", "o_RAG_Use_Recent"),
            ("use_retrieval", "o_RAG_Use_Retrieval"),
            ("use_code_context", "o_RAG_Use_Code_Context"),
            ("include_recent_attachments", "o_RAG_Recent_Attachments"),
        ]

        while True:
            self.clear_screen()

            cfg = self.load_config()
            self.ensure_base_config()
            cfg = self.load_config()

            rag = cfg.get("rag_settings", {})
            if not isinstance(rag, dict):
                rag = {}

            print(f"\n={self('o_RAG_Settings')}=")

            n = 1

            for key, label_key in int_settings:
                print(f"{n}) {self(label_key)}: {rag.get(key, '')}")
                n += 1

            for key, label_key in bool_settings:
                val = bool(rag.get(key, False))
                print(f"{n}) {self(label_key)}: {'✅' if val else '❌'}")
                n += 1

            back_num = n
            print(f"{back_num}) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not choice.isdigit():
                continue

            num = int(choice)

            if num == back_num:
                return

            if 1 <= num <= len(int_settings):
                key, label_key = int_settings[num - 1]

                new_value = self._read_input(
                    f"{self(label_key)} ({self('o_New_Value')}) "
                ).strip()

                if self._is_escape_input(new_value):
                    continue

                try:
                    val = int(new_value)

                    if key in ("recent_message_count", "retrieved_chunk_count"):
                        val = max(0, min(val, 50))
                    elif key == "summary_update_every":
                        val = max(1, min(val, 200))
                    else:
                        val = max(100, min(val, 100_000))

                    rag[key] = val
                    cfg["rag_settings"] = rag
                    self.save_config(cfg)

                except Exception:
                    print(f"\n{self('o_Invalid_Value')}")
                    self._read_input(f"\n{self('o_to_Menu')}")

                continue

            bool_start = len(int_settings) + 1
            bool_end = len(int_settings) + len(bool_settings)

            if bool_start <= num <= bool_end:
                idx = num - bool_start
                key, _label_key = bool_settings[idx]

                rag[key] = not bool(rag.get(key, False))
                cfg["rag_settings"] = rag
                self.save_config(cfg)
                continue



    # ----------------- CONTEXT MODE -----------------

    def menu_context_mode(self):
        while True:
            self.clear_screen()
            mode = self.get_context_mode(self.current_chat)

            mode_label = self("o_Context_Direct") if mode == "direct" else self("o_Context_RAG")

            print(f"\n={self('o_Context_Mode_Text')}=")
            print(f"{self('o_Current')}: {mode_label}")
            print(f"1) {self('o_Context_RAG')}")
            print(f"2) {self('o_Context_Direct')}")
            print(f"3) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                self.set_context_mode("rag", self.current_chat)
                print(f"\n✔ {self('o_Context_RAG')}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "2":
                self.set_context_mode("direct", self.current_chat)
                print(f"\n✔ {self('o_Context_Direct')}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "3":
                return



    # ---------------- IMAGES / FILES ----------------

    def list_pending_attachments(self):
        items = []

        for v in self.pending_voices:
            if isinstance(v, dict):
                p = v.get("path", "")
                text = v.get("text", "")
            else:
                p = str(v)
                text = ""

            pp = Path(str(p))

            items.append({
                "type": "voice",
                "name": pp.name,
                "path": str(pp.resolve()) if pp.exists() else str(pp),
                "text": str(text or "").strip()
            })

        for p in self.pending_images:
            pp = Path(str(p))
            items.append({
                "type": "image",
                "name": pp.name,
                "path": str(pp.resolve()) if pp.exists() else str(pp)
            })

        for f in self.pending_files:
            fp = Path(str(f.get("path", "")))
            editable = bool(f.get("edit", False))
            items.append({
                "type": "file",
                "name": f.get("name") or fp.name,
                "path": str(fp.resolve()) if fp.exists() else str(fp),
                "edit": editable
            })

        return items
    
    def delete_pending_attachment_by_number(self, number: int) -> bool:
        items = self.list_pending_attachments()
        idx = number - 101

        if not (0 <= idx < len(items)):
            return False

        item = items[idx]

        if item["type"] == "voice":
            self.pending_voices = [
                x for x in self.pending_voices
                if str(Path(str(x.get("path") if isinstance(x, dict) else x)).resolve()) != item["path"]
            ]

        elif item["type"] == "image":
            self.pending_images = [
                x for x in self.pending_images
                if str(Path(str(x)).resolve()) != item["path"]
            ]
        else:
            self.pending_files = [
                x for x in self.pending_files
                if str(Path(str(x.get("path", ""))).resolve()) != item["path"]
            ]

        print(f"\n✔ {self('o_Delete')}: {item['name']}")
        self._read_input(f"\n{self('o_to_Menu')}")
        return True

    def toggle_pending_file_edit_by_number(self, number: int) -> bool:
        items = self.list_pending_attachments()
        idx = number - 201

        if not (0 <= idx < len(items)):
            return False

        item = items[idx]

        if item["type"] != "file":
            return False

        for f in self.pending_files:
            fp = Path(str(f.get("path", "")))
            fpath = str(fp.resolve()) if fp.exists() else str(fp)

            if fpath == item["path"]:
                f["edit"] = not bool(f.get("edit", False))
                mark = self("o_On") if f["edit"] else self("o_Off")
                print(f"\n✔ {self('o_Edit_Mode')} {mark}: {item['name']}")
                self._read_input(f"\n{self('o_to_Menu')}")
                return True

        return False

    def save_base64_image(self, b64_data: str, ext: str = "png") -> str | None:
        try:
            raw = base64.b64decode(b64_data)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            out = GENERATED_DIR / f"gen-{stamp}.{ext}"
            out.write_bytes(raw)
            return str(out.resolve())
        except Exception as e:
            print(self("o_Error_With_Detail", error=str(e)))
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
            print(self("o_Error_With_Detail", error=str(e)))
            return None

    def add_file_prompt(self):
        self.clear_screen()
        print(f"{self('o_Attach_File')}")
        print(f"{self('o_Type_ESC')}")
        raw = self._read_input(f"\n{self('o_Drag_Drop')}: ")

        if str(raw).strip().lower() in {"esc", ":q"} or raw == "\x1b":
            return False

        raw = raw.strip()
        if not raw:
            return False

        path = raw.strip().strip("'").strip('"')
        try:
            path = str(Path(path).expanduser().resolve())
        except Exception:
            pass

        p = Path(path)
        if not p.exists():
            print(self("o_File_Not_Found"))
            self._read_input(f"\n{self('o_to_Menu')}")
            return False

        import mimetypes
        mime, _ = mimetypes.guess_type(str(p))
        mime = (mime or "").lower()

        if mime.startswith("image/"):
            if str(p) not in self.pending_images:
                self.pending_images.append(str(p))
            print(self("o_Image_Added", name=p.name))
        else:
            if not any(x.get("path") == str(p) for x in self.pending_files):
                edit_default = False
                ext = p.suffix.lower()

                if ext in (".docx", ".pdf", ".xlsx", ".txt", ".md"):
                    edit_default = self.yes_no_prompt(self("o_Edit_Mode"))

                self.pending_files.append({
                    "path": str(p),
                    "name": p.name,
                    "edit": edit_default
                })

            print(self("o_File_Added", name=p.name))

        return True

    def _capture_photo_from_script(self):
        script = Path.home() / ".config" / "scripts" / "screenprint.sh"
        if not script.exists():
            print(self("o_Script_Not_Found", path=script))
            return None

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

            if p.returncode != 0:
                print(self("o_Photo_Capture_Cancelled_Or_Failed"))
                return None

            out = (p.stdout or "").strip()

            if out:
                try:
                    cand = Path(out).expanduser().resolve()
                    if cand.exists() and cand.is_file():
                        old_mtime = before_files.get(str(cand))
                        new_mtime = cand.stat().st_mtime
                        if old_mtime is None or new_mtime > old_mtime or new_mtime >= start_ts:
                            return str(cand)
                except Exception:
                    pass

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

            if not after_files:
                return None

            newest = max(after_files, key=lambda x: x.stat().st_mtime)
            return str(newest.resolve())

        except Exception as e:
            print(self("o_Photo_Capture_Error", error=e))
            return None

    def take_photo_and_queue(self):
        self.clear_screen()
        print(f"{self('o_Take_Photo')}")
        print(f"{self('o_Press_ESC')}\n")

        path = self._capture_photo_from_script()
        if not path:
            return False

        if path not in self.pending_images:
            self.pending_images.append(path)

        print(f"\n{self('o_Photo_Added', name=Path(path).name)}")
        return True

    # --------------- VOICE --------------

    def _get_use_desktop_voice(self) -> bool:
        cfg = self.load_config()
        return bool(cfg.get("use_desktop_voice", False))


    def _is_monitor_like_name(self, name: str) -> bool:
        s = str(name or "").strip().lower()

        bad_words = [
            "monitor",
            ".monitor",
            "loopback",
            "stereo mix",
            "what u hear",
            "mix monitor",
            "output",
        ]

        return any(w in s for w in bad_words)


    def _detect_pw_desktop_target(self) -> str | None:
        pactl = shutil.which("pactl")
        if not pactl:
            return None

        try:
            p = subprocess.run(
                [pactl, "list", "short", "sources"],
                capture_output=True,
                text=True
            )

            if p.returncode != 0:
                return None

            for line in (p.stdout or "").splitlines():
                parts = line.split("\t")

                if len(parts) < 2:
                    continue

                source_name = parts[1].strip()

                if source_name and self._is_monitor_like_name(source_name):
                    return source_name

        except Exception:
            pass

        return None


    def _detect_pw_mic_target(self) -> str | None:
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

                    for line in lines:
                        parts = line.split("\t")

                        if len(parts) < 2:
                            continue

                        source_name = parts[1].strip()

                        if source_name and not self._is_monitor_like_name(source_name):
                            return source_name

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

            lines = [
                x.strip()
                for x in (p.stdout or "").splitlines()
                if x.strip()
            ]

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

    def _wait_recording_stop(self, proc, timeout: float, use_timeout: bool, use_silence: bool):
        start = time.time()

        while True:
            if proc.poll() is not None:
                return "process_stopped"

            elapsed = time.time() - start

            # ENTER ile durdurma
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.15)
                if r:
                    sys.stdin.readline()
                    return "manual"
            except Exception:
                pass

            # Silence detection
            if use_silence and proc.stderr:
                try:
                    r, _, _ = select.select([proc.stderr], [], [], 0)
                    if r:
                        line = proc.stderr.readline()
                        if line:
                            low = line.decode("utf-8", errors="ignore").lower()

                            if ("silence_start" in low or "silence_end" in low) and elapsed >= 1.5:
                                return "silence"
                except Exception:
                    pass

            # Timeout
            if use_timeout:
                remaining = int(timeout - elapsed)

                if remaining <= 0:
                    print()
                    return "timeout"

                print(f"\r⏳ {remaining}", end="", flush=True)


    def record_voice_to_file(self):
        cfg = self.load_config()

        GENERATED_FILES_DIR.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        out = GENERATED_FILES_DIR / f"voice-{stamp}.wav"

        pw = shutil.which("pw-record")
        ar = shutil.which("arecord")

        ffmpeg = shutil.which("ffmpeg")
        use_silence = bool(cfg.get("use_stt_silence", False))

        try:
            silence_duration = float(str(cfg.get("stt_silence_duration", "2")).replace(",", "."))
        except Exception:
            silence_duration = 2.0

        use_desktop_voice = self._get_use_desktop_voice()

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
                str(out)
            ]

        elif pw:
            if use_desktop_voice:
                target = self._detect_pw_desktop_target()

                if not target:
                    print(self("o_Desktop_Audio_Monitor_Not_Found"))
                    return None
            else:
                target = self._detect_pw_mic_target()

                if not target:
                    print(self("o_Microphone_Source_Not_Found"))
                    return None

            cmd = [
                pw,
                "--rate", "16000",
                "--channels", "1",
                "--target", str(target),
                str(out)
            ]

        elif ar:
            if use_desktop_voice:
                print(self("o_Desktop_Audio_Requires_PWRecord"))
                return None

            device = self._detect_arecord_mic_device()

            if not device:
                print(self("o_Microphone_Device_Not_Found"))
                return None

            cmd = [
                ar,
                "-D", str(device),
                "-f", "S16_LE",
                "-r", "16000",
                "-c", "1",
                str(out)
            ]

        else:
            print(self("o_Audio_Recorder_Not_Found"))
            return None

        use_timeout = bool(cfg.get("use_stt_timeout", False))

        try:
            timeout = float(str(cfg.get("stt_timeout", "10")).replace(",", "."))
        except Exception:
            timeout = 10.0

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if use_silence else subprocess.DEVNULL
            )

            print(f"\n{self('o_STT_Recording')}")
            print(f"{self('o_STT_Press_Enter_Stop')}\n")

            stop_reason = self._wait_recording_stop(proc, timeout, use_timeout, use_silence)

            if stop_reason == "timeout":
                print(f"\n{self('o_STT_Recording_Timeout')}")

            if proc.poll() is None:
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

        except KeyboardInterrupt:
            try:
                proc.kill()
            except Exception:
                pass
            return None

        except Exception as e:
            print(self("o_Audio_Record_Start_Failed", error=str(e)))
            return None

        if not out.exists() or out.stat().st_size <= 0:
            return None

        return str(out.resolve())

    def transcribe_voice_file(self, audio_path: str) -> str:
        cfg = self.load_config()

        is_online = bool(cfg.get("is_mic_online", True))

        if is_online:
            return self.transcribe_voice_online(audio_path)

        return self.transcribe_voice_local(audio_path)

    def normalize_stt_text(self, text: str) -> str:
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

        if low.strip().lower() == "empty_audio":
            return "__NO_SPEECH__"

        bad_patterns = [
            "blank_audio",
            "music_audio",
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
            "no audio",
            "cannot transcribe",
            "can't transcribe",
            "no speech",
            "no clear speech",
            "silence",
            "only silence",
            "noise",
            "only noise",
            "background sounds",
            "music only",
            "please speak when you're ready",
            "please speak when you are ready",
            "speak when you're ready",
            "speak when you are ready",
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

    def transcribe_voice_online(self, audio_path: str) -> str:
        import requests

        cfg = self.load_config()

        key = str(cfg.get("open_router_key") or "").strip()
        if not key:
            print(self("o_OpenRouter_STT_Key_Missing"))
            return ""

        model = str(
            cfg.get("stt_model_online")
            or "openai/gpt-audio-mini"
        ).strip()

        try:
            audio_b64 = base64.b64encode(
                Path(audio_path).read_bytes()
            ).decode("utf-8")

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self("o_STT_Transcription_Prompt")
                            },
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": audio_b64,
                                    "format": "wav"
                                }
                            }
                        ]
                    }
                ],
                "stream": False
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
                print(r.text[:500])
                return ""

            data = r.json()
            raw_text = str(
                data["choices"][0]["message"]["content"] or ""
            ).strip()

            return self.normalize_stt_text(raw_text)

        except Exception as e:
            print(self("o_STT_Error", error=str(e)))
            return ""

    def transcribe_voice_local(self, audio_path: str) -> str:
        cfg = self.load_config()

        whisper_bin = str(cfg.get("whisper_cpp_bin", "") or "").strip()
        whisper_model = str(cfg.get("whisper_cpp_model", "") or "").strip()

        if not whisper_bin:
            print(self("o_Whisper_Bin_Not_Found", path=self("o_Unknown_Error")))
            return ""

        if not whisper_model:
            print(self("o_Whisper_Model_Not_Found", path=self("o_Unknown_Error")))
            return ""

        bin_path = Path(whisper_bin).expanduser()
        model_path = Path(whisper_model).expanduser()

        if not bin_path.exists():
            print(self("o_Whisper_Bin_Not_Found", path=bin_path))
            return ""

        if not model_path.exists():
            print(self("o_Whisper_Model_Not_Found", path=model_path))
            return ""

        cmd = [
            str(bin_path),
            "-m",
            str(model_path),
            "-f",
            str(audio_path),
            "-otxt"
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )

            if proc.returncode != 0:
                print(proc.stderr.strip() or proc.stdout.strip() or self("o_Whisper_Failed"))
                return ""

            txt_path = Path(str(audio_path) + ".txt")

            if txt_path.exists():
                text = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
                try:
                    txt_path.unlink()
                except Exception:
                    pass
                return self.normalize_stt_text(text)

            return self.normalize_stt_text(proc.stdout.strip())

        except Exception as e:
            print(self("o_STT_Error", error=e))
            return ""


    def voice_input_flow(self):
        self.clear_screen()
        print(f"{self('o_Microphone')}")
        print(f"{self('o_Type_ESC')}\n")

        audio_path = self.record_voice_to_file()

        if not audio_path:
            self._read_input(f"\n{self('o_to_Menu')}")
            return

        text = self.transcribe_voice_file(audio_path)

        if not text or text == "__NO_SPEECH__":
            print(f"\n{self('o_No_Speech_Detected')}")
            self._read_input(f"\n{self('o_to_Menu')}")
            return

        if not any(
            (v.get("path") if isinstance(v, dict) else v) == audio_path
            for v in self.pending_voices
        ):
            self.pending_voices.append({
                "path": audio_path,
                "text": text
            })

        print("\n---")
        print(text)
        print("---")

        if not self.yes_no_prompt(f"\n{self('o_Send_Message')}?"):
            return

        self.append_user_message(text)

        self.clear_screen()
        print(f"{self('o_AI_Response')}\n")

        t = threading.Thread(target=self.call_ai, daemon=True)
        t.start()

        print(f"\n⏹️ {self('o_Stop_Button')}: esc")

        while t.is_alive():
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.15)

                if r:
                    cmd = sys.stdin.readline().strip().lower()

                    if cmd in ("esc", "stop", "/stop"):
                        self.stop_ai_generation_cli()
                        break

            except KeyboardInterrupt:
                self.stop_ai_generation_cli()
                break

        t.join(timeout=0.2)

        self._read_input(f"\n{self('o_to_Menu')}")

    # ---------------- AI ----------------

    def _prompt_multiline(self):
        print(f"{self('o_sub_Send_Message')}")
        print(f"{self('o_Type_ESC')}\n")

        line = self._read_input_with_ctrl_shortcuts(f"{self('o_Send_Message')}: ")

        if self._is_escape_input(line):
            return ""

        return line.strip()

    def _is_image_file_path(self, path: str) -> bool:
        ext = Path(str(path or "")).suffix.lower()
        return ext in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
        )


    def _message_has_image_file(self, msg: dict) -> bool:
        if not isinstance(msg, dict):
            return False

        if self._is_image_file_path(msg.get("image")):
            return True

        images = msg.get("images")
        if isinstance(images, list):
            for p in images:
                if self._is_image_file_path(p):
                    return True

        files = msg.get("files")
        if isinstance(files, list):
            for f in files:
                if isinstance(f, dict) and self._is_image_file_path(f.get("path")):
                    return True

        return False


    def _build_image_settings_suffix(self) -> str:
        cfg = self.load_config()

        if not bool(cfg.get("use_image_settings", False)):
            return ""

        parts = []

        resolution = str(cfg.get("image_resolution", "")).strip()
        aspect_ratio = str(cfg.get("image_aspect_ratio", "")).strip()
        quality = str(cfg.get("image_quality", "")).strip()
        style = str(cfg.get("image_style", "")).strip()

        if resolution:
            parts.append(f"resolution:{resolution}")
        if aspect_ratio:
            parts.append(f"aspect_ratio:{aspect_ratio}")
        if quality:
            parts.append(f"quality:{quality}")
        if style:
            parts.append(f"style:{style}")

        if not parts:
            return ""

        return "\n\nimage{" + ", ".join(parts) + "}"

    def append_user_message(self, message: str):
        messages = self.load_chat_messages()

        voice_texts = []

        for v in self.pending_voices:
            if isinstance(v, dict):
                t = str(v.get("text") or "").strip()

                if t:
                    voice_texts.append(t)

        final_message = str(message or "").strip()

        if voice_texts:
            combined_voice = "\n".join(voice_texts).strip()

            if not final_message:
                final_message = combined_voice

        ref_set = set()
        selected_refs = sorted([
            i for i in self.selected_ref_indexes
            if isinstance(i, int) and 0 <= i < len(messages)
        ])

        for ridx in selected_refs:
            ref_set |= self.expand_ref_chain(messages, ridx)

        new_message = {
            "role": "user",
            "content": final_message
        }

        if ref_set:
            new_message["used_refs"] = sorted(ref_set)
            new_message["refs_groups"] = self.build_refs_groups_cli(messages, selected_refs)

        if self.pending_voices:
            new_message["voices"] = []

            for v in self.pending_voices:
                if isinstance(v, dict):
                    p = v.get("path", "")
                    text = v.get("text", "")
                else:
                    p = str(v)
                    text = ""

                pp = Path(str(p))

                if pp.exists():
                    new_message["voices"].append({
                        "path": str(pp.resolve()),
                        "text": str(text or "").strip()
                    })

        if self.pending_images:
            new_message["images"] = [
                str(Path(p).resolve()) for p in self.pending_images if Path(p).exists()
            ]

        if self.pending_files:
            new_message["files"] = []
            for f in self.pending_files:
                fp = Path(str(f.get("path", "")))
                if fp.exists():
                    file_obj = {
                        "path": str(fp.resolve()),
                        "name": fp.name,
                        "edit": bool(f.get("edit", False))
                    }

                    new_message["files"].append(file_obj)
        should_add_image_settings = False

        # 1) Şu an gönderilen eklerde image varsa
        if isinstance(new_message.get("images"), list) and new_message["images"]:
            should_add_image_settings = True

        # 2) Referans seçildiyse, referans mesajlarında image var mı?
        if not should_add_image_settings:
            ref_idxs = new_message.get("used_refs") or []

            if isinstance(ref_idxs, list):
                for ridx in ref_idxs:
                    if isinstance(ridx, int) and 0 <= ridx < len(messages):
                        if self._message_has_image_file(messages[ridx]):
                            should_add_image_settings = True
                            break

        if should_add_image_settings:
            image_suffix = self._build_image_settings_suffix()

            if image_suffix and image_suffix not in final_message:
                final_message = final_message + image_suffix
                new_message["content"] = final_message

        messages.append(new_message)
        self.save_chat_messages(messages)

        self.pending_voices.clear()
        self.pending_images.clear()
        self.pending_files.clear()
        self.selected_ref_indexes.clear()

    def finalize_ai_response(self, raw_text: str):
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

        messages = self.load_chat_messages()

        bot_idx = None
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]

            if not isinstance(msg, dict):
                continue

            if msg.get("role") in ("bot", "assistant") and msg.get("streaming"):
                bot_idx = i
                break

            if msg.get("role") == "user":
                break

        bot_msg = {
            "role": "bot",
            "content": str(data.get("content") or "").strip()
        }

        usage = data.get("usage")

        web_req = data.get("web_search_request")

        if isinstance(web_req, dict):
            bot_msg["web_search_request"] = {
                "query": str(web_req.get("query") or "").strip(),
                "status": str(web_req.get("status") or "pending").strip(),
                "err": str(web_req.get("err") or "").strip(),
                "sources": web_req.get("sources") if isinstance(web_req.get("sources"), list) else []
            }

        if isinstance(usage, dict):
            bot_msg["usage"] = usage

        saved_images = []
        seen_saved = set()

        image_candidates = []
        inline_data_urls = []

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

        base64_candidates = []

        for item in inline_data_urls:
            if item.startswith("data:image/") and "," in item:
                base64_candidates.append(item.split(",", 1)[1].strip())

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
            local_path = self.save_base64_image(item, "png")
            if local_path and local_path not in seen_saved:
                seen_saved.add(local_path)
                saved_images.append(local_path)

        if saved_images:
            bot_msg["images"] = saved_images

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

        if bot_idx is not None:
            bot_msg.pop("streaming", None)
            messages[bot_idx] = bot_msg
        else:
            messages.append(bot_msg)

        self.save_chat_messages(messages)
        return bot_msg

    def stream_print_cli(self, text: str):
        text = str(text or "")

        if not text:
            return

        print(text, end="", flush=True)

    def stop_ai_generation_cli(self):
        proc = self.current_ai_process

        if not proc:
            return False

        try:
            self.ai_cancel_requested = True

            try:
                proc.terminate()
            except Exception:
                pass

            try:
                proc.kill()
            except Exception:
                pass

            self.current_ai_process = None

            print(f"\n⏭️ {self('o_Cancelled')}")
            return True

        except Exception:
            return False

    def begin_cli_ai_stream_placeholder(self):
        data = self.load_chat_data_cli()
        messages = data.get("messages", [])

        # Zaten son tarafta streaming bot varsa yenisini açma
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue

            if msg.get("role") in ("bot", "assistant") and msg.get("streaming"):
                self.save_chat_data_cli(data)
                return

            if msg.get("role") == "user":
                break

        messages.append({
            "role": "bot",
            "content": "",
            "streaming": True
        })

        data["messages"] = messages
        self.save_chat_data_cli(data)


    def remove_cli_ai_stream_placeholder(self):
        data = self.load_chat_data_cli()
        messages = data.get("messages", [])

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]

            if not isinstance(msg, dict):
                continue

            if msg.get("role") in ("bot", "assistant") and msg.get("streaming"):
                messages.pop(i)
                break

            if msg.get("role") == "user":
                break

        data["messages"] = messages
        self.save_chat_data_cli(data)

    def call_ai(self):
        selected_arg = ""

        try:
            messages = self.load_chat_messages()
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    used_refs = msg.get("used_refs")
                    if isinstance(used_refs, list) and used_refs:
                        selected_arg = ",".join(
                            str(x) for x in used_refs if isinstance(x, int)
                        )
                    break
        except Exception:
            selected_arg = ""

        context_mode = self.get_context_mode(self.current_chat)

        cmd = [
            sys.executable,
            "-u",
            AI_SCRIPT,
            str(self.current_chat),
            selected_arg or "",
            "--context-mode",
            context_mode,
        ]

        self.begin_cli_ai_stream_placeholder()

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

            self.current_ai_process = proc
            self.ai_cancel_requested = False

            if proc.stdout is None or proc.stderr is None:
                raise RuntimeError(self("o_Unknown_Error"))

            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            partial = ""
            stderr_chunks = []
            json_mode = False
            apply_mode = False

            out_fd = proc.stdout.fileno()
            err_fd = proc.stderr.fileno()

            stream_buffer = []
            stream_lock = threading.Lock()
            stream_timer_active = True
            stream_failed = False

            STREAM_FLUSH_SECONDS = 0.01
            STREAM_CHARS_PER_FLUSH = 8
            last_flush = time.time()

            def flush_stream_buffer(force=False):
                nonlocal stream_timer_active

                if (not stream_timer_active) or stream_failed:
                    return

                if json_mode:
                    return

                with stream_lock:
                    pending = "".join(stream_buffer)

                    if not pending:
                        return

                    if force:
                        chunk = pending
                        stream_buffer.clear()
                    else:
                        chunk = pending[:STREAM_CHARS_PER_FLUSH]
                        rest = pending[STREAM_CHARS_PER_FLUSH:]
                        stream_buffer.clear()

                        if rest:
                            stream_buffer.append(rest)

                if chunk:
                    self.stream_print_cli(chunk)

            while True:
                rlist, _, _ = select.select([out_fd, err_fd], [], [], 0.1)

                if out_fd in rlist:
                    data = os.read(out_fd, 4096)

                    if data:
                        text = decoder.decode(data)

                        if text:
                            partial += text

                            stripped = partial.lstrip()

                            # JSON başladıysa ekrana basma.
                            # Çünkü ai.py final result JSON döndürüyor.
                            if stripped.startswith("{") or stripped.startswith("["):
                                json_mode = True
                            else:
                                # apply bloğu başladıysa terminale basma
                                if re.search(r"(?m)^\s*apply\s*$", partial):
                                    apply_mode = True

                                if not apply_mode:
                                    with stream_lock:
                                        stream_buffer.append(text)

                    else:
                        break

                if err_fd in rlist:
                    data = os.read(err_fd, 4096)

                    if data:
                        stderr_chunks.append(
                            data.decode("utf-8", errors="replace")
                        )

                now = time.time()
                if now - last_flush >= STREAM_FLUSH_SECONDS:
                    flush_stream_buffer()
                    last_flush = now

                if proc.poll() is not None:
                    break

            # decoder içinde kalan UTF-8 parçasını al
            tail = decoder.decode(b"", final=True)
            if tail:
                partial += tail

                if not json_mode and not apply_mode:
                    with stream_lock:
                        stream_buffer.append(tail)

            flush_stream_buffer(force=True)
            stream_timer_active = False

            # Kalan stdout varsa oku
            try:
                rest = os.read(out_fd, 65536)
                if rest:
                    text = decoder.decode(rest)
                    partial += text
            except Exception:
                pass

            try:
                err_rest = os.read(err_fd, 65536)
                if err_rest:
                    stderr_chunks.append(
                        err_rest.decode("utf-8", errors="replace")
                    )
            except Exception:
                pass

            proc.wait()

            self.current_ai_process = None

            stdout_text = fix_mojibake(partial)
            stderr_text = fix_mojibake("".join(stderr_chunks))

            if self.ai_cancel_requested:
                self.handle_ai_error(self("o_Cancelled"))
                return None

            if proc.returncode != 0:
                err = (stderr_text or stdout_text or self("o_Unknown_Error")).strip()
                self.handle_ai_error(err)
                return None

            # Eğer stdout JSON ise finalize et.
            # Eğer stdout gerçek streaming text ise finalize raw text olarak kaydeder.
            bot_msg = self.finalize_ai_response(stdout_text)

            # JSON modda ekrana hiç yazmadık; son cevabı burada göster.
            # Normal text stream modda zaten yazıldı, tekrar basmayalım.
            if json_mode and bot_msg:
                self.print_last_bot_message(bot_msg)

            return bot_msg

        except Exception as e:
            self.current_ai_process = None
            self.handle_ai_error(str(e))
            return None

    def handle_ai_error(self, error_text):
        self.remove_cli_ai_stream_placeholder()

        messages = self.load_chat_messages()

        messages.append({
            "role": "bot",
            "error_key": "o_Error_With_Detail",
            "error_params": {
                "error": str(error_text or self("o_Unknown_Error")).strip()
            }
        })

        self.save_chat_messages(messages)

        print()
        print(self("o_Error_With_Detail", error=str(error_text or self("o_Unknown_Error")).strip()))

    def render_message_text_cli(self, msg: dict) -> str:
        if not isinstance(msg, dict):
            return ""

        if msg.get("error_key"):
            params = msg.get("error_params") or {}
            if not isinstance(params, dict):
                params = {}

            error_key = str(msg.get("error_key"))
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

        text = str(msg.get("content") or "")

        # apply bloğu mesaj içinde kalsın ama history/chat görünümünde gösterilmesin
        text = re.sub(
            r"(?ms)\n?\s*apply\s*\n.*?\n\s*apply\s*$",
            "",
            text
        ).strip()

        return text


    def extract_links_from_text(self, text: str) -> list[str]:
        return re.findall(r"https?://[^\s\]\)\"'>]+", str(text or ""))


    def get_message_by_number(self, number: int):
        messages = self.load_chat_messages()
        idx = number - 1

        if not (0 <= idx < len(messages)):
            return None, None

        return idx, messages[idx]

    def build_header_lines(self):
        self.active_model = self.get_chat_model(self.current_chat)
        model_entry = self.get_chat_model_entry(self.current_chat)

        model_label = model_entry["id"]
        if model_entry.get("local"):
            model_label += f"  [{self('o_Local').upper()}]"
        else:
            model_label += f"  [{self('o_Online').upper()}]"

        labels = [
            self("o_Active_Chat"),
            self("o_Active_Model"),
            self("o_Language"),
        ]

        max_len = max(len(x) for x in labels)

        return [
            "=" * 60,
            f"{labels[0].ljust(max_len)} : {self.current_chat.stem}",
            f"{labels[1].ljust(max_len)} : {model_label}",
            f"{labels[2].ljust(max_len)} : {self.get_ui_language()}",
            "=" * 60,
        ]

    def build_chat_history_lines(self):
        messages = self.load_chat_messages()
        lines = []

        lines.append(f"{self('o_Chats')} / {self('o_History')}")
        lines.append("=" * 60)

        if not messages:
            lines.append("-")
            return lines

        def web_status_label(status: str) -> str:
            status = str(status or "").strip()

            if status == "searched":
                return f"✅ {self('o_Web_Search_Searched')}"
            if status == "searching":
                return f"🔎 {self('o_Web_Searching')}"
            if status == "cancelled":
                return f"⏭️ {self('o_Web_Search_Cancelled')}"
            if status == "pending":
                return f"⏳ {self('o_Web_Search_Request')}"

            return status or "-"


        def apply_status_label(status: str) -> str:
            status = str(status or "").strip()

            if status == "ok":
                return f"✅ {self('o_Apply_Applied')}"
            if status == "fail":
                return f"❌ {self('o_Apply_Failed')}"
            if status == "cancel":
                return f"⏭️ {self('o_Cancelled')}"

            return status or "-"

        for i, msg in enumerate(messages, 1):
            role = str(msg.get("role") or "").upper()
            text = self.render_message_text_cli(msg).strip()

            if len(text) > 240:
                text = text[:240] + "..."

            has_files = bool(msg.get("generated_files"))
            has_images = bool(msg.get("images"))
            has_links = bool(self.extract_links_from_text(text))
            has_voices = isinstance(msg.get("voices"), list) and bool(msg.get("voices"))

            web_req = msg.get("web_search_request")
            apply_status = msg.get("apply_status")

            has_web = isinstance(web_req, dict)
            has_apply = isinstance(apply_status, dict)

            extra = []
            if has_files:
                extra.append("download")
            if has_images:
                extra.append("image")
            if has_links:
                extra.append("links")
            if has_voices:
                extra.append("voice")
            if has_web:
                status = str(web_req.get("status") or "")
                extra.append(f"web:{status}")

            if has_apply:
                status = str(apply_status.get("status") or "")
                extra.append(f"apply:{status}")

            if (i - 1) in self.selected_ref_indexes:
                extra.append("ref")

            suffix = f" [{' | '.join(extra)}]" if extra else ""

            lines.append("")
            lines.append(f"/{i} [{role}]{suffix}")
            lines.extend((text if text else "-").splitlines())

            if has_web:
                query = str(web_req.get("query") or "").strip()
                status = str(web_req.get("status") or "").strip()
                err = str(web_req.get("err") or "").strip()

                lines.append("")
                lines.append(f"  🌐 {self('o_Web_Search_Request')}")
                lines.append(f"     {self('o_Search_Query')}: {query or '-'}")
                lines.append(f"     {self('o_Status')}: {web_status_label(status)}")

                if err:
                    lines.append(f"     ❌ {err}")

            if has_apply:
                status = str(apply_status.get("status") or "").strip()
                err = str(apply_status.get("err") or "").strip()
                files = apply_status.get("files")

                lines.append("")
                lines.append(f"  📝 {self('o_File_Changes')}")
                lines.append(f"     {self('o_Status')}: {apply_status_label(status)}")

                if isinstance(files, list) and files:
                    lines.append(f"     {self('o_Files')}:")
                    for f in files:
                        lines.append(f"     - {f}")

                if err:
                    lines.append(f"     ❌ {err}")

        lines.append("")
        lines.append(f"{self('o_Commands')}:")
        lines.append("/number ref")
        lines.append("/clear refs")
        lines.append("/number copy")
        lines.append("/number regen")
        lines.append("/number open")
        lines.append("/number open 2")
        lines.append("/number links")
        lines.append("/number link 2")
        lines.append("/number download")
        lines.append("/number download all")
        lines.append("/number folder")
        lines.append("/back")

        return lines

    def print_chat_history_curses(self):
        def _screen(stdscr):
            curses.curs_set(1)
            stdscr.keypad(True)

            offset = None
            command = ""

            while True:
                stdscr.erase()
                h, w = stdscr.getmaxyx()

                header = self.build_header_lines()
                history = self.build_chat_history_lines()

                for y, line in enumerate(header[:h]):
                    stdscr.addnstr(y, 0, line, w - 1)

                top = len(header)
                input_h = 2
                body_h = max(1, h - top - input_h)

                max_offset = max(0, len(history) - body_h)

                if offset is None:
                    offset = max_offset
                else:
                    offset = max(0, min(offset, max_offset))

                visible = history[offset:offset + body_h]

                for i, line in enumerate(visible):
                    stdscr.addnstr(top + i, 0, line, w - 1)

                input_y = h - 1
                stdscr.addnstr(input_y, 0, f"{self('o_Selection')}: {command}", w - 1)
                stdscr.refresh()

                ch = stdscr.getch()

                if ch == curses.KEY_UP:
                    offset -= 1
                elif ch == curses.KEY_DOWN:
                    offset += 1
                elif ch in (curses.KEY_PPAGE,):
                    offset -= body_h
                elif ch in (curses.KEY_NPAGE,):
                    offset += body_h
                elif ch in (10, 13):
                    cmd = command.strip()
                    command = ""

                    # Boş Enter basılırsa history ekranında kal
                    if not cmd:
                        continue

                    # Sadece /back gibi komutlarda ana menüye dön
                    if cmd in ("/back", "back", "esc", ":q"):
                        return None

                    # Komutu curses dışına taşı
                    return {
                        "cmd": cmd
                    }

                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    command = command[:-1]
                elif 32 <= ch <= 126:
                    command += chr(ch)

        try:
            result = curses.wrapper(_screen)
        except curses.error:
            result = None

        if isinstance(result, dict):
            cmd = str(result.get("cmd") or "").strip()

            if not cmd:
                return

            low = cmd.lower().strip()

            if low in ("clear refs", "/clear refs", "clear ref", "/clear ref"):
                self.selected_ref_indexes.clear()
                self.print_chat_history_curses()
                return

            is_ref_cmd = bool(re.match(r"^/\d+\s+ref$", cmd, re.I))

            handled = self.handle_number_command(cmd)

            if is_ref_cmd and handled:
                self.print_chat_history_curses()
                return

            if handled:
                self._read_input(f"\n{self('o_to_Menu')}")

    def print_chat_history(self):
        self.print_chat_history_curses()

    def collect_message_paths(self, msg: dict) -> list[dict]:
        items = []

        files = msg.get("generated_files")
        if isinstance(files, list):
            for f in files:
                if not isinstance(f, dict):
                    continue

                path = str(f.get("path") or "").strip()
                if not path:
                    continue

                p = Path(path).expanduser()
                items.append({
                    "type": "file",
                    "path": str(p),
                    "name": str(f.get("name") or p.name)
                })

        images = msg.get("images")
        if isinstance(images, list):
            for img in images:
                p = Path(str(img)).expanduser()
                items.append({
                    "type": "image",
                    "path": str(p),
                    "name": p.name
                })

        if msg.get("image"):
            p = Path(str(msg.get("image"))).expanduser()
            items.append({
                "type": "image",
                "path": str(p),
                "name": p.name
            })

        voices = msg.get("voices")
        if isinstance(voices, list):
            for v in voices:
                if isinstance(v, dict):
                    p = Path(str(v.get("path") or "")).expanduser()
                else:
                    p = Path(str(v)).expanduser()

                items.append({
                    "type": "voice",
                    "path": str(p),
                    "name": p.name
                })

        return [
            item for item in items
            if Path(item["path"]).exists() and Path(item["path"]).is_file()
        ]


    def item_icon(self, item_type: str) -> str:
        if item_type == "image":
            return "🖼️"
        if item_type == "voice":
            return "🎙️"
        return "📄"

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
        rrole = str(rmsg.get("role") or "").strip()
        rtext = str(rmsg.get("content") or "").strip()

        has_img = bool(
            rmsg.get("image") or
            (isinstance(rmsg.get("images"), list) and rmsg.get("images"))
        )

        if not rtext and has_img:
            rtext = f"[{self('o_Image')}]"
        elif has_img:
            rtext = rtext + f" + [{self('o_Image')}]"

        if len(rtext) > 220:
            rtext = rtext[:220] + "..."

        return {
            "role": rrole,
            "text": rtext
        }


    def build_refs_groups_cli(self, messages: list, ref_indexes: list[int]) -> list[dict]:
        groups = []

        for ridx in ref_indexes:
            if not (0 <= ridx < len(messages)):
                continue

            rmsg = messages[ridx]
            if not isinstance(rmsg, dict):
                continue

            items = []

            rg = rmsg.get("refs_groups")
            if isinstance(rg, list) and rg:
                for g in rg:
                    its = g.get("items") if isinstance(g, dict) else None
                    if isinstance(its, list):
                        for it in its:
                            if isinstance(it, dict) and it.get("text"):
                                items.append({
                                    "role": it.get("role", ""),
                                    "text": it.get("text", "")
                                })
            else:
                rp = rmsg.get("refs_preview")
                if isinstance(rp, list) and rp:
                    for it in rp:
                        if isinstance(it, dict) and it.get("text"):
                            items.append({
                                "role": it.get("role", ""),
                                "text": it.get("text", "")
                            })

            items.append(self.pack_preview_item(rmsg))
            groups.append({"items": items})

        return groups

    def find_regen_target_index(self, idx: int, messages: list):
        if not (0 <= idx < len(messages)):
            return None

        msg = messages[idx]

        if str(msg.get("role")) == "user":
            return idx

        for i in range(idx - 1, -1, -1):
            if str(messages[i].get("role")) == "user":
                return i

        return None

    def clear_refs_command(self, cmd: str) -> bool:
        cmd = str(cmd or "").strip().lower()

        if cmd not in ("clear refs", "/clear refs", "clear ref", "/clear ref"):
            return False

        self.selected_ref_indexes.clear()
        return True

    def handle_number_command(self, cmd: str) -> bool:
        cmd = str(cmd or "").strip()

        m = re.match(r"^/(\d+)\s+(open|links|link|download|folder|copy|regen|ref)(?:\s+(\d+|all))?$", cmd, re.I)
        if not m:
            return False

        number = int(m.group(1))
        action = m.group(2).lower()
        arg = str(m.group(3) or "").strip().lower()

        idx, msg = self.get_message_by_number(number)

        if msg is None:
            print(f"\n{self('o_Invalid_Value')}")
            return True

        if action == "ref":
            if idx in self.selected_ref_indexes:
                self.selected_ref_indexes.remove(idx)
            else:
                self.selected_ref_indexes.add(idx)

            count = len(self.selected_ref_indexes)

            if count:
                print(f"\n✔ {count} {self('o_Reference_Message')}")
            else:
                print(f"\n✔ {self('o_Clear_Selection')}")

            return True

        if action == "copy":
            text = self.render_message_text_cli(msg).strip()

            if not text:
                print(f"\n{self('o_No_Text_To_Copy')}")
                return True

            copied = False

            try:
                if shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=text, text=True, check=True)
                    copied = True
                elif shutil.which("xclip"):
                    subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True)
                    copied = True
                elif shutil.which("xsel"):
                    subprocess.run(["xsel", "--clipboard", "--input"], input=text, text=True, check=True)
                    copied = True
            except Exception:
                copied = False

            if copied:
                print(f"\n✔ {self('o_Copy')}")
            else:
                print()
                print(text)

            return True

        if action == "regen":
            messages = self.load_chat_messages()

            target_idx = self.find_regen_target_index(idx, messages)

            if target_idx is None:
                print(f"\n{self('o_No_User_Message_For_Regen')}")
                return True

            keep = messages[:target_idx + 1]

            self.save_chat_messages(keep)

            self.clear_screen()
            print(f"{self('o_AI_Response')}\n")

            bot_msg = self.call_ai()

            # AI hata verdiyse apply/web aramaya çalışma
            if not bot_msg:
                self._read_input(f"\n{self('o_to_Menu')}")
                return True

            # Regen sonrası web search isteği oluştuysa hemen göster
            if self.handle_pending_web_search_requests():
                return True

            # Regen sonrası apply isteği oluştuysa hemen göster
            if self.handle_pending_apply_requests():
                return True

            self._read_input(f"\n{self('o_to_Menu')}")
            return True

        if action in ("links", "link"):
            text = self.render_message_text_cli(msg)
            links = self.extract_links_from_text(text)

            req = msg.get("web_search_request")
            if isinstance(req, dict):
                sources = req.get("sources")
                if isinstance(sources, list):
                    for s in sources:
                        if isinstance(s, str):
                            links += self.extract_links_from_text(s)
                        elif isinstance(s, dict):
                            for key in ("url", "link"):
                                val = s.get(key)
                                if isinstance(val, str):
                                    links.append(val)

            links = list(dict.fromkeys(links))

            if action == "link" and arg.isdigit():
                n = int(arg) - 1
                if 0 <= n < len(links):
                    webbrowser.open(links[n])
                return True

            if not links:
                print(f"\n{self('o_No_Links')}")
                return True

            print()
            for i, link in enumerate(links, 1):
                print(f"{i}) {link}")

            sel = self._read_input(f"\n{self('o_Open_Link_Number_Or_Enter')}: ").strip()
            if sel.isdigit():
                n = int(sel) - 1
                if 0 <= n < len(links):
                    webbrowser.open(links[n])

            return True
        if action == "download":
            downloadable = self.collect_message_paths(msg)

            if not downloadable:
                print(f"\n{self('o_No_Downloadable_Items')}")
                return True

            dest_dir = Path.home() / "Downloads"
            dest_dir.mkdir(parents=True, exist_ok=True)

            def copy_one(item):
                src = Path(item["path"]).expanduser()
                dest = dest_dir / src.name

                if dest.exists():
                    stem = src.stem
                    suffix = src.suffix
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    dest = dest_dir / f"{stem}-{stamp}{suffix}"

                shutil.copy2(src, dest)
                return dest

            if arg == "all":
                copied = []
                for item in downloadable:
                    copied.append(copy_one(item))

                print()
                for p in copied:
                    print(f"✔ {p}")

                return True

            print()
            for i, item in enumerate(downloadable, 1):
                print(f"{i}) {self.item_icon(item['type'])} {item['name']}")

            if arg.isdigit():
                sel = arg
            else:
                sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not sel.isdigit():
                return True

            n = int(sel) - 1
            if not (0 <= n < len(downloadable)):
                return True

            dest = copy_one(downloadable[n])

            print(f"\n✔ {dest}")
            return True

        if action == "open":
            paths = self.collect_message_paths(msg)

            if not paths:
                print(f"\n{self('o_No_Openable_Items')}")
                return True

            if arg.isdigit():
                n = int(arg) - 1
                if not (0 <= n < len(paths)):
                    return True

                path = Path(paths[n]["path"]).expanduser()
                subprocess.Popen(["xdg-open", str(path)])
                return True

            if len(paths) == 1:
                path = Path(paths[0]["path"]).expanduser()
                subprocess.Popen(["xdg-open", str(path)])
                return True

            print()
            for i, item in enumerate(paths, 1):
                print(f"{i}) {self.item_icon(item['type'])} {item['name']}")

            sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if not sel.isdigit():
                return True

            n = int(sel) - 1
            if not (0 <= n < len(paths)):
                return True

            path = Path(paths[n]["path"]).expanduser()
            subprocess.Popen(["xdg-open", str(path)])
            return True

        if action == "folder":
            paths = self.collect_message_paths(msg)

            if not paths:
                print(f"\n{self('o_No_Openable_Folder')}")
                return True

            if arg.isdigit():
                n = int(arg) - 1
                if not (0 <= n < len(paths)):
                    return True

                folder = Path(paths[n]["path"]).expanduser().parent
                subprocess.Popen(["xdg-open", str(folder)])
                return True

            folder = Path(paths[0]["path"]).expanduser().parent
            subprocess.Popen(["xdg-open", str(folder)])
            return True

        return False

    def print_last_bot_message(self, bot_msg: dict):
        cfg = self.load_config()
        show_usage = bool(cfg.get("show_usage", False))

        text = str(bot_msg.get("content") or "").strip()
        text = re.sub(
            r"(?ms)\n?\s*apply\s*\n.*?\n\s*apply\s*$",
            "",
            text
        ).strip()

        if text:
            print(f"\n{self('o_Assistant')}:")
            print(text)
            print()

        imgs = bot_msg.get("images")
        if isinstance(imgs, list) and imgs:
            print(f"{self('o_Generated_Images')}:")
            for p in imgs:
                print(f"- {p}")
            print()

        usage = bot_msg.get("usage")
        if show_usage and isinstance(usage, dict):
            p = int(usage.get("prompt_tokens", 0) or 0)
            c = int(usage.get("completion_tokens", 0) or 0)
            t = int(usage.get("total_tokens", 0) or 0)

            print(f"[{self('o_Input_Tokens')}: {p} | {self('o_Output_Tokens')}: {c} | {self('o_Total_Tokens')}: {t}]")

            show_token_value = bool(cfg.get("show_token_value", False))
            if show_token_value:
                try:
                    token_value = float(str(cfg.get("token_value", "2.0")).replace(",", "."))
                except Exception:
                    token_value = 2.0

                total_price = t * token_value / 1_000_000
                print(f"[{self('o_Total_Token_Price')}: ${total_price:.6f}]")

            print()

    def send_message_flow(self):
        self.clear_screen()
        message = self._prompt_multiline()
        if not message:
            return

        self.append_user_message(message)

        self.clear_screen()
        print(f"{self('o_AI_Response')}\n")

        t = threading.Thread(target=self.call_ai, daemon=True)
        t.start()

        print(f"\n⏹️ {self('o_Stop_Button')}: esc")

        while t.is_alive():
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.15)

                if r:
                    cmd = sys.stdin.readline().strip().lower()

                    if cmd in ("esc", "stop", "/stop"):
                        self.stop_ai_generation_cli()
                        break

            except KeyboardInterrupt:
                self.stop_ai_generation_cli()
                break

        t.join(timeout=0.2)

        # AI cevabından sonra web search isteği oluştuysa hemen aynı ekranda göster.
        if self.handle_pending_web_search_requests():
            return

        # Apply isteği oluştuysa onu da hemen aynı ekranda göster.
        if self.handle_pending_apply_requests():
            return

        self._read_input(f"\n{self('o_to_Menu')}")

    # ---------------- MENUS ----------------

    def print_header(self):
        self.active_model = self.get_chat_model(self.current_chat)
        model_entry = self.get_chat_model_entry(self.current_chat)
        model_label = model_entry["id"]
        if model_entry.get("local"):
            model_label += f"  [{self('o_Local').upper()}]"
        else:
            model_label += f"  [{self('o_Online').upper()}]"
        print("\n" + "=" * 60)
        labels = [
            self('o_Active_Chat'),
            self('o_Active_Model'),
            self('o_Language')
        ]

        max_len = max(len(x) for x in labels)

        print(f"{labels[0].ljust(max_len)} : {self.current_chat.stem}")
        print(f"{labels[1].ljust(max_len)} : {model_label}")
        print(f"{labels[2].ljust(max_len)} : {self.get_ui_language()}")
        print("=" * 60)

        if self.selected_ref_indexes:
            ref_numbers = [
                str(i + 1)
                for i in sorted(self.selected_ref_indexes)
            ]
            print(f"{self('o_Reference_Messages')} [{', '.join(ref_numbers)}]")
            print("-" * 60)

        if self.pending_voices or self.pending_images or self.pending_files:
            print(f"{self('o_Pending_Attachments')}:")
            n = 101

            for v in self.pending_voices:
                if isinstance(v, dict):
                    p = v.get("path", "")
                    text = str(v.get("text") or "").strip()
                else:
                    p = str(v)
                    text = ""

                preview = text if text else Path(p).name

                if len(preview) > 70:
                    preview = preview[:70] + "..."

                print(f"{n}) [🎙️ {self('o_Voice')}] {preview} ({self('o_Delete')})")
                n += 1

            for p in self.pending_images:
                print(f"{n}) [{self('o_image')}] {Path(p).name} ({self('o_Delete')})")
                n += 1

            for f in self.pending_files:
                edit_mark = "📝" if bool(f.get("edit")) else "📄"

                print(
                    f"{n}) [{edit_mark} {self('o_file')}] "
                    f"{f.get('name')} ({self('o_Delete')})"
                )

                n += 1

            print("-" * 60)

    def menu_chat(self):
        while True:
            self.clear_screen()

            cfg = self.load_config()
            pinned = cfg.get("pinned_chats", [])
            if not isinstance(pinned, list):
                pinned = []

            is_pinned = self.current_chat.name in pinned
            pin_label = self("o_Unpin_Current_Chat") if is_pinned else self("o_Pin_Current_Chat")

            print(f"\n={self('o_Chats')}=")
            print(f"{self('o_Active_Chat')}: {self.current_chat.stem}{' 📌' if is_pinned else ''}")
            print(f"1) {self('o_Change_Chat')}")
            print(f"2) {self('o_New_Chat')}")
            print(f"3) {self('o_Delete_Chat')}")
            print(f"4) {self('o_Rename')}")
            print(f"5) {pin_label}")
            print(f"6) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                self.select_chat()

            elif choice == "2":
                self.create_chat()

            elif choice == "3":
                chats = self.list_chat_files()
                if not chats:
                    print(self("o_No_Chats"))
                    continue

                print(f"\n{self('o_Delete_Chat')}:")
                for i, p in enumerate(chats, 1):
                    mark = "*" if p == self.current_chat else " "
                    pin_mark = "📌 " if p.name in pinned else ""
                    print(f"{i}) {mark} {pin_mark}{p.stem}")

                sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

                if not sel.isdigit():
                    continue

                idx = int(sel) - 1
                if not (0 <= idx < len(chats)):
                    continue

                print(f'{self("o_Delete_Chat_Confirm")}')
                if self.yes_no_prompt(f'{self("o_Delete")} "{chats[idx].stem}"?'):
                    self.delete_chat(chats[idx])
                    self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "4":
                chats = self.list_chat_files()
                if not chats:
                    print(self("o_No_Chats"))
                    continue

                print(f"\n{self('o_Rename_Chat')}:")
                for i, p in enumerate(chats, 1):
                    mark = "*" if p == self.current_chat else " "
                    pin_mark = "📌 " if p.name in pinned else ""
                    print(f"{i}) {mark} {pin_mark}{p.stem}")

                sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

                if not sel.isdigit():
                    continue

                idx = int(sel) - 1
                if not (0 <= idx < len(chats)):
                    continue

                new_name = input(f"{self('o_New_Name')} ").strip()
                self.rename_chat(chats[idx], new_name)
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "5":
                self.toggle_pin_current_chat()
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "6":
                return

    def menu_models(self):
        while True:
            self.clear_screen()
            cfg = self.load_config()
            models = self._ensure_min_one_model(cfg)

            print(f"\n{self('o_AI_Model')}")
            print(f"1) {self('o_Change_AI_Model')}")
            print(f"2) {self('o_Add_Model')}")
            print(f"3) {self('o_Delete_Model')}")
            print(f"4) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                print(f"\n{self('o_AI_Models')}:")

                active = self.get_chat_model(self.current_chat)

                for i, m in enumerate(models, 1):
                    model_id = str(m.get("id") or "").strip()
                    is_local = bool(m.get("local", False))
                    mark = "*" if model_id == active else " "
                    local_label = f" [{self('o_Local')}]" if is_local else ""
                    print(f"{i}) {mark} {model_id}{local_label}")

                sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

                if not sel.isdigit():
                    continue

                idx = int(sel) - 1
                if not (0 <= idx < len(models)):
                    continue

                selected = models[idx]
                model_id = str(selected.get("id") or "").strip()
                is_local = bool(selected.get("local", False))

                if not model_id:
                    continue

                self.set_chat_model(self.current_chat, model_id, is_local)
                print(f"\n{self('o_Active_Model')}: {model_id}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "2":
                model_id = self._read_input(
                    f"\n{self('o_Add_Model')} (exp: openai/gpt-4.1-mini): "
                ).strip()

                if self._is_escape_input(model_id):
                    continue

                if not model_id:
                    continue

                is_local = self.yes_no_prompt(f"{self('o_Local_Model')}?")

                self.set_chat_model(self.current_chat, model_id, is_local)

                local_label = f" [{self('o_Local')}]" if is_local else ""
                print(f"\n✔ {self('o_Add_Model')}: {model_id}{local_label}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "3":
                cfg2 = self.load_config()
                models2 = self._ensure_min_one_model(cfg2)

                if len(models2) <= 1:
                    print(f"\n{self('o_Min_Model_Error')}")
                    self._read_input(f"\n{self('o_to_Menu')}")
                    continue

                print(f"\n{self('o_Delete_Model')}:")
                active = self.get_chat_model(self.current_chat)

                for i, m in enumerate(models2, 1):
                    model_id = str(m.get("id") or "").strip()
                    is_local = bool(m.get("local", False))
                    mark = "*" if model_id == active else " "
                    local_label = f" [{self('o_Local')}]" if is_local else ""
                    print(f"{i}) {mark} {model_id}{local_label}")

                sel = self._read_input(f"\n{self('o_Selection')}: ").strip()

                if not sel.isdigit():
                    continue

                idx = int(sel) - 1
                if not (0 <= idx < len(models2)):
                    continue

                selected = models2[idx]
                model_id = str(selected.get("id") or "").strip()

                if not model_id:
                    continue

                if not self.yes_no_prompt(f'{self("o_Delete")} "{model_id}"?'):
                    continue

                models2 = [
                    m for m in models2
                    if str(m.get("id") or "").strip() != model_id
                ]

                if not models2:
                    models2 = [{
                        "id": "google/gemini-2.5-flash-image",
                        "local": False
                    }]

                cfg2["ai_models"] = models2

                new_default = {
                    "id": str(models2[0].get("id") or ""),
                    "local": bool(models2[0].get("local", False))
                }

                chat_models = cfg2.get("chat_models", {})
                if not isinstance(chat_models, dict):
                    chat_models = {}

                for chat_name, value in list(chat_models.items()):
                    if isinstance(value, dict):
                        old_id = str(value.get("id") or "").strip()
                    else:
                        old_id = str(value or "").strip()

                    if old_id == model_id:
                        chat_models[chat_name] = dict(new_default)

                cfg2["chat_models"] = chat_models
                self.save_config(cfg2)

                self.active_model = self.get_chat_model(self.current_chat)

                print(f"\n✔ {self('o_Delete_Model')}: {model_id}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "4":
                return
    def menu_image_settings(self):
        while True:
            self.clear_screen()

            cfg = self.load_config()
            self.ensure_base_config()
            cfg = self.load_config()

            use_image_settings = bool(cfg.get("use_image_settings", False))
            image_resolution = str(cfg.get("image_resolution", "1920x1080") or "1920x1080")
            image_aspect_ratio = str(cfg.get("image_aspect_ratio", "16:9") or "16:9")
            image_quality = str(cfg.get("image_quality", "medium") or "medium")
            image_style = str(cfg.get("image_style", "") or "")

            print(f"\n={self('o_Use_Image_Settings')}=")
            print(f"1) {self('o_Use_Image_Settings')}: {'✅' if use_image_settings else '❌'}")
            print(f"2) {self('o_Image_Resolution')}: {image_resolution}")
            print(f"3) {self('o_Image_Aspect_Ratio')}: {image_aspect_ratio}")
            print(f"4) {self('o_Image_Quality')}: {image_quality}")
            print(f"5) {self('o_Image_Style')}: {image_style}")
            print(f"6) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                cfg["use_image_settings"] = not use_image_settings
                self.save_config(cfg)

            elif choice == "2":
                val = self._read_input(f"{self('o_New_Value')} ").strip()
                if not self._is_escape_input(val):
                    cfg["image_resolution"] = val or "1920x1080"
                    self.save_config(cfg)

            elif choice == "3":
                val = self._read_input(f"{self('o_New_Value')} ").strip()
                if not self._is_escape_input(val):
                    cfg["image_aspect_ratio"] = val or "16:9"
                    self.save_config(cfg)

            elif choice == "4":
                val = self._read_input(f"{self('o_New_Value')} ").strip()
                if not self._is_escape_input(val):
                    cfg["image_quality"] = val or "medium"
                    self.save_config(cfg)

            elif choice == "5":
                val = self._read_input(f"{self('o_New_Value')} ")

                if val == "\x1b" or val.strip().lower() in {"esc", ":q"}:
                    continue

                cfg["image_style"] = val.strip()
                self.save_config(cfg)

            elif choice == "6":
                return

    def menu_personalization(self):
        while True:
            self.clear_screen()
            cfg = self.load_config()

            show_usage = bool(cfg.get("show_usage", True))
            show_token_value = bool(cfg.get("show_token_value", False))
            token_value = str(cfg.get("token_value", "2.0") or "2.0")

            print(f"\n{self('o_Personalization')}")
            print(f"1) {self('o_Token_Usage')}: {'✅' if show_usage else '❌'}")
            print(f"2) {self('o_Use_Token_Value')}: {'✅' if show_token_value else '❌'}")
            print(f"3) {self('o_Token_Value')}: {token_value}")
            print(f"4) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                cfg["show_usage"] = not show_usage
                self.save_config(cfg)

            elif choice == "2":
                cfg["show_token_value"] = not show_token_value
                self.save_config(cfg)

            elif choice == "3":
                new_value = self._read_input(f"{self('o_New_Value')} ").strip()

                if self._is_escape_input(new_value):
                    continue

                try:
                    val = float(new_value.replace(",", "."))
                    if val < 0:
                        raise ValueError
                    cfg["token_value"] = str(val)
                    self.save_config(cfg)
                except Exception:
                    print(f"\n{self('o_Invalid_Value')}")
                    self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "4":
                return

    def menu_ai_settings(self):
        while True:
            self.clear_screen()
            cfg = self.load_config()

            force_ui_language = bool(cfg.get("force_ui_language", False))
            ask_for_web_search = bool(cfg.get("ask_for_web_search", True))

            print(f"\n{self('o_AI_Settings')}")
            print(f"1) {self('o_Force_Language_Text')} ({self('o_Force_Language_Hint')}): {'✅' if force_ui_language else '❌'}")
            print(f"2) {self('o_Ask_Web_Search')}: {'✅' if ask_for_web_search else '❌'}")
            print(f"3) {self('o_AI_Providers')}")
            print(f"4) {self('o_Go_Back')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice == "1":
                cfg["force_ui_language"] = not force_ui_language
                self.save_config(cfg)

            elif choice == "2":
                cfg["ask_for_web_search"] = not ask_for_web_search
                self.save_config(cfg)

            elif choice == "3":
                self.menu_local_providers()

            elif choice == "4":
                return

    def menu_settings(self):
        while True:
            self.clear_screen()
            print(f"\n{self('o_Settings')}")
            print(f"1) {self('o_AI_Model')}")
            print(f"2) {self('o_OpenRouter_API_Key')}")
            print(f"3) {self('o_Tavily_API_Key')}")
            print(f"4) {self('o_Response_Style')}")
            print(f"5) {self('o_Personalization')}")
            print(f"6) {self('o_Language')}")
            print(f"7) {self('o_AI_Settings')}")
            print(f"8) {self('o_Prompt_Chooser')}")
            print(f"9) {self('o_Context_Mode_Text')} ({self('o_Context_RAG')} / {self('o_Context_Direct')})")
            print(f"10) {self('o_RAG_Settings')}")
            print(f"11) {self('o_STT_Settings')}")
            print(f"12) {self('o_Go_Back')}")


            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()


            if choice == "1":
                self.menu_models()

            elif choice == "2":
                cfg = self.load_config()
                current = str(cfg.get("open_router_key", "") or "")

                print(f"\n{self('o_OpenRouter_API_Key')}:")
                print("********" if current else "-")
                print(f"\n{self('o_Type_ESC')}")

                new_key = self._read_input(f"\n{self('o_OpenRouter_Placeholder')}: ").strip()

                if self._is_escape_input(new_key):
                    continue

                if new_key:
                    cfg["open_router_key"] = new_key
                    self.save_config(cfg)
                    print(f"\n✔ {self('o_Saved')} ({self('o_OpenRouter_API_Key')})")
                    self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "3":
                cfg = self.load_config()
                current = str(cfg.get("tavily_api_key", "") or "")

                print(f"\n{self('o_Tavily_API_Key')}:")
                print("********" if current else "-")
                print(f"\n{self('o_Type_ESC')}")

                new_key = self._read_input(f"\n{self('o_Tavily_API_Key')}: ").strip()

                if self._is_escape_input(new_key):
                    continue

                if new_key:
                    cfg["tavily_api_key"] = new_key
                    self.save_config(cfg)
                    print(f"\n✔ {self('o_Saved')} ({self('o_Tavily_API_Key')})")
                    self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "4":
                while True:
                    self.clear_screen()

                    cfg = self.load_config()
                    current = str(cfg.get("response_style", "") or "")
                    use_image_settings = bool(cfg.get("use_image_settings", False))

                    print(f"\n={self('o_Response_Style')}=")
                    print(f"1) {self('o_Response_Style')}: {current}")
                    print(f"2) {self('o_Use_Image_Settings')}: {'✅' if use_image_settings else '❌'}")
                    print(f"3) {self('o_Go_Back')}")

                    sub = self._read_input(f"\n{self('o_Selection')}: ").strip()

                    if sub == "1":
                        print(f"\n{self('o_Response_Style')}:\n{current}\n")
                        print(f"\n{self('o_Type_ESC')}")
                        print(f"\n\n{self('o_New_Value')}")

                        new_style = self._read_input(
                            f"\n({self('o_Response_Style_Placeholder')}) "
                        ).strip()

                        if self._is_escape_input(new_style):
                            continue

                        if new_style:
                            cfg["response_style"] = new_style
                            self.save_config(cfg)
                            print(f"\n✔ {self('o_Saved')} ({self('o_Response_Style')})")
                            self._read_input(f"\n{self('o_to_Menu')}")

                    elif sub == "2":
                        self.menu_image_settings()

                    elif sub == "3":
                        break


            elif choice == "5":
                self.menu_personalization()

            elif choice == "6":
                self.clear_screen()

                current = self.get_ui_language()
                langs = self.get_available_ui_languages()

                print(f"\n{self('o_Language')}: {current}")

                if langs:
                    print(f"\n{self('o_Available_Languages')}:")
                    for lang in langs:
                        mark = "*" if lang == current else " "
                        print(f"  {mark} {lang}")

                print(f"\n{self('o_Language_Select_Hint')}")
                print(f"{self('o_Type_ESC')}")

                new_lang = self._read_input(f"\n{self('o_New_Value')} ").strip().lower()

                if self._is_escape_input(new_lang):
                    continue

                if new_lang not in langs:
                    print(f"\n{self('o_Invalid_Language_Code')}")
                    self._read_input(f"\n{self('o_to_Menu')}")
                    continue

                self.set_ui_language(new_lang)

                print(f"\n✔ {self('o_Saved')} ({self('o_Language')}): {new_lang}")
                self._read_input(f"\n{self('o_to_Menu')}")

            elif choice == "7":
                self.menu_ai_settings()

            elif choice == "8":
                self.menu_prompt_chooser()

            elif choice == "9":
                self.menu_context_mode()

            elif choice == "10":
                self.menu_rag_settings()

            elif choice == "11":
                self.menu_stt_settings()

            elif choice == "12":
                return



    def run(self):
        while True:
            self.clear_screen()
            self.print_header()

            if self.handle_pending_apply_requests():
                continue

            if self.handle_pending_web_search_requests():
                continue

            print(f"1) {self('o_History')}")
            print(f"2) {self('o_Send_Message')}")
            print(f"3) {self('o_Microphone')}")
            print(f"4) {self('o_Take_Photo')}")
            print(f"5) {self('o_Attach_File')}")
            print(f"6) {self('o_Chats')}")
            print(f"7) {self('o_Settings')}")
            print(f"8) {self('o_Exit')}")

            choice = self._read_input(f"\n{self('o_Selection')}: ").strip()

            if choice.isdigit():
                num = int(choice)

                if num >= 201:
                    if self.toggle_pending_file_edit_by_number(num):
                        continue

                if num >= 101:
                    if self.delete_pending_attachment_by_number(num):
                        continue

            if choice == "1":
                self.print_chat_history()

            elif choice == "2":
                self.send_message_flow()

            elif choice == "3":
                self.voice_input_flow()

            elif choice == "4":
                ok = self.take_photo_and_queue()
                if ok:
                    self.send_message_flow()

            elif choice == "5":
                ok = self.add_file_prompt()
                if ok:
                    self.send_message_flow()

            elif choice == "6":
                self.menu_chat()

            elif choice == "7":
                self.menu_settings()

            elif choice == "8":
                self.clear_screen()
                print("🏁")
                return


if __name__ == "__main__":
    cli = ChatCLI()
    cli.run()
