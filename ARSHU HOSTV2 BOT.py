# -*- coding: utf-8 -*-
"""Single-file Telegram Bot Hosting system.
Migrated from the original Arafat Hosting Bot to a Pyrogram/Kurigram-compatible
architecture while preserving the original command, callback, file, security,
admin, subscription and process-management logic.
"""

# ============================================================
# CONFIGURATION
# ============================================================
API_ID = 39541385
API_HASH = "56411b5754673b3f2299b710e1296b97"
BOT_TOKEN = "8786987752:AAEgu9NoTCcaQH2-rwbGzqEJbRrJ46yyDvA"
OWNER_ID = 8024522916
ADMIN_IDS = {8024522916}

MONGO_URI = "mongodb+srv://skr346902_db_user:JziFmympo2SMfwjI@cloud1.bwgggor.mongodb.net/?appName=Cloud1"
MONGO_DB_NAME = "Cloud1"
BOT_DATABASE_CHANNEL_ID = -1002817146570
LOG_CHANNEL_ID = -1002854075903

YOUR_USERNAME = "@y0hxl"
UPDATE_CHANNEL = "@arshuera"
FREE_USER_LIMIT = 3
SUBSCRIBED_USER_LIMIT = 100
ADMIN_LIMIT = 9999
OWNER_LIMIT = float("inf")
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_SCRIPT_RUNTIME = 0  # 0 = unlimited runtime for hosted user bots (24/7)
MAX_RESTARTS = 5
RESTART_WINDOW_SECONDS = 600
MONITOR_INTERVAL = 10
MEMBERSHIP_CACHE_TTL = 60
ERROR_SUPPRESS_SECONDS = 60

# ============================================================
# IMPORTS
# ============================================================
import asyncio
import atexit
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import psutil
from flask import Flask
from threading import Thread

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, "upload_bots")
IROTECH_DIR = os.path.join(BASE_DIR, "inf")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError as exc:
    raise RuntimeError("Motor is required. Install it with: pip install motor") from exc

try:
    from pyrogram import Client, enums, filters, handlers
    from pyrogram.errors import RPCError, FloodWait, MessageNotModified
    from pyrogram.types import (
        InlineKeyboardMarkup as PyroInlineKeyboardMarkup,
        InlineKeyboardButton as PyroInlineKeyboardButton,
        ReplyKeyboardMarkup as PyroReplyKeyboardMarkup,
        KeyboardButton as PyroKeyboardButton,
    )
except ImportError as exc:
    raise RuntimeError("Kurigram/Pyrogram is required. Install kurigram") from exc

try:
    ButtonStyle = enums.ButtonStyle
except AttributeError:
    class ButtonStyle:
        PRIMARY = "primary"
        SUCCESS = "success"
        DANGER = "danger"

TelegramCompatError = RPCError

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("bot_hosting")

# ============================================================
# CUSTOM SMALL-CAPS FONT
# ============================================================
SMALL_CAPS_MAP = dict(zip(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴧʙᴄᴅєꜰɢʜιᴊᴋʟᴍɴσᴩǫʀѕтυνω᥊ʏᴢᴧʙᴄᴅєꜰɢʜιᴊᴋʟᴍɴσᴩǫʀѕтυνω᥊ʏᴢ"
))

def sc(text: str) -> str:
    """Convert normal UI text to the requested small-caps alphabet."""
    if not isinstance(text, str):
        return text
    return "".join(SMALL_CAPS_MAP.get(ch, ch) for ch in text)

_UI_PROTECTED = re.compile(r"(```.*?```|`[^`]*`|https?://[^\s]+|tg://[^\s]+|@[A-Za-z0-9_]+)", re.S)

def ui_text(text):
    """Style user-facing text while preserving code, URLs, filenames and handles."""
    if not isinstance(text, str):
        return text
    out=[]; pos=0
    for m in _UI_PROTECTED.finditer(text):
        out.append(sc(text[pos:m.start()]))
        out.append(m.group(0))
        pos=m.end()
    out.append(sc(text[pos:]))
    return "".join(out)

# ============================================================
# KURIGRAM NATIVE BUTTON STYLING
# ============================================================
# Telegram inline keyboards support native button styles.  Keep all button
# creation behind make_button() so the whole legacy UI automatically gets
# consistent PRIMARY / SUCCESS / DANGER styling without changing callback data.
try:
    BUTTON_PRIMARY = enums.ButtonStyle.PRIMARY
    BUTTON_SUCCESS = enums.ButtonStyle.SUCCESS
    BUTTON_DANGER = enums.ButtonStyle.DANGER
except (AttributeError, TypeError):
    # Compatibility fallback for older Kurigram/Pyrogram builds.  The actual
    # native enum is preferred whenever the installed client exposes it.
    BUTTON_PRIMARY = "primary"
    BUTTON_SUCCESS = "success"
    BUTTON_DANGER = "danger"


def _normalize_button_style(style):
    """Return a Kurigram-native ButtonStyle value when available."""
    if style is None:
        return BUTTON_PRIMARY
    if hasattr(style, "value"):
        return style
    value = str(style).strip().lower()
    return {
        "primary": BUTTON_PRIMARY,
        "success": BUTTON_SUCCESS,
        "danger": BUTTON_DANGER,
    }.get(value, BUTTON_PRIMARY)


def _auto_button_style(text):
    """Choose a safe semantic style for legacy buttons."""
    t = str(text).lower()
    danger = (
        "delete", "remove", "ban", "stop", "reject", "lock",
        "🗑", "❌", "🚫", "🔴",
    )
    success = (
        "start", "run", "save", "approve", "add", "confirm",
        "unlock", "verify", "🟢", "✅", "➕",
    )
    if any(token in t for token in danger):
        return BUTTON_DANGER
    if any(token in t for token in success):
        return BUTTON_SUCCESS
    return BUTTON_PRIMARY


class CompatButton:
    """Small compatibility wrapper used by the migrated legacy UI."""

    def __init__(self, text, callback_data=None, url=None, style=None):
        if callback_data is not None and url is not None:
            raise ValueError("An inline button cannot have both callback_data and url")
        if callback_data is None and url is None:
            raise ValueError("An inline button requires callback_data or url")
        self.text = text
        self.callback_data = callback_data
        self.url = url
        self.style = _normalize_button_style(style or _auto_button_style(text))

    def build(self):
        # Kurigram/Pyrogram exposes Telegram's native style field directly on
        # InlineKeyboardButton. Do not emulate colours with Unicode/HTML.
        kwargs = {
            "text": ui_text(self.text),
            "style": self.style,
        }
        if self.callback_data is not None:
            kwargs["callback_data"] = str(self.callback_data)
        if self.url is not None:
            kwargs["url"] = self.url
        return PyroInlineKeyboardButton(**kwargs)


def make_button(text, callback_data=None, url=None, style=BUTTON_PRIMARY):
    """Create a native Kurigram inline button with PRIMARY/SUCCESS/DANGER style."""
    return CompatButton(
        text=text,
        callback_data=callback_data,
        url=url,
        style=style,
    )


class CompatInlineMarkup:
    def __init__(self, row_width=2):
        self.row_width = max(1, int(row_width))
        self.rows = []

    def add(self, *buttons):
        row = []
        for button in buttons:
            row.append(button)
            if len(row) >= self.row_width:
                self.rows.append(row)
                row = []
        if row:
            self.rows.append(row)
        return self

    def row(self, *buttons):
        self.rows.append(list(buttons))
        return self

    def build(self):
        return PyroInlineKeyboardMarkup([
            [button.build() if hasattr(button, "build") else button for button in row]
            for row in self.rows
        ])


class CompatKeyboardButton:
    """Reply-keyboard buttons are intentionally unstyled.

    Telegram's native colour styles apply to inline buttons, not ordinary
    reply-keyboard buttons. Passing a style field here would break clients.
    """

    def __init__(self, text, **_kwargs):
        self.text = text

    def build(self):
        return PyroKeyboardButton(text=ui_text(self.text))


class CompatReplyMarkup:
    def __init__(self, resize_keyboard=True, row_width=2, one_time_keyboard=False):
        self.resize_keyboard = resize_keyboard
        self.row_width = max(1, int(row_width))
        self.rows = []
        self.one_time_keyboard = one_time_keyboard

    def add(self, *buttons):
        row = []
        for button in buttons:
            row.append(button)
            if len(row) >= self.row_width:
                self.rows.append(row)
                row = []
        if row:
            self.rows.append(row)
        return self

    def row(self, *buttons):
        self.rows.append(list(buttons))
        return self

    def build(self):
        return PyroReplyKeyboardMarkup(
            [[button.build() if hasattr(button, "build") else button for button in row]
             for row in self.rows],
            resize_keyboard=self.resize_keyboard,
            one_time_keyboard=self.one_time_keyboard,
        )


class CompatTypes:
    InlineKeyboardMarkup = CompatInlineMarkup
    ReplyKeyboardMarkup = CompatReplyMarkup
    KeyboardButton = CompatKeyboardButton


types = CompatTypes()
# Route every legacy InlineKeyboardButton construction through the same
# make_button() helper so existing handlers automatically get native styling.
types.InlineKeyboardButton = make_button

# ============================================================
# MONGODB ASYNC BRIDGE
# ============================================================
class MongoStore:
    def __init__(self, uri, db_name):
        self.uri=uri; self.db_name=db_name
        self.loop=asyncio.new_event_loop()
        self.thread=threading.Thread(target=self._thread_main, name="mongo-loop", daemon=True)
        self.ready=threading.Event(); self.failed=None; self.client=None; self.db=None
        self.thread.start()
        if not self.ready.wait(30): raise RuntimeError("MongoDB initialization timed out")
        if self.failed: raise self.failed
    def _thread_main(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.client=AsyncIOMotorClient(
                self.uri, maxPoolSize=50, minPoolSize=2,
                serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                socketTimeoutMS=30000, retryWrites=True,
            )
            self.db=self.client[self.db_name]
            self.loop.run_until_complete(self._ping())
        except Exception as e:
            self.failed=e; self.ready.set(); return
        self.ready.set(); self.loop.run_forever()
    async def _ping(self): await self.client.admin.command("ping")
    def call(self, coro, timeout=60):
        if not self.thread.is_alive(): raise RuntimeError("MongoDB worker stopped")
        fut=asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)
    async def init_indexes_async(self):
        await self.db.users.create_index("user_id", unique=True)
        await self.db.subscriptions.create_index("user_id", unique=True)
        await self.db.files.create_index("file_id", unique=True)
        await self.db.files.create_index([("user_id",1),("file_name",1)], unique=True)
        await self.db.files.create_index("database_message_id")
        await self.db.hosted_bots.create_index([("user_id",1),("file_name",1)], unique=True)
        await self.db.hosted_bots.create_index("status")
        await self.db.admins.create_index("user_id", unique=True)
        await self.db.banned_users.create_index("user_id", unique=True)
        await self.db.user_limits.create_index("user_id", unique=True)
        await self.db.mandatory_channels.create_index("channel_id", unique=True)
        await self.db.install_logs.create_index([("install_date",-1)])
        await self.db.install_logs.create_index("user_id")
        await self.db.settings.create_index("key", unique=True)
    def init_indexes(self): self.call(self.init_indexes_async())
    def close(self):
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
            if self.client: self.client.close()
        except Exception: pass

mongo=MongoStore(MONGO_URI, MONGO_DB_NAME)

# ============================================================
# GLOBAL STATE / CACHE
# ============================================================
bot_scripts={}
user_subscriptions={}
user_files={}
active_users=set()
admin_ids=set(ADMIN_IDS)
banned_users=set()
user_limits={}
bot_locked=False
pending_modules={}
manual_install_requests={}
mandatory_channels={}
pending_zip_files={}
pending_next_steps={}
membership_cache={}
error_cache={}
restart_history={}
shutdown_event=threading.Event()
DB_LOCK=threading.RLock()

SECURITY_CONFIG={
    "blocked_modules":["os.system","os","zipfile","subprocess.Popen","subprocess","eval","exec","compile","__import__"],
    "max_file_size":MAX_FILE_SIZE,
    "max_script_runtime":MAX_SCRIPT_RUNTIME,
    "allowed_extensions":[".py",".js",".zip"],
    "blocked_imports":["shutil.rmtree","subprocess","os.remove","os.unlink"],
}

# ============================================================
# FLASK KEEP-ALIVE
# ============================================================
app=Flask(__name__)
@app.route("/")
def home(): return "ARAFAT HOSTING BOT"
def run_flask():
    try: app.run(host="0.0.0.0", port=8080, use_reloader=False)
    except Exception as e: logger.error("Keep-alive server failed: %s", e, exc_info=True)
def keep_alive():
    Thread(target=run_flask, name="flask-keepalive", daemon=True).start()

# ============================================================
# KURIGRAM CLIENT + LEGACY DISPATCH REGISTRY
# ============================================================
pyro_client=Client(
    "bot_hosting",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    workers=32,
)

_message_rules=[]
_callback_rules=[]

class BotAdapter:
    """Synchronous facade used by the preserved business logic.
    All Telegram operations execute through the single Pyrogram/Kurigram client.
    """
    def __init__(self, client): self.client=client
    def _run(self, coro, timeout=120):
        try:
            asyncio.get_running_loop()
            running=True
        except RuntimeError:
            running=False
        if running and threading.current_thread() is threading.main_thread():
            raise RuntimeError("Blocking Telegram adapter call from the Pyrogram event loop")
        if getattr(pyro_client,"is_connected",False):
            return asyncio.run_coroutine_threadsafe(coro, pyro_client.loop).result(timeout)
        return asyncio.run(coro)
    def _markup(self, m):
        if hasattr(m,"build"): return m.build()
        return m
    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kwargs):
        return self._run(self.client.send_message(chat_id, ui_text(text), reply_markup=self._markup(reply_markup), parse_mode=parse_mode, **kwargs))
    def reply_to(self, message, text, reply_markup=None, parse_mode=None, **kwargs):
        return self.send_message(message.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode, reply_to_message_id=message.id, **kwargs)
    def edit_message_text(self, text, chat_id, message_id, reply_markup=None, parse_mode=None, **kwargs):
        return self._run(self.client.edit_message_text(chat_id, message_id, ui_text(text), reply_markup=self._markup(reply_markup), parse_mode=parse_mode, **kwargs))
    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return self._run(self.client.edit_message_reply_markup(chat_id, message_id, reply_markup=self._markup(reply_markup)))
    def delete_message(self, chat_id, message_id): return self._run(self.client.delete_messages(chat_id, message_id))
    def answer_callback_query(self, callback_query_id, text=None, show_alert=False, **kwargs):
        return self._run(self.client.answer_callback_query(callback_query_id, text=ui_text(text) if text else None, show_alert=show_alert, **kwargs))
    def send_chat_action(self, chat_id, action="typing"):
        action_map={"typing":enums.ChatAction.TYPING,"upload_document":enums.ChatAction.UPLOAD_DOCUMENT,"upload_photo":enums.ChatAction.UPLOAD_PHOTO}
        return self._run(self.client.send_chat_action(chat_id, action_map.get(action, action)))
    def send_photo(self, chat_id, photo, caption=None, parse_mode=None, **kwargs):
        return self._run(self.client.send_photo(chat_id, photo, caption=ui_text(caption) if caption else None, parse_mode=parse_mode, **kwargs))
    def send_video(self, chat_id, video, caption=None, parse_mode=None, **kwargs):
        return self._run(self.client.send_video(chat_id, video, caption=ui_text(caption) if caption else None, parse_mode=parse_mode, **kwargs))
    def forward_message(self, chat_id, from_chat_id, message_id): return self._run(self.client.forward_messages(chat_id, from_chat_id, message_id))
    def get_chat(self, chat_id): return self._run(self.client.get_chat(chat_id))
    def get_me(self): return self._run(self.client.get_me())
    def get_chat_member(self, chat_id, user_id):
        member=self._run(self.client.get_chat_member(chat_id,user_id))
        status=getattr(member,"status","")
        if hasattr(status,"value"): status=status.value
        return SimpleNamespace(status=str(status).lower(), user=member.user, raw=member)
    def get_file(self, file_id): return SimpleNamespace(file_path=file_id, file_id=file_id)
    def download_file(self, file_path):
        data=self._run(self.client.download_media(file_path, in_memory=True))
        if hasattr(data,"getvalue"): return data.getvalue()
        if isinstance(data,bytes): return data
        with open(data,"rb") as f: return f.read()
    def register_next_step_handler(self, message, callback):
        pending_next_steps[(message.chat.id, message.from_user.id if message.from_user else 0)] = callback
    def message_handler(self, **kwargs):
        def deco(func): _message_rules.append((kwargs,func)); return func
        return deco
    def callback_query_handler(self, func=None, **kwargs):
        def deco(handler): _callback_rules.append((func,handler)); return handler
        return deco
    def infinity_polling(self, **kwargs): return self.client.run()

bot=BotAdapter(pyro_client)

# ============================================================
# GLOBAL LOGGING / ERROR SYSTEM
# ============================================================
def _log_error_signature(exc, context=None):
    return f"{type(exc).__name__}:{str(exc)}:{context or ''}"[:1000]

def send_log(text, level="INFO", user_id=None, event=None):
    payload=(f"━━━━━━━━━━━━━━━━━━━━\n{level}\n━━━━━━━━━━━━━━━━━━━━\n"
             f"Event: {event or '-'}\nUser ID: {user_id or '-'}\n\n{text}\n━━━━━━━━━━━━━━━━━━━━")
    try: bot.send_message(LOG_CHANNEL_ID, payload)
    except Exception as e: logger.warning("Log channel send failed: %s", e)

def report_exception(exc, context=None, user_id=None, event=None):
    sig=_log_error_signature(exc, context)
    now=time.time()
    if now-error_cache.get(sig,0)<ERROR_SUPPRESS_SECONDS:
        return
    error_cache[sig]=now
    tb=traceback.format_exc()
    logger.error("%s", payload if False else f"{context or 'Unhandled exception'}: {exc}", exc_info=True)
    send_log(f"Type: {type(exc).__name__}\nMessage: {exc}\n\nTraceback:\n{tb[-6000:]}", "ERROR", user_id, event or context)

# ============================================================
# PERSISTENCE HELPERS
# ============================================================
def init_db():
    mongo.init_indexes()
    now=datetime.utcnow().isoformat()
    mongo.call(mongo.db.admins.update_one({"user_id":OWNER_ID},{"$setOnInsert":{"user_id":OWNER_ID,"added_by":OWNER_ID,"added_date":now} },upsert=True))
    for aid in ADMIN_IDS:
        mongo.call(mongo.db.admins.update_one({"user_id":aid},{"$setOnInsert":{"user_id":aid,"added_by":OWNER_ID,"added_date":now}},upsert=True))

def load_data():
    async def _load():
        users,subs,files,admins,banned,limits,channels=await asyncio.gather(
            self_db.users.find({}, {"user_id":1}).to_list(None),
            self_db.subscriptions.find({}, {"user_id":1,"expiry":1}).to_list(None),
            self_db.files.find({}, {"user_id":1,"file_name":1,"file_type":1}).to_list(None),
            self_db.admins.find({}, {"user_id":1}).to_list(None),
            self_db.banned_users.find({}, {"user_id":1}).to_list(None),
            self_db.user_limits.find({}, {"user_id":1,"file_limit":1}).to_list(None),
            self_db.mandatory_channels.find({}, {"channel_id":1,"channel_username":1,"channel_name":1}).to_list(None),
        )
        return users,subs,files,admins,banned,limits,channels
    users,subs,files,admins,banned,limits,channels=mongo.call(_load())
    for d in subs:
        try: user_subscriptions[int(d["user_id"])]={"expiry":datetime.fromisoformat(d["expiry"])}
        except Exception: pass
    for d in files: user_files.setdefault(int(d["user_id"]),[]).append((d["file_name"],d.get("file_type","py")))
    active_users.update(int(d["user_id"]) for d in users)
    admin_ids.update(int(d["user_id"]) for d in admins)
    banned_users.update(int(d["user_id"]) for d in banned)
    for d in limits: user_limits[int(d["user_id"])]=int(d["file_limit"])
    for d in channels: mandatory_channels[str(d["channel_id"])]={"username":d.get("channel_username"),"name":d.get("channel_name")}

self_db=mongo.db
init_db(); load_data()

# ============================================================
# FILE DATABASE CHANNEL / MONGO METADATA
# ============================================================
def store_file_in_bot_database(file_path, file_name, user_id, file_type):
    try:
        msg=bot._run(pyro_client.send_document(BOT_DATABASE_CHANNEL_ID, file_path, caption=f"file: {file_name}\nuser: {user_id}"))
        doc=getattr(msg,"document",None)
        telegram_file_id=getattr(doc,"file_id",None)
        record={"file_id":f"{user_id}:{file_name}","user_id":user_id,"file_name":file_name,"file_type":file_type,"telegram_file_id":telegram_file_id,"database_message_id":msg.id,"database_channel_id":BOT_DATABASE_CHANNEL_ID,"file_size":getattr(doc,"file_size",None),"uploaded_at":datetime.utcnow().isoformat(),"status":"stored"}
        mongo.call(self_db.files.update_one({"file_id":record["file_id"]},{"$set":record},upsert=True))
        send_log(f"File: {file_name}\nType: {file_type}\nStatus: stored\nDatabase message: {msg.id}","INFO",user_id,"FILE_STORED")
        return record
    except Exception as e:
        report_exception(e,"store_file_in_bot_database",user_id,"FILE_STORE")
        return None

def mark_hosted_bot(user_id,file_id,file_name,file_type,status,**extra):
    doc={"user_id":user_id,"file_id":file_id,"file_name":file_name,"file_type":file_type,"status":status,"updated_at":datetime.utcnow().isoformat(),**extra}
    mongo.call(self_db.hosted_bots.update_one({"user_id":user_id,"file_name":file_name},{"$set":doc,"$setOnInsert":{"created_at":datetime.utcnow().isoformat()}},upsert=True))

def remove_hosted_bot(user_id,file_name):
    mongo.call(self_db.hosted_bots.delete_one({"user_id":user_id,"file_name":file_name}))

def recover_file_from_database_channel(record, destination):
    try:
        os.makedirs(os.path.dirname(destination),exist_ok=True)
        if os.path.exists(destination) and os.path.getsize(destination)>0: return True
        msg_id=record.get("database_message_id") or record.get("archive_database_message_id")
        if not msg_id: return False
        db_message=bot._run(pyro_client.get_messages(BOT_DATABASE_CHANNEL_ID,int(msg_id)))
        data=bot._run(pyro_client.download_media(db_message,in_memory=True))
        if hasattr(data,"getvalue"): data=data.getvalue()
        archive_name=record.get("archive_file_name")
        if archive_name:
            with tempfile.TemporaryDirectory(prefix="recover_") as td:
                archive_path=os.path.join(td,os.path.basename(archive_name))
                with open(archive_path,"wb") as f: f.write(data)
                with zipfile.ZipFile(archive_path,"r") as z:
                    root=os.path.abspath(td)
                    for member in z.infolist():
                        target=os.path.abspath(os.path.join(root,member.filename))
                        if not target.startswith(root+os.sep) and target!=root: raise zipfile.BadZipFile("Unsafe recovery path")
                    z.extractall(td)
                extracted=os.path.join(td,record.get("file_name"))
                if os.path.exists(extracted): shutil.copy2(extracted,destination); return True
                # If the archive stored the script in a directory, locate the exact basename safely.
                wanted=os.path.basename(destination)
                for base,_,names in os.walk(td):
                    if wanted in names:
                        shutil.copy2(os.path.join(base,wanted),destination); return True
                return False
        with open(destination,"wb") as f: f.write(data)
        return True
    except Exception as e:
        report_exception(e,"recover_file_from_database_channel",record.get("user_id"),"FILE_RECOVERY")
        return False

# ============================================================
# CENTRALIZED MESSAGE DISPATCH
# ============================================================
class MessageProxy:
    def __init__(self, message): self._message=message
    @property
    def message_id(self): return getattr(self._message,"id",getattr(self._message,"message_id",None))
    @property
    def id(self): return self.message_id
    def __getattr__(self,name):
        value=getattr(self._message,name)
        if name=="reply_to_message" and value is not None: return MessageProxy(value)
        return value

class CallbackProxy:
    def __init__(self, call): self._call=call
    @property
    def message(self): return MessageProxy(self._call.message)
    def __getattr__(self,name): return getattr(self._call,name)

def _command_matches(message, commands):
    if not getattr(message,"text",None): return False
    cmd=message.text.split()[0].split("@",1)[0].lstrip("/").lower()
    return cmd in {str(x).lstrip("/").lower() for x in commands}

def _rule_matches(message, rule):
    kwargs=rule
    if "commands" in kwargs and not _command_matches(message,kwargs["commands"]): return False
    if "content_types" in kwargs:
        allowed=set(kwargs["content_types"])
        if "document" in allowed and not getattr(message,"document",None): return False
        if "text" in allowed and not getattr(message,"text",None): return False
    if "func" in kwargs and kwargs["func"] and not kwargs["func"](message): return False
    return True

async def _dispatch_message(_, message):
    try:
        if not getattr(message,"from_user",None): return
        message=MessageProxy(message)
        key=(message.chat.id,message.from_user.id)
        pending=pending_next_steps.pop(key,None)
        if pending:
            await asyncio.to_thread(pending,message)
            return
        for rule,func in list(_message_rules):
            if _rule_matches(message,rule):
                await asyncio.to_thread(func,message)
    except Exception as e:
        report_exception(e,"message_dispatch",getattr(getattr(message,"from_user",None),"id",None),"MESSAGE")

async def _dispatch_callback(_, call):
    try:
        call=CallbackProxy(call)
        for predicate,func in list(_callback_rules):
            if predicate is None or predicate(call):
                await asyncio.to_thread(func,call)
    except Exception as e:
        report_exception(e,"callback_dispatch",getattr(getattr(call,"from_user",None),"id",None),"CALLBACK")

def register_handlers():
    pyro_client.add_handler(handlers.MessageHandler(_dispatch_message, filters.all))
    pyro_client.add_handler(handlers.CallbackQueryHandler(_dispatch_callback, filters.all))

# ============================================================
# USER-SPECIFIC BUTTON LAYOUTS
# ============================================================
COMMAND_BUTTONS_LAYOUT_USER_SPEC=[["📢 Updates Channel"],["📤 Upload File","📂 Check Files"],["⚡ Bot Speed","📊 Statistics"],["📞 Contact Owner"],["📦 Manual Install","🆘 Help"]]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC=[["📢 Updates Channel"],["📤 Upload File","📂 Check Files"],["⚡ Bot Speed","📊 Statistics"],["💳 Subscriptions","📢 Broadcast"],["🔒 Lock Bot","🟢 Running All Code"],["👑 Admin Panel","📞 Contact Owner"],["📢 Channel Add","🛠️ Manual Install"],["👥 User Management","⚙️ Settings"]]

# --- Security Functions ---
def check_code_security(file_path, file_type):
    """Check code for dangerous commands (lightweight version)"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Comprehensive dangerous patterns with regex
        dangerous_patterns = [
    # ======================
    # SYSTEM / OS COMMANDS
    # ======================
    r'\bos\b',
    r'\bos\.system\b',
    r'\bos\.(remove|unlink|walk|listdir|scandir|stat|popen|fork|exec|kill|spawn)\b',
    r'\bshutdown\b',
    r'\breboot\b',
    r'rm\s+-rf',
    r'format\s+c:',
    r'dd\s+if=',
    r'\bmkfs\b',
    r'\bfdisk\b',
    r'chmod\s+777',
    r'chmod\s+\+x',
    r'\bsys\.exit\b',
    r'\bsys\.argv\b',

    # ======================
    # BASIC SHELL COMMANDS
    # ======================
    r'\bls\b',
    r'\bcd\b',
    r'\bvps\b',
    r'\bkill\b',
    r'\bkillall\b',
    r'\bpkill\b',
    r'\bkill\s+-\d+',
    r'\bhalt\b',
    r'\bpoweroff\b',
    r'\binit\s+0',
    r'\binit\s+6',
    r'\btelinit\s+0',
    r'\btelinit\s+6',
    r'\bmv\b.*/dev/null',
    r'\bcat\s+>/dev/null',
    r'>\s*/dev/null',
    r'2>\s*&1',
    r'\b&\s*$',
    r'\bnohup\b',
    r'\bdisown\b',

    # ======================
    # FILE DELETION/DESTRUCTION
    # ======================
    r'rm\s+-rf\s+/',
    r'rm\s+-rf\s+~',
    r'rm\s+-rf\s+\.',
    r'rm\s+-rf\s+\*',
    r'rm\s+-rf\s+.*',
    r'\bdd\s+if=/dev/zero',
    r'\bdd\s+of=/dev/sda',
    r'\bmv\s+/dev/null',
    r'>\s+\.bash_history',
    r'>\s+\.zsh_history',
    r'echo\s+""\s+>',
    r'truncate\s+-s\s+0',
    r':>\s*',

    # ======================
    # REGULAR EXPRESSIONS (re) - Yeh add kiya
    # ======================
    r'\bre\b',
    r'\bre\.(compile|search|match|findall|finditer|sub|split|escape|fullmatch)\b',
    r'\bimport\s+re\b',
    r'\bfrom\s+re\s+import\b',
    r'\bregex\b',
    r'\bpattern\s*=\s*re\.compile',
    r're\.(I|IGNORECASE|M|MULTILINE|S|DOTALL|U|UNICODE|X|VERBOSE)',
    r'\.*\{.*,\}',
    r'\^.*\$',
    r'\[.*\]',
    r'\(.*\)',
    r'\?.*',
    r'\*.*',
    r'\+.*',

    # ======================
    # IMAGE/FILE MANIPULATION - Yeh add kiya
    # ======================
    r'image\.jpeg',
    r'image\.jpg',
    r'image\.png',
    r'image\.gif',
    r'image\.bmp',
    r'\.jpeg\b',
    r'\.jpg\b',
    r'\.png\b',
    r'\.gif\b',
    r'\.bmp\b',
    r'\.ico\b',
    r'\.svg\b',
    r'\.webp\b',
    r'\.tiff\b',
    r'\.tif\b',
    r'\.pdf\b',
    r'\.docx\b',
    r'\.doc\b',
    r'\.xlsx\b',
    r'\.xls\b',
    r'\.pptx\b',
    r'\.ppt\b',
    r'\.zip\b',
    r'\.tar\b',
    r'\.gz\b',
    r'\.7z\b',
    r'\.rar\b',
    r'\bPIL\b',
    r'\bImage\b',
    r'\bImage\.(open|save|new|fromarray|frombytes)\b',
    r'\bcv2\b',
    r'\bopencv\b',
    r'\bskimage\b',
    r'\bscikit-image\b',
    r'\bmatplotlib\.image\b',
    r'\bimread\b',
    r'\bimwrite\b',
    r'\bimshow\b',
    r'\bimsave\b',

    # ======================
    # CTYPES / DLL LOADING
    # ======================
    r'\bctypes\b',
    r'\bctypes\.(CDLL|WinDLL|PyDLL|cdll|windll|oledll|py_object|Structure|Union)\b',
    r'\bCDLL\b',
    r'\bWinDLL\b',
    r'\blibc\b',
    r'\bFILE_p\b',
    r'\blibc\.(system|exec|fork|kill|popen)\b',
    r'\bmemset\b',
    r'\bmemcpy\b',
    r'\bmprotect\b',
    r'\bmmap\b',
    r'\bVirtualAlloc\b',
    r'\bCreateProcess\b',
    r'\bLoadLibrary\b',
    r'\bGetProcAddress\b',

    # ======================
    # EXEC / SUBPROCESS
    # ======================
    r'\bsubprocess\b',
    r'\bsubprocess\.(Popen|call|run|check_output|getoutput|getstatusoutput)\b',
    r'\beval\b',
    r'\bexec\b',
    r'\bcompile\b',
    r'\b__import__\b',

    # ======================
    # FILE SYSTEM / DATA READ
    # ======================
    r'\bopen\s*\(',
    r'\bread\s*\(',
    r'\bpathlib\b',
    r'\bglob\b',
    r'\bshutil\b',
    r'\bshutil\.(rmtree|copytree|move|disk_usage)\b',
    r'\bzipfile\b',
    r'\btempfile\b',
    r'\bcPickle\b',
    r'\bshelve\b',
    r'\bsqlite3\b',
    r'\bpandas\.(read_csv|read_excel|read_json)\b',

    # ======================
    # ENV / SECRETS
    # ======================
    r'\bos\.environ\b',
    r'\bdotenv\b',
    r'\bload_dotenv\b',
    r'\bprintenv\b',
    r'\benv\b',
    r'\bgetpass\b',
    r'\bkeyring\b',
    r'\bconfigparser\b',
    r'\byaml\b',
    r'\bjson\.load\b',

    # ======================
    # NETWORK / DATA EXFIL
    # ======================
    r'\bsocket\b',
    r'\bsocket\.(socket|create_connection|gethostname|gethostbyname)\b',
    r'\brequests\b',
    r'\brequests\.(get|post|put|delete|head|request)\b',
    r'\burllib\b',
    r'\burllib2\b',
    r'\burllib3\b',
    r'\bhttp\.client\b',
    r'\bwebsocket\b',
    r'\basyncio\.open_connection\b',
    r'\bwget\b',
    r'\bcurl\b',
    r'\bdownload\b',
    r'\bftplib\b',
    r'\bsmtplib\b',
    r'\bpoplib\b',
    r'\bimaplib\b',
    r'\btelnetlib\b',

    # ======================
    # SSH / REMOTE ACCESS
    # ======================
    r'\bparamiko\b',
    r'\bscp\b',
    r'\bssh\b',
    r'\bsshlib\b',
    r'\bpexpect\b',
    r'\bfabric\b',

    # ======================
    # SYSTEM INFO LEAK
    # ======================
    r'\bpsutil\b',
    r'\bplatform\b',
    r'\bplatform\.(node|processor|machine|architecture|system|version)\b',
    r'\bcmdline\b',
    r'\bpid\b',
    r'/proc/',
    r'\bmem\b',
    r'\bcpu\b',
    r'\bhostname\b',
    r'\buname\b',
    r'\bwhoami\b',

    # ======================
    # PYTHON INTERNAL ABUSE
    # ======================
    r'\bglobals\b',
    r'\blocals\b',
    r'\bvars\b',
    r'\binspect\b',
    r'\bmarshal\b',
    r'\bpickle\b',
    r'\bimportlib\b',
    r'\b__builtins__\b',
    r'\b__import__\b',
    r'\b__loader__\b',
    r'\b__file__\b',
    r'\b__package__\b',
    r'\b__spec__\b',
    r'\b__code__\b',
    r'\b__dict__\b',
    r'\bgetattr\b',
    r'\bsetattr\b',
    r'\bdelattr\b',
    r'\bhasattr\b',
    r'\bcallable\b',

    # ======================
    # TELEGRAM / BOT CONTROL
    # ======================
    r'\btelebot\b',
    r'\btelebot\.types\b',
    r'\baiogram\b',
    r'\bpyrogram\b',
    r'\btelegram\.ext\b',
    r'\btelegram\.bot\b',

    # ======================
    # LINUX / SHELL / BACKDOOR
    # ======================
    r'/bin/sh',
    r'/bin/bash',
    r'/bin/zsh',
    r'/bin/dash',
    r'nc\s+-e',
    r'netcat',
    r'\bbase64\b',
    r'\becho\b.*\|',
    r'\bawk\b',
    r'\bsed\b',
    r'\bfind\b',
    r'\bxargs\b',
    r'\bcrontab\b',
    r'\bservice\b',
    r'\bsystemctl\b',
    r'\btop\b',
    r'\bps\b',
    r'\bhtop\b',
    r'\bifconfig\b',
    r'\bip\s+a',
    r'\bss\b',
    r'\blsof\b',
    r'\bnetstat\b',

    # ======================
    # SSH KEYS / USER DATA
    # ======================
    r'/etc/passwd',
    r'/etc/shadow',
    r'/etc/hosts',
    r'/etc/resolv.conf',
    r'\.ssh/',
    r'id_rsa',
    r'id_dsa',
    r'authorized_keys',
    r'known_hosts',
    r'\.bashrc',
    r'\.bash_profile',
    r'\.zshrc',
    r'\.profile',

    # ======================
    # DATABASE ACCESS
    # ======================
    r'\bsqlite3\b',
    r'\bmysql\b',
    r'\bmysql\.connector\b',
    r'\bpsycopg2\b',
    r'\bpymongo\b',
    r'\bredis\b',

    # ======================
    # CRYPTO / ENCRYPTION
    # ======================
    r'\bcrypt\b',
    r'\bhashlib\b',
    r'\bhmac\b',
    r'\bssl\b',
    r'\btls\b',
    r'\bCrypto\b',
    r'\bcryptography\b',

    # ======================
    # PROCESS CONTROL
    # ======================
    r'\bsignal\b',
    r'\bmultiprocessing\b',
    r'\bthreading\b',
    r'\bdaemon\b',
    r'\batexit\b',
    r'\bexit\b',
    r'\bquit\b',

    # ======================
    # GUI / SCREEN CAPTURE
    # ======================
    r'\bpyautogui\b',
    r'\bselenium\b',
    r'\bpyscreenshot\b',
    r'\bImageGrab\b',

    # ======================
    # KEYLOGGING / INPUT
    # ======================
    r'\bpynput\b',
    r'\bkeyboard\b',
    r'\bmouse\b',
    r'\bgetch\b',

    # ======================
    # MISC DANGEROUS
    # ======================
    r'\.name\b',
    r'\.__name__\b',
    r'\.__class__\b',
    r'\.__bases__\b',
    r'\.__subclasses__\b',
    r'\.__mro__\b',
    r'\.__dictitems__\b',
    r'\.__reduce__\b',
    r'\.__reduce_ex__\b',
    r'\.__getstate__\b',
    r'\.__setstate__\b',

    # ======================
    # WINDOWS SPECIFIC
    # ======================
    r'\bwin32api\b',
    r'\bwin32com\b',
    r'\bwin32con\b',
    r'\bwin32event\b',
    r'\bwin32file\b',
    r'\bwin32process\b',
    r'\bwin32security\b',
    r'\bwmi\b',
    r'\bregedit\b',
    r'\bregistry\b',
    r'\bGetAsyncKeyState\b',
    r'\bSetWindowsHookEx\b',
    r'\btaskkill\b',
    r'\btasklist\b',
    r'\bschtasks\b',

    # ======================
    # ANTI-DEBUG / ANTI-VM
    # ======================
    r'\bptrace\b',
    r'\bdebugger\b',
    r'\bisatty\b',
    r'\bwindbg\b',
    r'\bollydbg\b',

    # ======================
    # MEMORY MANIPULATION
    # ======================
    r'\bmmap\b',
    r'\bmprotect\b',
    r'\bbrk\b',
    r'\bsbrk\b',
    r'\bmalloc\b',
    r'\bfree\b',
    r'\brealloc\b',
    r'\bVirtualAlloc\b',
    r'\bVirtualProtect\b',
    r'\bVirtualFree\b',
    r'\bHeapAlloc\b',
    r'\bHeapFree\b',

    # ======================
    # CODE INJECTION
    # ======================
    r'\binject\b',
    r'\bpayload\b',
    r'\bshellcode\b',
    r'\bmetasploit\b',
    r'\bbackdoor\b',
    r'\brootkit\b',
    r'\btrojan\b',
    r'\bmalware\b',
    r'\bexploit\b',
    r'\bvirus\b',
    r'\bworm\b',

    # ======================
    # NETWORK SCANNING
    # ======================
    r'\bnmap\b',
    r'\bnping\b',
    r'\bscapy\b',
    r'\barp\b',
    r'\bping\b',
    r'\btraceroute\b',
    r'\broute\b',
    r'\bifconfig\b',
    r'\bipconfig\b',
    r'\bnetstat\b',
    r'\bss\b',

    # ======================
    # PRIVILEGE ESCALATION
    # ======================
    r'\bsudo\b',
    r'\bsu\b',
    r'\brunas\b',
    r'\bprivilege\b',
    r'\bescalation\b',
    r'\buac\b',
    r'\bbypassuac\b',

    # ======================
    # PERSISTENCE
    # ======================
    r'\bregistry\b',
    r'\bstartup\b',
    r'\bautostart\b',
    r'\bscheduled\s*task\b',
    r'\bcron\b',
    r'\bat\b',
    r'\binit\.d\b',
    r'\bsystemd\b',
    r'\blaunchd\b',
    r'\bplist\b',

    # ======================
    # MORE DESTRUCTIVE COMMANDS
    # ======================
    r'\bmv\s+.*\s+/dev/null',
    r'\b>+\s*.*\.log',
    r'\btar\s+.*--exclude',
    r'\bfuser\b',
    r'\bstrace\b',
    r'\bltrace\b',
    r'\bgdb\b',
    r'\bobjdump\b',
    r'\bstrings\b',
    r'\bhexdump\b',
    r'\bxxd\b',
    r'\bod\b',
    r'\bsize\b',
    r'\bnm\b',
    r'\breadelf\b',
    r'\bldd\b',
    r'\bfile\b',
    r'\bwhich\b',
    r'\bwhereis\b',
    r'\blocate\b',
    r'\bupdatedb\b',
    r'\bmake\b',
    r'\bgcc\b',
    r'\bg\+\+\b',
    r'\bclang\b',
    r'\bclang\+\+\b',
    r'\bpython\d*\s+-c',
    r'\bperl\s+-e',
    r'\bruby\s+-e',
    r'\bphp\s+-r',
    r'\blua\s+-e',
    r'\bnode\s+-e',
    r'\bwget\s+.*\|\s*sh',
    r'\bcurl\s+.*\|\s*sh',
    r'\bwget\s+.*\|\s*bash',
    r'\bcurl\s+.*\|\s*bash',
    r'\bchattr\s+\+i',
    r'\bchattr\s+-i',
    r'\bsetfacl\b',
    r'\bgetfacl\b',
    r'\bchown\s+.*:.*',
    r'\bchgrp\b',
    r'\busermod\b',
    r'\bgroupmod\b',
    r'\badduser\b',
    r'\baddgroup\b',
    r'\bdeluser\b',
    r'\bdelgroup\b',
    r'\bpasswd\b',
    r'\bvisudo\b',
    r'\bed\b',
    r'\bex\b',
    r'\bvi\b',
    r'\bvim\b',
    r'\bnano\b',
    r'\bemacs\b',
    r'\bpico\b',
    r'\bmicro\b',
    r'\bne\b',

    # ======================
    # ADDITIONAL SECURITY PATTERNS
    # ======================
    r'\b__import__\s*\(',
    r'\bgetattr\s*\(',
    r'\bsetattr\s*\(',
    r'\bdelattr\s*\(',
    r'\bhasattr\s*\(',
    r'\b__getattr__\b',
    r'\b__setattr__\b',
    r'\b__delattr__\b',
    r'\b__getattribute__\b',
    r'\b__call__\b',
    r'\b__enter__\b',
    r'\b__exit__\b',
    r'\b__new__\b',
    r'\b__init__\b',
    r'\b__del__\b',
    r'\b__repr__\b',
    r'\b__str__\b',
    r'\b__bytes__\b',
    r'\b__format__\b',
    r'\b__lt__\b',
    r'\b__le__\b',
    r'\b__eq__\b',
    r'\b__ne__\b',
    r'\b__gt__\b',
    r'\b__ge__\b',
    r'\b__hash__\b',
    r'\b__bool__\b',
    r'\b__getitem__\b',
    r'\b__setitem__\b',
    r'\b__delitem__\b',
    r'\b__iter__\b',
    r'\b__next__\b',
    r'\b__reversed__\b',
    r'\b__contains__\b',
    r'\b__len__\b',
    r'\b__length_hint__\b',
    r'\b__missing__\b',
    r'\b__copy__\b',
    r'\b__deepcopy__\b'
]
        
        found_patterns = []
        for pattern in dangerous_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                found_patterns.append(pattern)
        
        if found_patterns:
            logger.warning(f"🚨 Dangerous patterns detected in {file_path}: {found_patterns}")
            return False, f"Code contains dangerous commands: {', '.join(found_patterns[:5])}"  # Show first 5 only
        
        return True, "Code is safe"
    except Exception as e:
        logger.error(f"Error in security check: {e}")
        return False, f"Security check error: {str(e)}"

def scan_zip_security(zip_path):
    """Check ZIP contents for security (lightweight version)"""
    try:
        dangerous_patterns = [
    # ======================
    # SYSTEM / OS COMMANDS
    # ======================
    r'\bos\b',
    r'\bos\.system\b',
    r'\bos\.(remove|unlink|walk|listdir|scandir|stat|popen|fork|exec|kill|spawn)\b',
    r'\bshutdown\b',
    r'\breboot\b',
    r'rm\s+-rf',
    r'format\s+c:',
    r'dd\s+if=',
    r'\bmkfs\b',
    r'\bfdisk\b',
    r'chmod\s+777',
    r'chmod\s+\+x',
    r'\bsys\.exit\b',
    r'\bsys\.argv\b',

    # ======================
    # BASIC SHELL COMMANDS
    # ======================
    r'\bls\b',
    r'\bcd\b',
    r'\bvps\b',
    r'\bkill\b',
    r'\bkillall\b',
    r'\bpkill\b',
    r'\bkill\s+-\d+',
    r'\bhalt\b',
    r'\bpoweroff\b',
    r'\binit\s+0',
    r'\binit\s+6',
    r'\btelinit\s+0',
    r'\btelinit\s+6',
    r'\bmv\b.*/dev/null',
    r'\bcat\s+>/dev/null',
    r'>\s*/dev/null',
    r'2>\s*&1',
    r'\b&\s*$',
    r'\bnohup\b',
    r'\bdisown\b',

    # ======================
    # FILE DELETION/DESTRUCTION
    # ======================
    r'rm\s+-rf\s+/',
    r'rm\s+-rf\s+~',
    r'rm\s+-rf\s+\.',
    r'rm\s+-rf\s+\*',
    r'rm\s+-rf\s+.*',
    r'\bdd\s+if=/dev/zero',
    r'\bdd\s+of=/dev/sda',
    r'\bmv\s+/dev/null',
    r'>\s+\.bash_history',
    r'>\s+\.zsh_history',
    r'echo\s+""\s+>',
    r'truncate\s+-s\s+0',
    r':>\s*',

    # ======================
    # REGULAR EXPRESSIONS (re) - Yeh add kiya
    # ======================
    r'\bre\b',
    r'\bre\.(compile|search|match|findall|finditer|sub|split|escape|fullmatch)\b',
    r'\bimport\s+re\b',
    r'\bfrom\s+re\s+import\b',
    r'\bregex\b',
    r'\bpattern\s*=\s*re\.compile',
    r're\.(I|IGNORECASE|M|MULTILINE|S|DOTALL|U|UNICODE|X|VERBOSE)',
    r'\.*\{.*,\}',
    r'\^.*\$',
    r'\[.*\]',
    r'\(.*\)',
    r'\?.*',
    r'\*.*',
    r'\+.*',

    # ======================
    # IMAGE/FILE MANIPULATION - Yeh add kiya
    # ======================
    r'image\.jpeg',
    r'image\.jpg',
    r'image\.png',
    r'image\.gif',
    r'image\.bmp',
    r'\.jpeg\b',
    r'\.jpg\b',
    r'\.png\b',
    r'\.gif\b',
    r'\.bmp\b',
    r'\.ico\b',
    r'\.svg\b',
    r'\.webp\b',
    r'\.tiff\b',
    r'\.tif\b',
    r'\.pdf\b',
    r'\.docx\b',
    r'\.doc\b',
    r'\.xlsx\b',
    r'\.xls\b',
    r'\.pptx\b',
    r'\.ppt\b',
    r'\.zip\b',
    r'\.tar\b',
    r'\.gz\b',
    r'\.7z\b',
    r'\.rar\b',
    r'\bPIL\b',
    r'\bImage\b',
    r'\bImage\.(open|save|new|fromarray|frombytes)\b',
    r'\bcv2\b',
    r'\bopencv\b',
    r'\bskimage\b',
    r'\bscikit-image\b',
    r'\bmatplotlib\.image\b',
    r'\bimread\b',
    r'\bimwrite\b',
    r'\bimshow\b',
    r'\bimsave\b',

    # ======================
    # CTYPES / DLL LOADING
    # ======================
    r'\bctypes\b',
    r'\bctypes\.(CDLL|WinDLL|PyDLL|cdll|windll|oledll|py_object|Structure|Union)\b',
    r'\bCDLL\b',
    r'\bWinDLL\b',
    r'\blibc\b',
    r'\bFILE_p\b',
    r'\blibc\.(system|exec|fork|kill|popen)\b',
    r'\bmemset\b',
    r'\bmemcpy\b',
    r'\bmprotect\b',
    r'\bmmap\b',
    r'\bVirtualAlloc\b',
    r'\bCreateProcess\b',
    r'\bLoadLibrary\b',
    r'\bGetProcAddress\b',

    # ======================
    # EXEC / SUBPROCESS
    # ======================
    r'\bsubprocess\b',
    r'\bsubprocess\.(Popen|call|run|check_output|getoutput|getstatusoutput)\b',
    r'\beval\b',
    r'\bexec\b',
    r'\bcompile\b',
    r'\b__import__\b',

    # ======================
    # FILE SYSTEM / DATA READ
    # ======================
    r'\bopen\s*\(',
    r'\bread\s*\(',
    r'\bpathlib\b',
    r'\bglob\b',
    r'\bshutil\b',
    r'\bshutil\.(rmtree|copytree|move|disk_usage)\b',
    r'\bzipfile\b',
    r'\btempfile\b',
    r'\bcPickle\b',
    r'\bshelve\b',
    r'\bsqlite3\b',
    r'\bpandas\.(read_csv|read_excel|read_json)\b',

    # ======================
    # ENV / SECRETS
    # ======================
    r'\bos\.environ\b',
    r'\bdotenv\b',
    r'\bload_dotenv\b',
    r'\bprintenv\b',
    r'\benv\b',
    r'\bgetpass\b',
    r'\bkeyring\b',
    r'\bconfigparser\b',
    r'\byaml\b',
    r'\bjson\.load\b',

    # ======================
    # NETWORK / DATA EXFIL
    # ======================
    r'\bsocket\b',
    r'\bsocket\.(socket|create_connection|gethostname|gethostbyname)\b',
    r'\brequests\b',
    r'\brequests\.(get|post|put|delete|head|request)\b',
    r'\burllib\b',
    r'\burllib2\b',
    r'\burllib3\b',
    r'\bhttp\.client\b',
    r'\bwebsocket\b',
    r'\basyncio\.open_connection\b',
    r'\bwget\b',
    r'\bcurl\b',
    r'\bdownload\b',
    r'\bftplib\b',
    r'\bsmtplib\b',
    r'\bpoplib\b',
    r'\bimaplib\b',
    r'\btelnetlib\b',

    # ======================
    # SSH / REMOTE ACCESS
    # ======================
    r'\bparamiko\b',
    r'\bscp\b',
    r'\bssh\b',
    r'\bsshlib\b',
    r'\bpexpect\b',
    r'\bfabric\b',

    # ======================
    # SYSTEM INFO LEAK
    # ======================
    r'\bpsutil\b',
    r'\bplatform\b',
    r'\bplatform\.(node|processor|machine|architecture|system|version)\b',
    r'\bcmdline\b',
    r'\bpid\b',
    r'/proc/',
    r'\bmem\b',
    r'\bcpu\b',
    r'\bhostname\b',
    r'\buname\b',
    r'\bwhoami\b',

    # ======================
    # PYTHON INTERNAL ABUSE
    # ======================
    r'\bglobals\b',
    r'\blocals\b',
    r'\bvars\b',
    r'\binspect\b',
    r'\bmarshal\b',
    r'\bpickle\b',
    r'\bimportlib\b',
    r'\b__builtins__\b',
    r'\b__import__\b',
    r'\b__loader__\b',
    r'\b__file__\b',
    r'\b__package__\b',
    r'\b__spec__\b',
    r'\b__code__\b',
    r'\b__dict__\b',
    r'\bgetattr\b',
    r'\bsetattr\b',
    r'\bdelattr\b',
    r'\bhasattr\b',
    r'\bcallable\b',

    # ======================
    # TELEGRAM / BOT CONTROL
    # ======================
    r'\btelebot\b',
    r'\btelebot\.types\b',
    r'\baiogram\b',
    r'\bpyrogram\b',
    r'\btelegram\.ext\b',
    r'\btelegram\.bot\b',

    # ======================
    # LINUX / SHELL / BACKDOOR
    # ======================
    r'/bin/sh',
    r'/bin/bash',
    r'/bin/zsh',
    r'/bin/dash',
    r'nc\s+-e',
    r'netcat',
    r'\bbase64\b',
    r'\becho\b.*\|',
    r'\bawk\b',
    r'\bsed\b',
    r'\bfind\b',
    r'\bxargs\b',
    r'\bcrontab\b',
    r'\bservice\b',
    r'\bsystemctl\b',
    r'\btop\b',
    r'\bps\b',
    r'\bhtop\b',
    r'\bifconfig\b',
    r'\bip\s+a',
    r'\bss\b',
    r'\blsof\b',
    r'\bnetstat\b',

    # ======================
    # SSH KEYS / USER DATA
    # ======================
    r'/etc/passwd',
    r'/etc/shadow',
    r'/etc/hosts',
    r'/etc/resolv.conf',
    r'\.ssh/',
    r'id_rsa',
    r'id_dsa',
    r'authorized_keys',
    r'known_hosts',
    r'\.bashrc',
    r'\.bash_profile',
    r'\.zshrc',
    r'\.profile',

    # ======================
    # DATABASE ACCESS
    # ======================
    r'\bsqlite3\b',
    r'\bmysql\b',
    r'\bmysql\.connector\b',
    r'\bpsycopg2\b',
    r'\bpymongo\b',
    r'\bredis\b',

    # ======================
    # CRYPTO / ENCRYPTION
    # ======================
    r'\bcrypt\b',
    r'\bhashlib\b',
    r'\bhmac\b',
    r'\bssl\b',
    r'\btls\b',
    r'\bCrypto\b',
    r'\bcryptography\b',

    # ======================
    # PROCESS CONTROL
    # ======================
    r'\bsignal\b',
    r'\bmultiprocessing\b',
    r'\bthreading\b',
    r'\bdaemon\b',
    r'\batexit\b',
    r'\bexit\b',
    r'\bquit\b',

    # ======================
    # GUI / SCREEN CAPTURE
    # ======================
    r'\bpyautogui\b',
    r'\bselenium\b',
    r'\bpyscreenshot\b',
    r'\bImageGrab\b',

    # ======================
    # KEYLOGGING / INPUT
    # ======================
    r'\bpynput\b',
    r'\bkeyboard\b',
    r'\bmouse\b',
    r'\bgetch\b',

    # ======================
    # MISC DANGEROUS
    # ======================
    r'\.name\b',
    r'\.__name__\b',
    r'\.__class__\b',
    r'\.__bases__\b',
    r'\.__subclasses__\b',
    r'\.__mro__\b',
    r'\.__dictitems__\b',
    r'\.__reduce__\b',
    r'\.__reduce_ex__\b',
    r'\.__getstate__\b',
    r'\.__setstate__\b',

    # ======================
    # WINDOWS SPECIFIC
    # ======================
    r'\bwin32api\b',
    r'\bwin32com\b',
    r'\bwin32con\b',
    r'\bwin32event\b',
    r'\bwin32file\b',
    r'\bwin32process\b',
    r'\bwin32security\b',
    r'\bwmi\b',
    r'\bregedit\b',
    r'\bregistry\b',
    r'\bGetAsyncKeyState\b',
    r'\bSetWindowsHookEx\b',
    r'\btaskkill\b',
    r'\btasklist\b',
    r'\bschtasks\b',

    # ======================
    # ANTI-DEBUG / ANTI-VM
    # ======================
    r'\bptrace\b',
    r'\bdebugger\b',
    r'\bisatty\b',
    r'\bwindbg\b',
    r'\bollydbg\b',

    # ======================
    # MEMORY MANIPULATION
    # ======================
    r'\bmmap\b',
    r'\bmprotect\b',
    r'\bbrk\b',
    r'\bsbrk\b',
    r'\bmalloc\b',
    r'\bfree\b',
    r'\brealloc\b',
    r'\bVirtualAlloc\b',
    r'\bVirtualProtect\b',
    r'\bVirtualFree\b',
    r'\bHeapAlloc\b',
    r'\bHeapFree\b',

    # ======================
    # CODE INJECTION
    # ======================
    r'\binject\b',
    r'\bpayload\b',
    r'\bshellcode\b',
    r'\bmetasploit\b',
    r'\bbackdoor\b',
    r'\brootkit\b',
    r'\btrojan\b',
    r'\bmalware\b',
    r'\bexploit\b',
    r'\bvirus\b',
    r'\bworm\b',

    # ======================
    # NETWORK SCANNING
    # ======================
    r'\bnmap\b',
    r'\bnping\b',
    r'\bscapy\b',
    r'\barp\b',
    r'\bping\b',
    r'\btraceroute\b',
    r'\broute\b',
    r'\bifconfig\b',
    r'\bipconfig\b',
    r'\bnetstat\b',
    r'\bss\b',

    # ======================
    # PRIVILEGE ESCALATION
    # ======================
    r'\bsudo\b',
    r'\bsu\b',
    r'\brunas\b',
    r'\bprivilege\b',
    r'\bescalation\b',
    r'\buac\b',
    r'\bbypassuac\b',

    # ======================
    # PERSISTENCE
    # ======================
    r'\bregistry\b',
    r'\bstartup\b',
    r'\bautostart\b',
    r'\bscheduled\s*task\b',
    r'\bcron\b',
    r'\bat\b',
    r'\binit\.d\b',
    r'\bsystemd\b',
    r'\blaunchd\b',
    r'\bplist\b',

    # ======================
    # MORE DESTRUCTIVE COMMANDS
    # ======================
    r'\bmv\s+.*\s+/dev/null',
    r'\b>+\s*.*\.log',
    r'\btar\s+.*--exclude',
    r'\bfuser\b',
    r'\bstrace\b',
    r'\bltrace\b',
    r'\bgdb\b',
    r'\bobjdump\b',
    r'\bstrings\b',
    r'\bhexdump\b',
    r'\bxxd\b',
    r'\bod\b',
    r'\bsize\b',
    r'\bnm\b',
    r'\breadelf\b',
    r'\bldd\b',
    r'\bfile\b',
    r'\bwhich\b',
    r'\bwhereis\b',
    r'\blocate\b',
    r'\bupdatedb\b',
    r'\bmake\b',
    r'\bgcc\b',
    r'\bg\+\+\b',
    r'\bclang\b',
    r'\bclang\+\+\b',
    r'\bpython\d*\s+-c',
    r'\bperl\s+-e',
    r'\bruby\s+-e',
    r'\bphp\s+-r',
    r'\blua\s+-e',
    r'\bnode\s+-e',
    r'\bwget\s+.*\|\s*sh',
    r'\bcurl\s+.*\|\s*sh',
    r'\bwget\s+.*\|\s*bash',
    r'\bcurl\s+.*\|\s*bash',
    r'\bchattr\s+\+i',
    r'\bchattr\s+-i',
    r'\bsetfacl\b',
    r'\bgetfacl\b',
    r'\bchown\s+.*:.*',
    r'\bchgrp\b',
    r'\busermod\b',
    r'\bgroupmod\b',
    r'\badduser\b',
    r'\baddgroup\b',
    r'\bdeluser\b',
    r'\bdelgroup\b',
    r'\bpasswd\b',
    r'\bvisudo\b',
    r'\bed\b',
    r'\bex\b',
    r'\bvi\b',
    r'\bvim\b',
    r'\bnano\b',
    r'\bemacs\b',
    r'\bpico\b',
    r'\bmicro\b',
    r'\bne\b',

    # ======================
    # ADDITIONAL SECURITY PATTERNS
    # ======================
    r'\b__import__\s*\(',
    r'\bgetattr\s*\(',
    r'\bsetattr\s*\(',
    r'\bdelattr\s*\(',
    r'\bhasattr\s*\(',
    r'\b__getattr__\b',
    r'\b__setattr__\b',
    r'\b__delattr__\b',
    r'\b__getattribute__\b',
    r'\b__call__\b',
    r'\b__enter__\b',
    r'\b__exit__\b',
    r'\b__new__\b',
    r'\b__init__\b',
    r'\b__del__\b',
    r'\b__repr__\b',
    r'\b__str__\b',
    r'\b__bytes__\b',
    r'\b__format__\b',
    r'\b__lt__\b',
    r'\b__le__\b',
    r'\b__eq__\b',
    r'\b__ne__\b',
    r'\b__gt__\b',
    r'\b__ge__\b',
    r'\b__hash__\b',
    r'\b__bool__\b',
    r'\b__getitem__\b',
    r'\b__setitem__\b',
    r'\b__delitem__\b',
    r'\b__iter__\b',
    r'\b__next__\b',
    r'\b__reversed__\b',
    r'\b__contains__\b',
    r'\b__len__\b',
    r'\b__length_hint__\b',
    r'\b__missing__\b',
    r'\b__copy__\b',
    r'\b__deepcopy__\b'
]
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith(('.py', '.js', '.zip', '.txt', '.sh', '.bat', '.cmd')):
                    with zip_ref.open(file_info.filename) as f:
                        try:
                            content = f.read().decode('utf-8', errors='ignore')
                        except Exception:
                            continue
                        
                        for pattern in dangerous_patterns:
                            if re.search(pattern, content, re.IGNORECASE):
                                return False, f"File {file_info.filename} contains dangerous command: {pattern}"
        return True, "Archive is safe"
    except Exception as e:
        return False, f"Error scanning archive: {str(e)}"

# --- Mandatory Channels Functions ---
def is_user_member(user_id, channel_id):
    """Check if user is member of a channel"""
    try:
        chat_member = bot.get_chat_member(channel_id, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking channel membership for {user_id} in {channel_id}: {e}")
        return False

def check_mandatory_subscription(user_id):
    """Check if user is subscribed to all mandatory channels"""
    if not mandatory_channels:
        return True, []  # No mandatory channels exist
    
    not_joined = []
    for channel_id, channel_info in mandatory_channels.items():
        if not is_user_member(user_id, channel_id):
            not_joined.append((channel_id, channel_info))
    
    if not_joined:
        return False, not_joined
    return True, []

def save_mandatory_channel(channel_id, channel_username, channel_name, added_by):
    try:
        doc={"channel_id":str(channel_id),"channel_username":channel_username,"channel_name":channel_name,"added_by":added_by,"added_date":datetime.utcnow().isoformat()}
        mongo.call(self_db.mandatory_channels.update_one({"channel_id":str(channel_id)},{"$set":doc},upsert=True))
        mandatory_channels[str(channel_id)]={"username":channel_username,"name":channel_name}; return True
    except Exception as e: report_exception(e,"save_mandatory_channel",added_by,"CHANNEL_ADMIN"); return False

def remove_mandatory_channel_db(channel_id):
    try:
        mongo.call(self_db.mandatory_channels.delete_one({"channel_id":str(channel_id)})); mandatory_channels.pop(str(channel_id),None); return True
    except Exception as e: report_exception(e,"remove_mandatory_channel_db",None,"CHANNEL_ADMIN"); return False

def create_mandatory_channels_menu():
    """Create mandatory channels management menu"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Channel', callback_data='add_mandatory_channel'),
        types.InlineKeyboardButton('➖ Remove Channel', callback_data='remove_mandatory_channel')
    )
    markup.row(types.InlineKeyboardButton('📋 List Channels', callback_data='list_mandatory_channels'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_check_message(not_joined_channels):
    """Create subscription verification message"""
    message = "📢 **Important: Join Our Channels First:**\n\n"
    
    markup = types.InlineKeyboardMarkup()
    
    for channel_id, channel_info in not_joined_channels:
        channel_username = channel_info.get('username', '')
        channel_name = channel_info.get('name', 'Channel')
        
        if channel_username:
            channel_link = f"https://t.me/{channel_username.replace('@', '')}"
        else:
            channel_link = f"https://t.me/c/{channel_id.replace('-100', '')}"
        
        message += f"• {channel_name}\n"
        markup.add(types.InlineKeyboardButton(f"Join {channel_name}", url=channel_link))
    
    markup.add(types.InlineKeyboardButton("✅ Verify Subscription", callback_data='check_subscription_status'))
    
    return message, markup

# --- User management helpers (MongoDB) ---
def is_user_banned(user_id):
    return user_id in banned_users

def ban_user_db(user_id, reason, banned_by):
    try:
        mongo.call(self_db.banned_users.update_one({"user_id":user_id},{"$set":{"user_id":user_id,"reason":reason,"banned_by":banned_by,"ban_date":datetime.utcnow().isoformat()}},upsert=True))
        banned_users.add(user_id)
        send_log(f"Reason: {reason}","WARNING",user_id,"USER_BAN")
        return True
    except Exception as e:
        report_exception(e,"ban_user_db",user_id,"USER_BAN"); return False

def unban_user_db(user_id):
    try:
        mongo.call(self_db.banned_users.delete_one({"user_id":user_id}))
        banned_users.discard(user_id)
        send_log("Ban removed","INFO",user_id,"USER_UNBAN")
        return True
    except Exception as e:
        report_exception(e,"unban_user_db",user_id,"USER_UNBAN"); return False

def set_user_limit_db(user_id, limit, set_by):
    try:
        mongo.call(self_db.user_limits.update_one({"user_id":user_id},{"$set":{"user_id":user_id,"file_limit":limit,"set_by":set_by,"set_date":datetime.utcnow().isoformat()}},upsert=True))
        user_limits[user_id]=limit
        return True
    except Exception as e:
        report_exception(e,"set_user_limit_db",user_id,"USER_LIMIT"); return False

def remove_user_limit_db(user_id):
    try:
        mongo.call(self_db.user_limits.delete_one({"user_id":user_id}))
        user_limits.pop(user_id,None)
        return True
    except Exception as e:
        report_exception(e,"remove_user_limit_db",user_id,"USER_LIMIT"); return False

# --- Modified Helper Functions ---
def get_user_folder(user_id):
    """Get or create user's folder for storing files"""
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    """Get the file upload limit for a user"""
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_limits: return user_limits[user_id]
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    """Get the number of files uploaded by a user"""
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    """Check if a bot script is currently running for a specific user"""
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                logger.warning(f"Process {script_info['process'].pid} for {script_key} found in memory but not running/zombie. Cleaning up.")
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try:
                        script_info['log_file'].close()
                    except Exception as log_e:
                        logger.error(f"Error closing log file during zombie cleanup {script_key}: {log_e}")
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            logger.warning(f"Process for {script_key} not found (NoSuchProcess). Cleaning up.")
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try:
                     script_info['log_file'].close()
                except Exception as log_e:
                     logger.error(f"Error closing log file during cleanup of non-existent process {script_key}: {log_e}")
            if script_key in bot_scripts:
                 del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process status for {script_key}: {e}", exc_info=True)
            return False
    return False

def kill_process_tree(process_info):
    """Kill a process and all its children, ensuring log file is closed."""
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A') 

    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"Closed log file for {script_key} (PID: {process_info.get('process', {}).get('pid', 'N/A')})")
            except Exception as log_e:
                logger.error(f"Error closing log file during kill for {script_key}: {log_e}")

        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
           pid = process.pid
           if pid: 
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    logger.info(f"Attempting to kill process tree for {script_key} (PID: {pid}, Children: {[c.pid for c in children]})")

                    for child in children:
                        try:
                            child.terminate()
                            logger.info(f"Terminated child process {child.pid} for {script_key}")
                        except psutil.NoSuchProcess:
                            logger.warning(f"Child process {child.pid} for {script_key} already gone.")
                        except Exception as e:
                            logger.error(f"Error terminating child {child.pid} for {script_key}: {e}. Trying kill...")
                            try: child.kill(); logger.info(f"Killed child process {child.pid} for {script_key}")
                            except Exception as e2: logger.error(f"Failed to kill child {child.pid} for {script_key}: {e2}")

                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        logger.warning(f"Child process {p.pid} for {script_key} still alive. Killing.")
                        try: p.kill()
                        except Exception as e: logger.error(f"Failed to kill child {p.pid} for {script_key} after wait: {e}")

                    try:
                        parent.terminate()
                        logger.info(f"Terminated parent process {pid} for {script_key}")
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            logger.warning(f"Parent process {pid} for {script_key} did not terminate. Killing.")
                            parent.kill()
                            logger.info(f"Killed parent process {pid} for {script_key}")
                    except psutil.NoSuchProcess:
                        logger.warning(f"Parent process {pid} for {script_key} already gone.")
                    except Exception as e:
                        logger.error(f"Error terminating parent {pid} for {script_key}: {e}. Trying kill...")
                        try: parent.kill(); logger.info(f"Killed parent process {pid} for {script_key}")
                        except Exception as e2: logger.error(f"Failed to kill parent {pid} for {script_key}: {e2}")

                except psutil.NoSuchProcess:
                    logger.warning(f"Process {pid or 'N/A'} for {script_key} not found during kill. Already terminated?")
           else: logger.error(f"Process PID is None for {script_key}.")
        elif log_file_closed: logger.warning(f"Process object missing for {script_key}, but log file closed.")
        else: logger.error(f"Process object missing for {script_key}, and no log file. Cannot kill.")
    except Exception as e:
        logger.error(f"❌ Unexpected error killing process tree for PID {pid or 'N/A'} ({script_key}): {e}", exc_info=True)

# --- Map Telegram import names to actual PyPI package names ---
TELEGRAM_MODULES = {
    # Main Bot Frameworks
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon', # Handle specific imports
    'from telethon.sync import telegramclient': 'telethon', # Example

    # Additional Libraries (add more specific mappings if import name differs)
    'telepot': 'telepot',
    'pytg': 'pytg',
    'tgcrypto': 'tgcrypto',
    'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send',
    'telegram_text': 'telegram-text',

    # MTProto & Low-Level
    'mtproto': 'telegram-mtproto', # Example, check actual package name
    'tl': 'telethon',  # Part of Telethon, install 'telethon'

    # Utilities & Helpers (examples, verify package names)
    'telegram_utils': 'telegram-utils',
    'telegram_logger': 'telegram-logger',
    'telegram_handlers': 'python-telegram-handlers',

    # Database Integrations (examples)
    'telegram_redis': 'telegram-redis',
    'telegram_sqlalchemy': 'telegram-sqlalchemy',

    # Payment & E-commerce (examples)
    'telegram_payment': 'telegram-payment',
    'telegram_shop': 'telegram-shop-sdk',

    # Testing & Debugging (examples)
    'pytest_telegram': 'pytest-telegram',
    'telegram_debug': 'telegram-debug',

    # Scraping & Analytics (examples)
    'telegram_scraper': 'telegram-scraper',
    'telegram_analytics': 'telegram-analytics',

    # NLP & AI (examples)
    'telegram_nlp': 'telegram-nlp-toogit',
    'telegram_ai': 'telegram-ai', # Assuming this exists

    # Web & API Integration (examples)
    'telegram_api': 'telegram-api-client',
    'telegram_web': 'telegram-web-integration',

    # Gaming & Interactive (examples)
    'telegram_games': 'telegram-games',
    'telegram_quiz': 'telegram-quiz-bot',

    # File & Media Handling (examples)
    'telegram_ffmpeg': 'telegram-ffmpeg',
    'telegram_media': 'telegram-media-utils',

    # Security & Encryption (examples)
    'telegram_2fa': 'telegram-twofa',
    'telegram_crypto': 'telegram-crypto-bot',

    # Localization & i18n (examples)
    'telegram_i18n': 'telegram-i18n',
    'telegram_translate': 'telegram-translate',

    # Common non-telegram examples
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow', # Note the capitalization difference
    'cv2': 'opencv-python', # Common import name for OpenCV
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'asyncio': None, # Core module, should not be installed
    'json': None,    # Core module
    'datetime': None,# Core module
    'os': None,      # Core module
    'sys': None,     # Core module
    're': None,      # Core module
    'time': None,    # Core module
    'math': None,    # Core module
    'random': None,  # Core module
    'logging': None, # Core module
    'threading': None,# Core module
    'subprocess':None,# Core module
    'zipfile':None,  # Core module
    'tempfile':None, # Core module
    'shutil':None,   # Core module
    'sqlite3':None,  # Core module
    'psutil': 'psutil',
    'atexit': None   # Core module
}

# --- Manual Modules Installation System ---
def save_install_log(user_id, module_name, package_name, status, log):
    try:
        mongo.call(self_db.install_logs.insert_one({
            "user_id":user_id,"module_name":module_name,"package_name":package_name,
            "status":status,"log":log[-12000:],"install_date":datetime.utcnow().isoformat()
        }))
    except Exception as e: report_exception(e,"save_install_log",user_id,"INSTALL_LOG")

def attempt_install_pip(module_name, message, manual_request=False):
    """Install Python package via pip"""
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name) 
    if package_name is None: 
        logger.info(f"Module '{module_name}' is core. Skipping pip install.")
        return False, "Core module - no installation needed"
    
    try:
        if manual_request:
            bot.reply_to(message, f"🔄 Manual installation requested for `{module_name}` -> `{package_name}`...", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"🐍 Module `{module_name}` not found. Installing `{package_name}`...", parse_mode='Markdown')
        
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        logger.info(f"Running install: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0:
            log_msg = f"Installed {package_name}. Output:\n{result.stdout}"
            logger.info(log_msg)
            success_msg = f"✅ Package `{package_name}` (for `{module_name}`) installed successfully."
            bot.reply_to(message, success_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, package_name, "success", log_msg)
            return True, log_msg
        else:
            error_msg = f"❌ Failed to install `{package_name}` for `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, package_name, "failed", error_msg)
            return False, error_msg
    except Exception as e:
        error_msg = f"❌ Error installing `{package_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        save_install_log(message.from_user.id, module_name, package_name, "error", error_msg)
        return False, error_msg

def attempt_install_npm(module_name, user_folder, message, manual_request=False):
    """Install Node package via npm"""
    try:
        if manual_request:
            bot.reply_to(message, f"🔄 Manual Node package installation requested for `{module_name}`...", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"🟠 Node package `{module_name}` not found. Installing locally...", parse_mode='Markdown')
        
        command = ['npm', 'install', module_name]
        logger.info(f"Running npm install: {' '.join(command)} in {user_folder}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=user_folder, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0:
            log_msg = f"Installed {module_name}. Output:\n{result.stdout}"
            logger.info(log_msg)
            success_msg = f"✅ Node package `{module_name}` installed locally."
            bot.reply_to(message, success_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, module_name, "success", log_msg)
            return True, log_msg
        else:
            error_msg = f"❌ Failed to install Node package `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, module_name, "failed", error_msg)
            return False, error_msg
    except FileNotFoundError:
         error_msg = "❌ Error: 'npm' not found. Ensure Node.js/npm are installed and in PATH."
         logger.error(error_msg)
         bot.reply_to(message, error_msg)
         save_install_log(message.from_user.id, module_name, module_name, "error", error_msg)
         return False, error_msg
    except Exception as e:
        error_msg = f"❌ Error installing Node package `{module_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        save_install_log(message.from_user.id, module_name, module_name, "error", error_msg)
        return False, error_msg

def manual_install_module_init(message):
    """Initialize manual module installation"""
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin. Try later.")
        return
    
    msg = bot.reply_to(message, "📦 Send module name to install (e.g., `requests` or `pillow`)\nFor Node.js: `npm:module_name`\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_manual_install_module)

def process_manual_install_module(message):
    """Process manual module installation"""
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Installation cancelled.")
        return
    
    module_name = message.text.strip()
    
    # Check if it's a Node.js module
    if module_name.lower().startswith('npm:'):
        module_name = module_name[4:].strip()
        user_folder = get_user_folder(user_id)
        success, log = attempt_install_npm(module_name, user_folder, message, manual_request=True)
    else:
        # Python module
        success, log = attempt_install_pip(module_name, message, manual_request=True)
    
    if success:
        logger.info(f"User {user_id} manually installed module: {module_name}")

# --- MongoDB Operations ---
def save_user_file(user_id,file_name,file_type="py"):
    try:
        now=datetime.utcnow().isoformat()
        mongo.call(self_db.files.update_one({"file_id":f"{user_id}:{file_name}"},{"$set":{"user_id":user_id,"file_name":file_name,"file_type":file_type,"updated_at":now},"$setOnInsert":{"file_id":f"{user_id}:{file_name}","uploaded_at":now}},upsert=True))
        user_files[user_id]=[(fn,ft) for fn,ft in user_files.get(user_id,[]) if fn!=file_name]+[(file_name,file_type)]
        send_log(f"File: {file_name}\nType: {file_type}","INFO",user_id,"FILE_RECORD")
    except Exception as e: report_exception(e,"save_user_file",user_id,"FILE_DB")

def remove_user_file_db(user_id,file_name):
    try:
        mongo.call(self_db.files.delete_one({"file_id":f"{user_id}:{file_name}"}))
        mongo.call(self_db.hosted_bots.delete_one({"user_id":user_id,"file_name":file_name}))
        if user_id in user_files:
            user_files[user_id]=[f for f in user_files[user_id] if f[0]!=file_name]
            if not user_files[user_id]: user_files.pop(user_id,None)
    except Exception as e: report_exception(e,"remove_user_file_db",user_id,"FILE_DB")

def add_active_user(user_id):
    first=user_id not in active_users
    active_users.add(user_id)
    try:
        now=datetime.utcnow().isoformat()
        mongo.call(self_db.users.update_one({"user_id":user_id},{"$set":{"last_seen":now},"$setOnInsert":{"user_id":user_id,"created_at":now,"banned":False}},upsert=True))
        if first: send_log(f"First seen user: {user_id}","INFO",user_id,"NEW_USER")
    except Exception as e: report_exception(e,"add_active_user",user_id,"USER")

def save_subscription(user_id,expiry):
    try:
        mongo.call(self_db.subscriptions.update_one({"user_id":user_id},{"$set":{"user_id":user_id,"expiry":expiry.isoformat()}},upsert=True))
        user_subscriptions[user_id]={"expiry":expiry}
        send_log(f"Expiry: {expiry.isoformat()}","INFO",user_id,"SUBSCRIPTION_CHANGE")
    except Exception as e: report_exception(e,"save_subscription",user_id,"SUBSCRIPTION")

def remove_subscription_db(user_id):
    try:
        mongo.call(self_db.subscriptions.delete_one({"user_id":user_id})); user_subscriptions.pop(user_id,None)
    except Exception as e: report_exception(e,"remove_subscription_db",user_id,"SUBSCRIPTION")

def add_admin_db(admin_id,added_by):
    try:
        mongo.call(self_db.admins.update_one({"user_id":admin_id},{"$setOnInsert":{"user_id":admin_id,"added_by":added_by,"added_date":datetime.utcnow().isoformat()}},upsert=True)); admin_ids.add(admin_id)
        send_log(f"Admin added by {added_by}","INFO",admin_id,"ADMIN_ACTION")
    except Exception as e: report_exception(e,"add_admin_db",admin_id,"ADMIN_ACTION")

def remove_admin_db(admin_id):
    if admin_id==OWNER_ID: return False
    try:
        r=mongo.call(self_db.admins.delete_one({"user_id":admin_id})); admin_ids.discard(admin_id); return r.deleted_count>0
    except Exception as e: report_exception(e,"remove_admin_db",admin_id,"ADMIN_ACTION"); return False

# --- Menu creation (Inline and ReplyKeyboards) ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=f'https://t.me/{UPDATE_CHANNEL.replace("@", "")}'),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📦 Manual Install', callback_data='manual_install'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]

    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'), #0
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'), #1
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot', #2
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'), #3
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'), #4
            types.InlineKeyboardButton('🟢 Run All Scripts', callback_data='run_all_scripts'), #5
            types.InlineKeyboardButton('📢 Channel Add', callback_data='manage_mandatory_channels'), #6
            types.InlineKeyboardButton('👥 User Management', callback_data='user_management'), #7
            types.InlineKeyboardButton('🛠️ Admin Install', callback_data='admin_install'), #8
            types.InlineKeyboardButton('⚙️ Settings', callback_data='admin_settings') #9
        ]
        markup.add(buttons[0]) # Updates
        markup.add(buttons[1], buttons[2]) # Upload, Check Files
        markup.add(buttons[3], admin_buttons[0]) # Speed, Subscriptions
        markup.add(admin_buttons[1], admin_buttons[3]) # Stats, Broadcast
        markup.add(admin_buttons[2], admin_buttons[5]) # Lock Bot, Run All Scripts
        markup.add(admin_buttons[6], admin_buttons[8]) # Channel Management, Admin Install
        markup.add(admin_buttons[7], admin_buttons[9]) # User Management, Settings
        markup.add(admin_buttons[4]) # Admin Panel
        markup.add(buttons[5]) # Contact
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], buttons[4]) # Speed, Manual Install
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats')) # Allow non-admins to see stats too
        markup.add(buttons[5])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row_buttons_text in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row_buttons_text])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_user_management_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('🚫 Ban User', callback_data='ban_user'),
        types.InlineKeyboardButton('✅ Unban User', callback_data='unban_user')
    )
    markup.row(
        types.InlineKeyboardButton('📊 User Info', callback_data='user_info'),
        types.InlineKeyboardButton('👥 All Users', callback_data='all_users')
    )
    markup.row(
        types.InlineKeyboardButton('🔧 Set User Limit', callback_data='set_user_limit'),
        types.InlineKeyboardButton('🗑️ Remove User Limit', callback_data='remove_user_limit')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_admin_settings_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('📊 System Info', callback_data='system_info'),
        types.InlineKeyboardButton('📈 Bot Performance', callback_data='bot_performance')
    )
    markup.row(
        types.InlineKeyboardButton('🧹 Cleanup Files', callback_data='cleanup_files'),
        types.InlineKeyboardButton('📋 Installation Logs', callback_data='install_logs')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# --- File Handling ---
def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None 
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        logger.info(f"Temp dir for zip: {temp_dir}")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as new_file: new_file.write(downloaded_file_content)
        
        # Security check for ZIP
        is_safe, security_msg = scan_zip_security(zip_path)
        if not is_safe:
            # Send security warning to admin for approval
            security_warning_msg = f"🚨 File needs approval:\n👤 User: {user_id}\n📁 File: {file_name_zip}\n⚠️ Reason: {security_msg}"
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_zip_{user_id}_{file_name_zip}"),
                types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_zip_{user_id}_{file_name_zip}")
            )
            for admin_id in admin_ids:
                try:
                    bot.send_message(admin_id, security_warning_msg, reply_markup=markup)
                except Exception as e:
                    logger.error(f"Failed to send security warning to admin {admin_id}: {e}")
            
            # Store the file content for later approval
            if user_id not in pending_zip_files:
                pending_zip_files[user_id] = {}
            pending_zip_files[user_id][file_name_zip] = downloaded_file_content
            
            bot.reply_to(message, f"⏳ File under security review. You will be notified upon approval.")
            return

        # Process ZIP file if safe
        process_zip_file(zip_path, user_id, user_folder, file_name_zip, message, temp_dir)
        
    except zipfile.BadZipFile as e:
        logger.error(f"Bad zip file from {user_id}: {e}")
        bot.reply_to(message, f"❌ Error: Invalid/corrupted ZIP. {e}")
    except Exception as e:
        logger.error(f"❌ Error processing zip for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir); logger.info(f"Cleaned temp dir: {temp_dir}")
            except Exception as e: logger.error(f"Failed to clean temp dir {temp_dir}: {e}", exc_info=True)

def process_zip_file(zip_path, user_id, user_folder, file_name_zip, message, temp_dir=None):
    """Process ZIP file extraction and setup"""
    cleanup_temp = False
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        cleanup_temp = True
        
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Check for safe paths
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Zip has unsafe path: {member.filename}")
            zip_ref.extractall(temp_dir)
            logger.info(f"Extracted zip to {temp_dir}")

        extracted_items = os.listdir(temp_dir)
        py_files = [f for f in extracted_items if f.endswith('.py')]
        js_files = [f for f in extracted_items if f.endswith('.js')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None
        pkg_json = 'package.json' if 'package.json' in extracted_items else None

        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            logger.info(f"requirements.txt found, installing: {req_path}")
            bot.reply_to(message, f"🔄 Installing Python deps from `{req_file}`...")
            try:
                command = [sys.executable, '-m', 'pip', 'install', '-r', req_path]
                result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
                logger.info(f"pip install from requirements.txt OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Python deps from `{req_file}` installed.")
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Python deps from `{req_file}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Python deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        if pkg_json:
            logger.info(f"package.json found, npm install in: {temp_dir}")
            bot.reply_to(message, f"🔄 Installing Node deps from `{pkg_json}`...")
            try:
                command = ['npm', 'install']
                result = subprocess.run(command, capture_output=True, text=True, check=True, cwd=temp_dir, encoding='utf-8', errors='ignore')
                logger.info(f"npm install OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Node deps from `{pkg_json}` installed.")
            except FileNotFoundError:
                bot.reply_to(message, "❌ 'npm' not found. Cannot install Node deps."); return 
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Node deps from `{pkg_json}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Node deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        main_script_name = None; file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']; preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']
        for p in preferred_py:
            if p in py_files: main_script_name = p; file_type = 'py'; break
        if not main_script_name:
             for p in preferred_js:
                 if p in js_files: main_script_name = p; file_type = 'js'; break
        if not main_script_name:
            if py_files: main_script_name = py_files[0]; file_type = 'py'
            elif js_files: main_script_name = js_files[0]; file_type = 'js'
        if not main_script_name:
            bot.reply_to(message, "❌ No `.py` or `.js` script found in archive!"); return

        logger.info(f"Moving extracted files from {temp_dir} to {user_folder}")
        moved_count = 0
        for item_name in os.listdir(temp_dir):
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(user_folder, item_name)
            if os.path.isdir(dest_path): shutil.rmtree(dest_path)
            elif os.path.exists(dest_path): os.remove(dest_path)
            shutil.move(src_path, dest_path); moved_count +=1
        logger.info(f"Moved {moved_count} items to {user_folder}")

        save_user_file(user_id, main_script_name, file_type)
        try:
            archive_rec=mongo.call(self_db.files.find_one({"user_id":user_id,"file_name":file_name_zip}))
            if archive_rec:
                mongo.call(self_db.files.update_one({"file_id":f"{user_id}:{main_script_name}"},{"$set":{"archive_file_name":file_name_zip,"archive_database_message_id":archive_rec.get("database_message_id"),"archive_telegram_file_id":archive_rec.get("telegram_file_id"),"database_channel_id":BOT_DATABASE_CHANNEL_ID}},upsert=True))
        except Exception as e: report_exception(e,"zip_metadata_link",user_id,"FILE_STORE")
        logger.info(f"Saved main script '{main_script_name}' ({file_type}) for {user_id} from zip.")
        main_script_path = os.path.join(user_folder, main_script_name)
        bot.reply_to(message, f"✅ Files extracted. Starting main script: `{main_script_name}`...", parse_mode='Markdown')

        # Use user_id as script_owner_id for script key context
        if file_type == 'py':
             threading.Thread(target=run_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
        elif file_type == 'js':
             threading.Thread(target=run_js_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
             
    except Exception as e:
        logger.error(f"Error processing zip file: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
    finally:
        if cleanup_temp and temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir); logger.info(f"Cleaned temp dir: {temp_dir}")
            except Exception as e: logger.error(f"Failed to clean temp dir {temp_dir}: {e}", exc_info=True)

def handle_js_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'js')
        threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        logger.error(f"❌ Error processing JS file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing JS file: {str(e)}")

def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        logger.error(f"❌ Error processing Python file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing Python file: {str(e)}")

# --- Automatic Package Installation & Script Running ---
def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Run Python script. script_owner_id is used for the script_key. message_obj_for_reply is for sending feedback."""
    max_attempts = 2 
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run Python script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found at '{script_path}'!")
             logger.error(f"Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            logger.info(f"Running Python pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"Python Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip().strip("'\"")
                        logger.info(f"Detected missing Python module: {module_name}")
                        success, _ = attempt_install_pip(module_name, message_obj_for_reply)
                        if success:
                            logger.info(f"Install OK for {module_name}. Retrying run_script...")
                            bot.reply_to(message_obj_for_reply, f"🔄 Install successful. Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed. Cannot run '{file_name}'.")
                            return
                    else:
                         error_summary = stderr[:500]
                         bot.reply_to(message_obj_for_reply, f"❌ Error in script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix the script.", parse_mode='Markdown')
                         return
            except subprocess.TimeoutExpired:
                logger.info("Python Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("Python Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 logger.error(f"Python interpreter not found: {sys.executable}")
                 bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
                 return
            except Exception as e:
                 logger.error(f"Error in Python pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in script pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"Python Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"Starting long-running Python process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
             logger.error(f"Failed to open log file '{log_file_path}' for {script_key}: {e}", exc_info=True)
             bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
             return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"Started Python process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id, # Chat ID for potential future direct replies from script, defaults to admin/triggering user
                'script_owner_id': script_owner_id, # Actual owner of the script
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key, 'intentional_stop': False
            }
            try:
                rec=mongo.call(self_db.files.find_one({"user_id":script_owner_id,"file_name":file_name}))
                mark_hosted_bot(script_owner_id, rec.get("file_id") if rec else f"{script_owner_id}:{file_name}", file_name, 'py', 'running', pid=process.pid, started_at=datetime.utcnow().isoformat(), restart_count=len(restart_history.get(script_key,[])))
            except Exception as e: report_exception(e,"mark_hosted_bot_py",script_owner_id,"BOT_START")
            send_log(f"File: {file_name}\nPID: {process.pid}","INFO",script_owner_id,"BOT_START")
            bot.reply_to(message_obj_for_reply, f"✅ Python script '{file_name}' started! (PID: {process.pid}) (For User: {script_owner_id})")
        except FileNotFoundError:
             logger.error(f"Python interpreter {sys.executable} not found for long run {script_key}")
             bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
             if log_file and not log_file.closed: log_file.close()
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting Python script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"Killing potentially started Python process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running Python script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"Cleaning up {script_key} due to error in run_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Run JS script. script_owner_id is used for the script_key. message_obj_for_reply is for sending feedback."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run JS script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found at '{script_path}'!")
             logger.error(f"JS Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = ['node', script_path]
            logger.info(f"Running JS pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"JS Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip().strip("'\"")
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                             logger.info(f"Detected missing Node module: {module_name}")
                             success, _ = attempt_install_npm(module_name, user_folder, message_obj_for_reply)
                             if success:
                                 logger.info(f"NPM Install OK for {module_name}. Retrying run_js_script...")
                                 bot.reply_to(message_obj_for_reply, f"🔄 NPM Install successful. Retrying '{file_name}'...")
                                 time.sleep(2)
                                 threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                                 return
                             else:
                                 bot.reply_to(message_obj_for_reply, f"❌ NPM Install failed. Cannot run '{file_name}'.")
                                 return
                        else: logger.info(f"Skipping npm install for relative/core: {module_name}")
                    error_summary = stderr[:500]
                    bot.reply_to(message_obj_for_reply, f"❌ Error in JS script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix script or install manually.", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                logger.info("JS Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("JS Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 error_msg = "❌ Error: 'node' not found. Ensure Node.js is installed for JS files."
                 logger.error(error_msg)
                 bot.reply_to(message_obj_for_reply, error_msg)
                 return
            except Exception as e:
                 logger.error(f"Error in JS pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in JS pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"JS Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"Starting long-running JS process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Failed to open log file '{log_file_path}' for JS script {script_key}: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
            return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"Started JS process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id, # Chat ID for potential future direct replies
                'script_owner_id': script_owner_id, # Actual owner of the script
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js', 'script_key': script_key, 'intentional_stop': False
            }
            try:
                rec=mongo.call(self_db.files.find_one({"user_id":script_owner_id,"file_name":file_name}))
                mark_hosted_bot(script_owner_id, rec.get("file_id") if rec else f"{script_owner_id}:{file_name}", file_name, 'js', 'running', pid=process.pid, started_at=datetime.utcnow().isoformat(), restart_count=len(restart_history.get(script_key,[])))
            except Exception as e: report_exception(e,"mark_hosted_bot_js",script_owner_id,"BOT_START")
            send_log(f"File: {file_name}\nPID: {process.pid}","INFO",script_owner_id,"BOT_START")
            bot.reply_to(message_obj_for_reply, f"✅ JS script '{file_name}' started! (PID: {process.pid}) (For User: {script_owner_id})")
        except FileNotFoundError:
             error_msg = "❌ Error: 'node' not found for long run. Ensure Node.js is installed."
             logger.error(error_msg)
             if log_file and not log_file.closed: log_file.close()
             bot.reply_to(message_obj_for_reply, error_msg)
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting JS script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"Killing potentially started JS process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running JS script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"Cleaning up {script_key} due to error in run_js_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

# --- Logic Functions (called by commands and text handlers) ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name

    logger.info(f"Welcome request from user_id: {user_id}")

    # Check if user is banned
    if is_user_banned(user_id):
        bot.send_message(chat_id, "❌ You are banned from using this bot.")
        return

    # Check mandatory subscription FIRST - before anything else
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.send_message(chat_id, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_notification = (f"🎉 New user!\n👤 Name: {user_name}\n🆔 ID: `{user_id}`")
            bot.send_message(OWNER_ID, owner_notification, parse_mode='Markdown')
        except Exception as e: 
            logger.error(f"⚠️ Failed to notify owner about new user {user_id}: {e}")

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    
    if user_id == OWNER_ID: 
        user_status = "👑 Owner"
    elif user_id in admin_ids: 
        user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"
            days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Subscription expires in: {days_left} days"
        else: 
            user_status = "🆓 Free User (Expired Sub)"
            remove_subscription_db(user_id)
    else: 
        user_status = "🆓 Free User"

    welcome_msg_text = (f"〽️ Welcome, {user_name}!\n\n🆔 Your User ID: `{user_id}`\n"
                        f"🔰 Your Status: {user_status}{expiry_info}\n"
                        f"📁 Files Uploaded: {current_files} / {limit_str}\n\n"
                        f"🤖 Host & run Python (`.py`) or JS (`.js`) scripts.\n"
                        f"   Upload single scripts or `.zip` archives.\n"
                        f"📦 Manual module installation available\n\n"
                        f"👇 Use buttons or type commands.")
    
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    try:
        bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error sending welcome to {user_id}: {e}", exc_info=True)

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=f'https://t.me/{UPDATE_CHANNEL.replace("@", "")}'))
    bot.reply_to(message, "Visit our Updates Channel:", reply_markup=markup)

def _logic_upload_file(message):
    user_id = message.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin, cannot accept files.")
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files first.")
        return
    bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

def _logic_check_files(message):
    user_id = message.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "📂 Your files:\n\n(No files uploaded yet)")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name) # Use user_id for checking status
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        # Callback data includes user_id as script_owner_id
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    bot.reply_to(message, "📂 Your files:\nClick to manage.", reply_markup=markup, parse_mode='Markdown')

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    start_time_ping = time.time()
    wait_msg = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time_ping) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: user_level = "👑 Owner"
        elif user_id in admin_ids: user_level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     f"👤 Your Level: {user_level}")
        bot.edit_message_text(speed_msg, chat_id, wait_msg.message_id)
    except Exception as e:
        logger.error(f"Error during speed test (cmd): {e}", exc_info=True)
        bot.edit_message_text("❌ Error during speed test.", chat_id, wait_msg.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=markup)

def _logic_manual_install(message):
    """Handle manual installation request from user"""
    manual_install_module_init(message)

def _logic_help(message):
    help_text = """
🤖 **Arafat Hosting Bot Help Guide**

**📌 Basic Commands:**
• /start - Start the bot
• /help - Show this help message
• /status - Show bot statistics

**📁 File Management:**
• Upload `.py` or `.js` files directly
• Upload `.zip` archives with multiple files
• Auto-installs dependencies from `requirements.txt` or `package.json`

**📦 Module Installation:**
• Auto-install missing Python/Node modules
• Manual install via "📦 Manual Install" button
• Admin can install modules for users

**👑 Admin Features:**
• User management (ban/unban)
• Set custom file limits
• Manage mandatory channels
• Broadcast messages
• Run all user scripts

**⚙️ Tips:**
1. Make sure your scripts don't contain dangerous commands
2. Join all required channels
3. Contact owner for subscription upgrades

**Support:** @arafat_flex
**Updates:** @arafat_source
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')

# --- Admin Logic Functions ---
def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "💳 Subscription Management\nUse inline buttons from /start or admin command menu.", reply_markup=create_subscription_menu())

def _logic_statistics(message):
    user_id = message.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())

    running_bots_count = 0
    user_running_bots = 0

    for script_key_iter, script_info_iter in list(bot_scripts.items()):
        s_owner_id, _ = script_key_iter.split('_', 1) # Extract owner_id from key
        if is_bot_running(int(s_owner_id), script_info_iter['file_name']):
            running_bots_count += 1
            if int(s_owner_id) == user_id:
                user_running_bots +=1

    stats_msg_base = (f"📊 Bot Statistics:\n\n"
                      f"👥 Total Users: {total_users}\n"
                      f"🚫 Banned Users: {len(banned_users)}\n"
                      f"📂 Total File Records: {total_files_records}\n"
                      f"🟢 Total Active Bots: {running_bots_count}\n")

    if user_id in admin_ids:
        stats_msg_admin = (f"🔒 Bot Status: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}\n"
                           f"📢 Mandatory Channels: {len(mandatory_channels)}\n"
                           f"⚙️ Custom Limits: {len(user_limits)}\n"
                           f"🤖 Your Running Bots: {user_running_bots}")
        stats_msg = stats_msg_base + stats_msg_admin
    else:
        stats_msg = stats_msg_base + f"🤖 Your Running Bots: {user_running_bots}"

    bot.reply_to(message, stats_msg)

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast to all active users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    logger.warning(f"Bot {status} by Admin {message.from_user.id} via command/button.")
    bot.reply_to(message, f"🔒 Bot has been {status}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👑 Admin Panel\nManage admins. Use inline buttons from /start or admin menu.",
                 reply_markup=create_admin_panel())

def _logic_user_management(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👥 User Management\nManage users, set limits, ban/unban.", 
                 reply_markup=create_user_management_menu())

def _logic_admin_settings(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "⚙️ Admin Settings\nSystem information and management.", 
                 reply_markup=create_admin_settings_menu())

def _logic_run_all_scripts(message_or_call):
    if hasattr(message_or_call, "chat") and hasattr(message_or_call, "from_user"):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.chat.id
        reply_func = lambda text, **kwargs: bot.reply_to(message_or_call, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call
    elif hasattr(message_or_call, "message") and hasattr(message_or_call, "data"):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply_func = lambda text, **kwargs: bot.send_message(admin_chat_id, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call.message 
    else:
        logger.error("Invalid argument for _logic_run_all_scripts")
        return

    if admin_user_id not in admin_ids:
        reply_func("⚠️ Admin permissions required.")
        return

    reply_func("⏳ Starting process to run all user scripts. This may take a while...")
    logger.info(f"Admin {admin_user_id} initiated 'run all scripts' from chat {admin_chat_id}.")

    started_count = 0; attempted_users = 0; skipped_files = 0; error_files_details = []

    # Use a copy of user_files keys and values to avoid modification issues during iteration
    all_user_files_snapshot = dict(user_files)

    for target_user_id, files_for_user in all_user_files_snapshot.items():
        if not files_for_user: continue
        attempted_users += 1
        logger.info(f"Processing scripts for user {target_user_id}...")
        user_folder = get_user_folder(target_user_id)

        for file_name, file_type in files_for_user:
            # script_owner_id for key context is target_user_id
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    logger.info(f"Admin {admin_user_id} attempting to start '{file_name}' ({file_type}) for user {target_user_id}.")
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        else:
                            logger.warning(f"Unknown file type '{file_type}' for {file_name} (user {target_user_id}). Skipping.")
                            error_files_details.append(f"`{file_name}` (User {target_user_id}) - Unknown type")
                            skipped_files += 1
                        time.sleep(0.7) # Increased delay slightly
                    except Exception as e:
                        logger.error(f"Error queueing start for '{file_name}' (user {target_user_id}): {e}")
                        error_files_details.append(f"`{file_name}` (User {target_user_id}) - Start error")
                        skipped_files += 1
                else:
                    logger.warning(f"File '{file_name}' for user {target_user_id} not found at '{file_path}'. Skipping.")
                    error_files_details.append(f"`{file_name}` (User {target_user_id}) - File not found")
                    skipped_files += 1
            # else: logger.info(f"Script '{file_name}' for user {target_user_id} already running.")

    summary_msg = (f"✅ All Users' Scripts - Processing Complete:\n\n"
                   f"▶️ Attempted to start: {started_count} scripts.\n"
                   f"👥 Users processed: {attempted_users}.\n")
    if skipped_files > 0:
        summary_msg += f"⚠️ Skipped/Error files: {skipped_files}\n"
        if error_files_details:
             summary_msg += "Details (first 5):\n" + "\n".join([f"  - {err}" for err in error_files_details[:5]])
             if len(error_files_details) > 5: summary_msg += "\n  ... and more (check logs)."

    reply_func(summary_msg, parse_mode='Markdown')
    logger.info(f"Run all scripts finished. Admin: {admin_user_id}. Started: {started_count}. Skipped/Errors: {skipped_files}")

# --- New Admin Functions for Channel Management ---
def _logic_manage_mandatory_channels(message):
    """Manage mandatory channels - for admin only"""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "📢 Manage Mandatory Channels\nUse the buttons below:", reply_markup=create_mandatory_channels_menu())

def _logic_admin_install(message):
    """Admin manual installation for users"""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "🛠️ Admin Module Installation\nSend user ID and module name (e.g., `12345678 requests`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_admin_install)

def process_admin_install(message):
    """Process admin installation request"""
    admin_id = message.from_user.id
    if admin_id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
        
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Installation cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "⚠️ Format: `user_id module_name`\nExample: `12345678 requests`")
            return
            
        user_id = int(parts[0])
        module_name = ' '.join(parts[1:])
        
        # Check if it's a Node.js module
        if module_name.lower().startswith('npm:'):
            module_name = module_name[4:].strip()
            user_folder = get_user_folder(user_id)
            success, log = attempt_install_npm(module_name, user_folder, message, manual_request=True)
        else:
            # Python module
            success, log = attempt_install_pip(module_name, message, manual_request=True)
        
        if success:
            logger.info(f"Admin {admin_id} installed module {module_name} for user {user_id}")
            # Notify user
            try:
                bot.send_message(user_id, f"📦 Admin installed module `{module_name}` for you.")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error in admin install: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

# --- Command Handlers & Text Handlers for ReplyKeyboard ---
@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message): 
    if message.text == '/help':
        _logic_help(message)
    else:
        _logic_send_welcome(message)

@bot.message_handler(commands=['status']) # Kept for direct command
def command_show_status(message): _logic_statistics(message)

BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics, 
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot, 
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
    "📢 Channel Add": _logic_manage_mandatory_channels,
    "👥 User Management": _logic_user_management,
    "🛠️ Manual Install": _logic_manual_install,
    "⚙️ Settings": _logic_admin_settings,
    "📦 Manual Install": _logic_manual_install,
    "🆘 Help": _logic_help
}

@bot.message_handler(func=lambda message: message.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    logic_func = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if logic_func: logic_func(message)
    else: logger.warning(f"Button text '{message.text}' matched but no logic func.")

@bot.message_handler(commands=['updateschannel'])
def command_updates_channel(message): _logic_updates_channel(message)
@bot.message_handler(commands=['uploadfile'])
def command_upload_file(message): _logic_upload_file(message)
@bot.message_handler(commands=['checkfiles'])
def command_check_files(message): _logic_check_files(message)
@bot.message_handler(commands=['botspeed'])
def command_bot_speed(message): _logic_bot_speed(message)
@bot.message_handler(commands=['contactowner'])
def command_contact_owner(message): _logic_contact_owner(message)
@bot.message_handler(commands=['subscriptions'])
def command_subscriptions(message): _logic_subscriptions_panel(message)
@bot.message_handler(commands=['statistics']) # Alias for /status
def command_statistics(message): _logic_statistics(message)
@bot.message_handler(commands=['broadcast'])
def command_broadcast(message): _logic_broadcast_init(message)
@bot.message_handler(commands=['lockbot']) 
def command_lock_bot(message): _logic_toggle_lock_bot(message)
@bot.message_handler(commands=['adminpanel'])
def command_admin_panel(message): _logic_admin_panel(message)
@bot.message_handler(commands=['runningallcode']) # Added
def command_run_all_code(message): _logic_run_all_scripts(message)
@bot.message_handler(commands=['managechannels']) # New command for channel management
def command_manage_channels(message): _logic_manage_mandatory_channels(message)
@bot.message_handler(commands=['usermanagement'])
def command_user_management(message): _logic_user_management(message)
@bot.message_handler(commands=['manualinstall'])
def command_manual_install(message): _logic_manual_install(message)
@bot.message_handler(commands=['admininstall'])
def command_admin_install(message): _logic_admin_install(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    user_id = message.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    start_ping_time = time.time() 
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start_ping_time) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)

# --- Document (File) Handler ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return

    doc = message.document
    logger.info(f"Doc from {user_id}: {doc.file_name} ({doc.mime_type}), Size: {doc.file_size}")

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    # File limit check (relies on FREE_USER_LIMIT being > 0 for free users)
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files via /checkfiles.")
        return

    file_name = doc.file_name
    if not file_name: bot.reply_to(message, "⚠️ No file name. Ensure file has a name."); return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    max_file_size = 20 * 1024 * 1024 # 20 MB
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB)."); return

    try:
        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
            bot.send_message(OWNER_ID, f"⬆️ File '{file_name}' from {message.from_user.first_name} (`{user_id}`)", parse_mode='Markdown')
        except Exception as e: logger.error(f"Failed to forward uploaded file to OWNER_ID {OWNER_ID}: {e}")

        download_wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...")
        file_info_tg_doc = bot.get_file(doc.file_id)
        downloaded_file_content = bot.download_file(file_info_tg_doc.file_path)
        bot.edit_message_text(f"✅ Downloaded `{file_name}`. Processing...", chat_id, download_wait_msg.message_id)
        logger.info(f"Downloaded {file_name} for user {user_id}")
        user_folder = get_user_folder(user_id)
        # Persist the actual uploaded file in the dedicated Telegram database channel.
        database_temp_path = os.path.join(user_folder, f".database_{message.message_id}_{file_name}")
        with open(database_temp_path, "wb") as _dbf: _dbf.write(downloaded_file_content)
        stored_record = store_file_in_bot_database(database_temp_path, file_name, user_id, file_ext.lstrip("."))
        try: os.remove(database_temp_path)
        except OSError: pass

        if file_ext == '.zip':
            handle_zip_file(downloaded_file_content, file_name, message)
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f: f.write(downloaded_file_content)
            logger.info(f"Saved single file to {file_path}")
            if stored_record:
                mongo.call(self_db.files.update_one({"file_id":f"{user_id}:{file_name}"},{"$set":{"telegram_file_id":stored_record.get("telegram_file_id"),"database_message_id":stored_record.get("database_message_id"),"database_channel_id":BOT_DATABASE_CHANNEL_ID,"file_size":doc.file_size}},upsert=True))
            
            # Security check for script files (lightweight)
            is_safe, security_msg = check_code_security(file_path, file_ext[1:])
            if not is_safe:
                # Send security warning to admin for approval
                security_warning_msg = f"🚨 File needs approval:\n👤 User: {user_id}\n📁 File: {file_name}\n⚠️ Reason: {security_msg}"
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_file_{user_id}_{file_name}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_file_{user_id}_{file_name}")
                )
                for admin_id in admin_ids:
                    try:
                        bot.send_message(admin_id, security_warning_msg, reply_markup=markup)
                    except Exception as e:
                        logger.error(f"Failed to send security warning to admin {admin_id}: {e}")
                
                bot.reply_to(message, f"⏳ File under security review. You will be notified upon approval.")
                return
                
            # Pass user_id as script_owner_id
            if file_ext == '.js': handle_js_file(file_path, user_id, user_folder, file_name, message)
            elif file_ext == '.py': handle_py_file(file_path, user_id, user_folder, file_name, message)
    except TelegramCompatError as e:
         logger.error(f"Telegram API Error handling file for {user_id}: {e}", exc_info=True)
         if "file is too big" in str(e).lower():
              bot.reply_to(message, f"❌ Telegram API Error: File too large to download (~20MB limit).")
         else: bot.reply_to(message, f"❌ Telegram API Error: {str(e)}. Try later.")
    except Exception as e:
        logger.error(f"❌ General error handling file for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Unexpected error: {str(e)}")

# --- Callback Query Handlers (for Inline Buttons) ---
@bot.callback_query_handler(func=lambda call: True) 
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    logger.info(f"Callback: User={user_id}, Data='{data}'")

    # Check if user is banned
    if is_user_banned(user_id) and data not in ['back_to_main']:
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return

    # Allow subscription check and back to main without subscription
    if data not in ['check_subscription_status', 'back_to_main', 'manual_install']:
        # Check mandatory subscription for other callbacks
        is_subscribed, not_joined = check_mandatory_subscription(user_id)
        if not is_subscribed and user_id not in admin_ids:
            subscription_message, markup = create_subscription_check_message(not_joined)
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(subscription_message, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
            except Exception as fallback_error:
                logger.warning("Callback subscription edit fallback failed: %s", fallback_error)
                bot.send_message(call.message.chat.id, subscription_message, reply_markup=markup, parse_mode='Markdown')
            return

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'check_subscription_status', 'manual_install']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked by admin.", show_alert=True)
        return
        
    try:
        if data == 'upload': upload_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'manual_install': manual_install_callback(call)
        # --- Admin Callbacks ---
        elif data == 'subscription': admin_required_callback(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call) # No admin check here, handled in func
        elif data == 'lock_bot': admin_required_callback(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_required_callback(call, unlock_bot_callback)
        elif data == 'run_all_scripts': admin_required_callback(call, run_all_scripts_callback)
        elif data == 'broadcast': admin_required_callback(call, broadcast_init_callback) 
        elif data == 'admin_panel': admin_required_callback(call, admin_panel_callback)
        elif data == 'add_admin': owner_required_callback(call, add_admin_init_callback) 
        elif data == 'remove_admin': owner_required_callback(call, remove_admin_init_callback) 
        elif data == 'list_admins': admin_required_callback(call, list_admins_callback)
        elif data == 'add_subscription': admin_required_callback(call, add_subscription_init_callback) 
        elif data == 'remove_subscription': admin_required_callback(call, remove_subscription_init_callback) 
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback)
        elif data == 'user_management': admin_required_callback(call, user_management_callback)
        elif data == 'ban_user': admin_required_callback(call, ban_user_callback)
        elif data == 'unban_user': admin_required_callback(call, unban_user_callback)
        elif data == 'user_info': admin_required_callback(call, user_info_callback)
        elif data == 'all_users': admin_required_callback(call, all_users_callback)
        elif data == 'set_user_limit': admin_required_callback(call, set_user_limit_callback)
        elif data == 'remove_user_limit': admin_required_callback(call, remove_user_limit_callback)
        elif data == 'admin_settings': admin_required_callback(call, admin_settings_callback)
        elif data == 'system_info': admin_required_callback(call, system_info_callback)
        elif data == 'bot_performance': admin_required_callback(call, bot_performance_callback)
        elif data == 'cleanup_files': admin_required_callback(call, cleanup_files_callback)
        elif data == 'install_logs': admin_required_callback(call, install_logs_callback)
        elif data == 'admin_install': admin_required_callback(call, admin_install_callback)
        # --- Mandatory Channels Callbacks ---
        elif data == 'manage_mandatory_channels': admin_required_callback(call, manage_mandatory_channels_callback)
        elif data == 'add_mandatory_channel': admin_required_callback(call, add_mandatory_channel_callback)
        elif data == 'remove_mandatory_channel': admin_required_callback(call, remove_mandatory_channel_callback)
        elif data == 'list_mandatory_channels': admin_required_callback(call, list_mandatory_channels_callback)
        elif data.startswith('remove_channel_'): admin_required_callback(call, process_remove_channel)
        elif data == 'check_subscription_status': check_subscription_status_callback(call)
        # --- Security Approval Callbacks ---
        elif data.startswith('approve_file_'): admin_required_callback(call, process_approve_file)
        elif data.startswith('reject_file_'): admin_required_callback(call, process_reject_file)
        elif data.startswith('approve_zip_'): admin_required_callback(call, process_approve_zip)
        elif data.startswith('reject_zip_'): admin_required_callback(call, process_reject_zip)
        else:
            bot.answer_callback_query(call.id, "Unknown action.")
            logger.warning(f"Unhandled callback data: {data} from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling callback '{data}' for {user_id}: {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error processing request.", show_alert=True)
        except Exception as e_ans: logger.error(f"Failed to answer callback after error: {e_ans}")

def admin_required_callback(call, func_to_run):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    func_to_run(call) 

def owner_required_callback(call, func_to_run):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner permissions required.", show_alert=True)
        return
    func_to_run(call)

# --- User Callbacks ---
def manual_install_callback(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    manual_install_module_init(call.message)

def upload_callback(call):
    user_id = call.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(subscription_message, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.debug("Subscription edit fallback: %s", edit_error)
            bot.send_message(call.message.chat.id, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ File limit ({current_files}/{limit_str}) reached.", show_alert=True)
        return
    bot.answer_callback_query(call.id) 
    bot.send_message(call.message.chat.id, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

def check_files_callback(call):
    user_id = call.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(subscription_message, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.debug("Subscription edit fallback: %s", edit_error)
            bot.send_message(call.message.chat.id, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    chat_id = call.message.chat.id 
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.answer_callback_query(call.id, "⚠️ No files uploaded.", show_alert=True)
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
            bot.edit_message_text("📂 Your files:\n\n(No files uploaded)", chat_id, call.message.message_id, reply_markup=markup)
        except Exception as e: logger.error(f"Error editing msg for empty file list: {e}")
        return
    bot.answer_callback_query(call.id) 
    markup = types.InlineKeyboardMarkup(row_width=1) 
    for file_name, file_type in sorted(user_files_list): 
        is_running = is_bot_running(user_id, file_name) # Use user_id for status check
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        # Callback includes user_id as script_owner_id
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    try:
        bot.edit_message_text("📂 Your files:\nClick to manage.", chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except TelegramCompatError as e:
         if "message is not modified" in str(e): logger.warning("Msg not modified (files).")
         else: logger.error(f"Error editing msg for file list: {e}")
    except Exception as e: logger.error(f"Unexpected error editing msg for file list: {e}", exc_info=True)

def file_control_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id

        # Allow owner/admin to control any file, or user to control their own
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            logger.warning(f"User {requesting_user_id} tried to access file '{file_name}' of user {script_owner_id} without permission.")
            bot.answer_callback_query(call.id, "⚠️ You can only manage your own files.", show_alert=True)
            check_files_callback(call) # Show their own files
            return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            logger.warning(f"File '{file_name}' not found for user {script_owner_id} during control.")
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            # If admin was viewing, this might be confusing. For now, just show their own.
            check_files_callback(call) 
            return

        bot.answer_callback_query(call.id) 
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Running' if is_running else '🔴 Stopped'
        file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?') 
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
                parse_mode='Markdown'
            )
        except TelegramCompatError as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified (controls for {file_name})")
             else: raise 
    except (ValueError, IndexError) as ve:
        logger.error(f"Error parsing file control callback: {ve}. Data: '{call.data}'")
        bot.answer_callback_query(call.id, "Error: Invalid action data.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in file_control_callback for data '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "An error occurred.", show_alert=True)

def start_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id # Where the admin/user gets the reply

        logger.info(f"Start request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied to start this script.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ Error: File `{file_name}` missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name); check_files_callback(call); return

        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already running.", show_alert=True)
            try: bot.edit_message_reply_markup(chat_id_for_reply, call.message.message_id, reply_markup=create_control_buttons(script_owner_id, file_name, True))
            except Exception as e: logger.error(f"Error updating buttons (already running): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Attempting to start {file_name} for user {script_owner_id}...")

        # Pass call.message as message_obj_for_reply so feedback goes to the person who clicked
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Error: Unknown file type '{file_type}' for '{file_name}'."); return 

        time.sleep(1.5) # Give script time to actually start or fail early
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting (or failed, check logs/replies)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except TelegramCompatError as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified after starting {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing start callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid start command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in start_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error starting script.", show_alert=True)
        try: # Attempt to reset buttons to 'stopped' state on error
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"Failed to update buttons after start error: {e_btn}")

def stop_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Stop request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1] 
        script_key = f"{script_owner_id}_{file_name}"

        if not is_bot_running(script_owner_id, file_name): 
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already stopped.", show_alert=True)
            try:
                 bot.edit_message_text(
                     f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                     chat_id_for_reply, call.message.message_id,
                     reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown')
            except Exception as e: logger.error(f"Error updating buttons (already stopped): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Stopping {file_name} for user {script_owner_id}...")
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]; logger.info(f"Removed {script_key} from running after stop.")
            mark_hosted_bot(script_owner_id,file_name,file_name,'py' if file_name.endswith('.py') else 'js','stopped',pid=None,stopped_at=datetime.utcnow().isoformat())
            send_log(f"File: {file_name}","INFO",script_owner_id,"BOT_STOP")
        else: logger.warning(f"Script {script_key} running by psutil but not in bot_scripts dict.")

        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown'
            )
        except TelegramCompatError as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified after stopping {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing stop callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid stop command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in stop_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error stopping script.", show_alert=True)

def restart_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Restart: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]; user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name); script_key = f"{script_owner_id}_{file_name}"

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ Error: File `{file_name}` missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name)
            if script_key in bot_scripts: del bot_scripts[script_key]
            check_files_callback(call); return

        bot.answer_callback_query(call.id, f"⏳ Restarting {file_name} for user {script_owner_id}...")
        if is_bot_running(script_owner_id, file_name):
            logger.info(f"Restart: Stopping existing {script_key}...")
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1.5) 

        logger.info(f"Restart: Starting script {script_key}...")
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Unknown type '{file_type}' for '{file_name}'."); return

        time.sleep(1.5) 
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting (or failed)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except TelegramCompatError as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified (restart {file_name})")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing restart callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid restart command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in restart_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error restarting.", show_alert=True)
        try:
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"Failed to update buttons after restart error: {e_btn}")

def delete_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Delete: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        bot.answer_callback_query(call.id, f"🗑️ Deleting {file_name} for user {script_owner_id}...")
        script_key = f"{script_owner_id}_{file_name}"
        if is_bot_running(script_owner_id, file_name):
            logger.info(f"Delete: Stopping {script_key}...")
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(0.5) 

        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        deleted_disk = []
        if os.path.exists(file_path):
            try: os.remove(file_path); deleted_disk.append(file_name); logger.info(f"Deleted file: {file_path}")
            except OSError as e: logger.error(f"Error deleting {file_path}: {e}")
        if os.path.exists(log_path):
            try: os.remove(log_path); deleted_disk.append(os.path.basename(log_path)); logger.info(f"Deleted log: {log_path}")
            except OSError as e: logger.error(f"Error deleting log {log_path}: {e}")

        remove_user_file_db(script_owner_id, file_name)
        send_log(f"File: {file_name}","INFO",script_owner_id,"BOT_DELETE")
        deleted_str = ", ".join(f"`{f}`" for f in deleted_disk) if deleted_disk else "associated files"
        try:
            bot.edit_message_text(
                f"🗑️ Record `{file_name}` (User `{script_owner_id}`) and {deleted_str} deleted!",
                chat_id_for_reply, call.message.message_id, reply_markup=None, parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error editing msg after delete: {e}")
            bot.send_message(chat_id_for_reply, f"🗑️ Record `{file_name}` deleted.", parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing delete callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid delete command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in delete_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

def logs_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Logs: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        user_folder = get_user_folder(script_owner_id)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, f"⚠️ No logs for '{file_name}'.", show_alert=True); return

        bot.answer_callback_query(call.id) 
        try:
            log_content = ""; file_size = os.path.getsize(log_path)
            max_log_kb = 100; max_tg_msg = 4096
            if file_size == 0: log_content = "(Log empty)"
            elif file_size > max_log_kb * 1024:
                 with open(log_path, 'rb') as f: f.seek(-max_log_kb * 1024, os.SEEK_END); log_bytes = f.read()
                 log_content = log_bytes.decode('utf-8', errors='ignore')
                 log_content = f"(Last {max_log_kb} KB)\n...\n" + log_content
            else:
                 with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: log_content = f.read()

            if len(log_content) > max_tg_msg:
                log_content = log_content[-max_tg_msg:]
                first_nl = log_content.find('\n')
                if first_nl != -1: log_content = "...\n" + log_content[first_nl+1:]
                else: log_content = "...\n" + log_content 
            if not log_content.strip(): log_content = "(No visible content)"

            bot.send_message(chat_id_for_reply, f"📜 Logs for `{file_name}` (User `{script_owner_id}`):\n```\n{log_content}\n```", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error reading/sending log {log_path}: {e}", exc_info=True)
            bot.send_message(chat_id_for_reply, f"❌ Error reading log for `{file_name}`.")
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing logs callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid logs command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in logs_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error fetching logs.", show_alert=True)

def speed_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(subscription_message, chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.debug("Subscription edit fallback: %s", edit_error)
            bot.send_message(chat_id, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    start_cb_ping_time = time.time() 
    try:
        bot.edit_message_text("🏃 Testing speed...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing') 
        response_time = round((time.time() - start_cb_ping_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: user_level = "👑 Owner"
        elif user_id in admin_ids: user_level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     f"👤 Your Level: {user_level}")
        bot.answer_callback_query(call.id) 
        bot.edit_message_text(speed_msg, chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
    except Exception as e:
         logger.error(f"Error during speed test (cb): {e}", exc_info=True)
         bot.answer_callback_query(call.id, "Error in speed test.", show_alert=True)
         try: bot.edit_message_text("〽️ Main Menu", chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
         except Exception: pass

def back_to_main_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(subscription_message, chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as edit_error:
            logger.debug("Subscription edit fallback: %s", edit_error)
            bot.send_message(chat_id, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "👑 Owner"
    elif user_id in admin_ids: user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"; days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Subscription expires in: {days_left} days"
        else: user_status = "🆓 Free User (Expired Sub)" # Will be cleaned up by welcome if not already
    else: user_status = "🆓 Free User"
    main_menu_text = (f"〽️ Welcome back, {call.from_user.first_name}!\n\n🆔 ID: `{user_id}`\n"
                      f"🔰 Status: {user_status}{expiry_info}\n📁 Files: {current_files} / {limit_str}\n\n"
                      f"👇 Use buttons or type commands.")
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(main_menu_text, chat_id, call.message.message_id,
                              reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
    except TelegramCompatError as e:
         if "message is not modified" in str(e): logger.warning("Msg not modified (back_to_main).")
         else: logger.error(f"API error on back_to_main: {e}")
    except Exception as e: logger.error(f"Error handling back_to_main: {e}", exc_info=True)

# --- Admin Callback Implementations (for Inline Buttons) ---
def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("💳 Subscription Management\nSelect action:",
                              call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())
    except Exception as e: logger.error(f"Error showing sub menu: {e}")

def stats_callback(call): # Called by user and admin
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message) 
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e:
        logger.error(f"Error updating menu after stats_callback: {e}")

def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    logger.warning(f"Bot locked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e: logger.error(f"Error updating menu (lock): {e}")

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    logger.warning(f"Bot unlocked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e: logger.error(f"Error updating menu (unlock): {e}")

def run_all_scripts_callback(call): # Added
    _logic_run_all_scripts(call) # Pass the call object

def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "Broadcast cancelled."); return

    broadcast_content = message.text # Can also handle photos, videos etc. if message.content_type is checked
    if not broadcast_content and not (message.photo or message.video or message.document or message.sticker or message.voice or message.audio): # If no text and no other media
         bot.reply_to(message, "⚠️ Cannot broadcast empty message. Send text or media, or /cancel.")
         msg = bot.send_message(message.chat.id, "📢 Send broadcast message or /cancel.")
         bot.register_next_step_handler(msg, process_broadcast_message)
         return

    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
               types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"))

    preview_text = broadcast_content[:1000].strip() if broadcast_content else "(Media message)"
    bot.reply_to(message, f"⚠️ Confirm Broadcast:\n\n```\n{preview_text}\n```\n" 
                          f"To **{target_count}** users. Sure?", reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True); return
    try:
        original_message = call.message.reply_to_message
        if not original_message: raise ValueError("Could not retrieve original message.")

        # Check content type and get content
        broadcast_text = None
        broadcast_photo_id = None
        broadcast_video_id = None
        # Add other types as needed: document, sticker, voice, audio

        if original_message.text:
            broadcast_text = original_message.text
        elif original_message.photo:
            broadcast_photo_id = original_message.photo[-1].file_id # Get highest quality
        elif original_message.video:
            broadcast_video_id = original_message.video.file_id
        # Add more elif for other content types
        else:
            raise ValueError("Message has no text or supported media for broadcast.")

        bot.answer_callback_query(call.id, "🚀 Starting broadcast...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        # Pass all potential content types to execute_broadcast
        thread = threading.Thread(target=execute_broadcast, args=(
            broadcast_text, broadcast_photo_id, broadcast_video_id, 
            original_message.caption if (broadcast_photo_id or broadcast_video_id) else None, # Pass caption
            chat_id))
        thread.start()
    except ValueError as ve: 
        logger.error(f"Error retrieving msg for broadcast confirm: {ve}")
        bot.edit_message_text(f"❌ Error starting broadcast: {ve}", chat_id, call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.error(f"Error in handle_confirm_broadcast: {e}", exc_info=True)
        bot.edit_message_text("❌ Unexpected error during broadcast confirm.", chat_id, call.message.message_id, reply_markup=None)

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Broadcast cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    # Optionally delete the original message too if call.message.reply_to_message exists
    if call.message.reply_to_message:
        try: bot.delete_message(call.message.chat.id, call.message.reply_to_message.message_id)
        except Exception as cleanup_error:
            logger.debug("Cleanup ignored: %s", cleanup_error)

def execute_broadcast(broadcast_text, photo_id, video_id, caption, admin_chat_id):
    sent_count = 0; failed_count = 0; blocked_count = 0
    start_exec_time = time.time() 
    users_to_broadcast = list(active_users); total_users = len(users_to_broadcast)
    logger.info(f"Executing broadcast to {total_users} users.")
    batch_size = 25; delay_batches = 1.5

    for i, user_id_bc in enumerate(users_to_broadcast): # Renamed
        try:
            if broadcast_text:
                bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
            elif photo_id:
                bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
            elif video_id:
                bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
            # Add other send methods for other types
            sent_count += 1
        except TelegramCompatError as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found", "kicked from", "restricted"]): 
                logger.warning(f"Broadcast failed to {user_id_bc}: User blocked/inactive.")
                blocked_count += 1
            elif "flood control" in err_desc or "too many requests" in err_desc:
                retry_after = 5; match = re.search(r"retry after (\d+)", err_desc)
                if match: retry_after = int(match.group(1)) + 1 
                logger.warning(f"Flood control. Sleeping {retry_after}s...")
                time.sleep(retry_after)
                try: # Retry once
                    if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
                    elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
                    elif video_id: bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
                    sent_count += 1
                except Exception as e_retry: logger.error(f"Broadcast retry failed to {user_id_bc}: {e_retry}"); failed_count +=1
            else: logger.error(f"Broadcast failed to {user_id_bc}: {e}"); failed_count += 1
        except Exception as e: logger.error(f"Unexpected error broadcasting to {user_id_bc}: {e}"); failed_count += 1

        if (i + 1) % batch_size == 0 and i < total_users - 1:
            logger.info(f"Broadcast batch {i//batch_size + 1} sent. Sleeping {delay_batches}s...")
            time.sleep(delay_batches)
        elif i % 5 == 0: time.sleep(0.2) 

    duration = round(time.time() - start_exec_time, 2)
    result_msg = (f"📢 Broadcast Complete!\n\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\n"
                  f"🚫 Blocked/Inactive: {blocked_count}\n👥 Targets: {total_users}\n⏱️ Duration: {duration}s")
    logger.info(result_msg)
    try: bot.send_message(admin_chat_id, result_msg)
    except Exception as e: logger.error(f"Failed to send broadcast result to admin {admin_chat_id}: {e}")

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👑 Admin Panel\nManage admins (Owner actions may be restricted).",
                              call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
    except Exception as e: logger.error(f"Error showing admin panel: {e}")

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to promote to Admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    owner_id_check = message.from_user.id 
    if owner_id_check != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Admin promotion cancelled."); return
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id <= 0: raise ValueError("ID must be positive")
        if new_admin_id == OWNER_ID: bot.reply_to(message, "⚠️ Owner is already Owner."); return
        if new_admin_id in admin_ids: bot.reply_to(message, f"⚠️ User `{new_admin_id}` already Admin."); return
        add_admin_db(new_admin_id, owner_id_check) 
        logger.warning(f"Admin {new_admin_id} added by Owner {owner_id_check}.")
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.")
        try: bot.send_message(new_admin_id, "🎉 Congrats! You are now an Admin.")
        except Exception as e: logger.error(f"Failed to notify new admin {new_admin_id}: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID to promote or /cancel.")
        bot.register_next_step_handler(msg, process_add_admin_id)
    except Exception as e: logger.error(f"Error processing add admin: {e}", exc_info=True); bot.reply_to(message, "Error.")

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID of Admin to remove.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    owner_id_check = message.from_user.id
    if owner_id_check != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Admin removal cancelled."); return
    try:
        admin_id_remove = int(message.text.strip()) # Renamed
        if admin_id_remove <= 0: raise ValueError("ID must be positive")
        if admin_id_remove == OWNER_ID: bot.reply_to(message, "⚠️ Owner cannot remove self."); return
        if admin_id_remove not in admin_ids: bot.reply_to(message, f"⚠️ User `{admin_id_remove}` not Admin."); return
        if remove_admin_db(admin_id_remove): 
            logger.warning(f"Admin {admin_id_remove} removed by Owner {owner_id_check}.")
            bot.reply_to(message, f"✅ Admin `{admin_id_remove}` removed.")
            try: bot.send_message(admin_id_remove, "ℹ️ You are no longer an Admin.")
            except Exception as e: logger.error(f"Failed to notify removed admin {admin_id_remove}: {e}")
        else: bot.reply_to(message, f"❌ Failed to remove admin `{admin_id_remove}`. Check logs.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter Admin ID to remove or /cancel.")
        bot.register_next_step_handler(msg, process_remove_admin_id)
    except Exception as e: logger.error(f"Error processing remove admin: {e}", exc_info=True); bot.reply_to(message, "Error.")

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        admin_list_str = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(list(admin_ids)))
        if not admin_list_str: admin_list_str = "(No Owner/Admins configured!)"
        bot.edit_message_text(f"👑 Current Admins:\n\n{admin_list_str}", call.message.chat.id,
                              call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
    except Exception as e: logger.error(f"Error listing admins: {e}")

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID & days (e.g., `12345678 30`).\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_subscription_details)

def process_add_subscription_details(message):
    admin_id_check = message.from_user.id 
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub add cancelled."); return
    try:
        parts = message.text.split();
        if len(parts) != 2: raise ValueError("Incorrect format")
        sub_user_id = int(parts[0].strip()); days = int(parts[1].strip())
        if sub_user_id <= 0 or days <= 0: raise ValueError("User ID/days must be positive")

        current_expiry = user_subscriptions.get(sub_user_id, {}).get('expiry')
        start_date_new_sub = datetime.now() # Renamed
        if current_expiry and current_expiry > start_date_new_sub: start_date_new_sub = current_expiry
        new_expiry = start_date_new_sub + timedelta(days=days)
        save_subscription(sub_user_id, new_expiry)

        logger.info(f"Sub for {sub_user_id} by admin {admin_id_check}. Expiry: {new_expiry:%Y-%m-%d}")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id}` by {days} days.\nNew expiry: {new_expiry:%Y-%m-%d}")
        try: bot.send_message(sub_user_id, f"🎉 Sub activated/extended by {days} days! Expires: {new_expiry:%Y-%m-%d}.")
        except Exception as e: logger.error(f"Failed to notify {sub_user_id} of new sub: {e}")
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid: {e}. Format: `ID days` or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID & days, or /cancel.")
        bot.register_next_step_handler(msg, process_add_subscription_details)
    except Exception as e: logger.error(f"Error processing add sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to remove sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_subscription_id)

def process_remove_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub removal cancelled."); return
    try:
        sub_user_id_remove = int(message.text.strip()) # Renamed
        if sub_user_id_remove <= 0: raise ValueError("ID must be positive")
        if sub_user_id_remove not in user_subscriptions:
            bot.reply_to(message, f"⚠️ User `{sub_user_id_remove}` no active sub in memory."); return
        remove_subscription_db(sub_user_id_remove) 
        logger.warning(f"Sub removed for {sub_user_id_remove} by admin {admin_id_check}.")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id_remove}` removed.")
        try: bot.send_message(sub_user_id_remove, "ℹ️ Your subscription removed by admin.")
        except Exception as e: logger.error(f"Failed to notify {sub_user_id_remove} of sub removal: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID to remove sub from, or /cancel.")
        bot.register_next_step_handler(msg, process_remove_subscription_id)
    except Exception as e: logger.error(f"Error processing remove sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub check cancelled."); return
    try:
        sub_user_id_check = int(message.text.strip()) # Renamed
        if sub_user_id_check <= 0: raise ValueError("ID must be positive")
        if sub_user_id_check in user_subscriptions:
            expiry_dt = user_subscriptions[sub_user_id_check].get('expiry')
            if expiry_dt:
                if expiry_dt > datetime.now():
                    days_left = (expiry_dt - datetime.now()).days
                    bot.reply_to(message, f"✅ User `{sub_user_id_check}` active sub.\nExpires: {expiry_dt:%Y-%m-%d %H:%M:%S} ({days_left} days left).")
                else:
                    bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` expired sub (On: {expiry_dt:%Y-%m-%d %H:%M:%S}).")
                    remove_subscription_db(sub_user_id_check) # Clean up
            else: bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` in sub list, but expiry missing. Re-add if needed.")
        else: bot.reply_to(message, f"ℹ️ User `{sub_user_id_check}` no active sub record.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID to check, or /cancel.")
        bot.register_next_step_handler(msg, process_check_subscription_id)
    except Exception as e: logger.error(f"Error processing check sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

# --- User Management Callbacks ---
def user_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👥 User Management\nSelect action:", call.message.chat.id, 
                              call.message.message_id, reply_markup=create_user_management_menu())
    except Exception as e: logger.error(f"Error showing user management menu: {e}")

def ban_user_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🚫 Enter User ID to ban and reason (e.g., `12345678 Spamming`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_ban_user)

def process_ban_user(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Ban cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "⚠️ Format: `user_id reason`\nExample: `12345678 Spamming`")
            return
        
        user_id = int(parts[0])
        reason = ' '.join(parts[1:])
        
        if user_id <= 0: raise ValueError("ID must be positive")
        if user_id == OWNER_ID: bot.reply_to(message, "⚠️ Cannot ban owner."); return
        if user_id in admin_ids: bot.reply_to(message, "⚠️ Cannot ban admin."); return
        
        if ban_user_db(user_id, reason, admin_id):
            bot.reply_to(message, f"✅ User `{user_id}` banned.\nReason: {reason}")
            # Stop all scripts for banned user
            for file_name, _ in user_files.get(user_id, []):
                script_key = f"{user_id}_{file_name}"
                if script_key in bot_scripts:
                    kill_process_tree(bot_scripts[script_key])
                    del bot_scripts[script_key]
            
            try:
                bot.send_message(user_id, f"🚫 You have been banned from using this bot.\nReason: {reason}")
            except Exception as e:
                logger.error(f"Failed to notify banned user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to ban user.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error banning user: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def unban_user_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✅ Enter User ID to unban\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_unban_user)

def process_unban_user(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Unban cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        if user_id not in banned_users:
            bot.reply_to(message, f"ℹ️ User `{user_id}` is not banned.")
            return
        
        if unban_user_db(user_id):
            bot.reply_to(message, f"✅ User `{user_id}` unbanned.")
            try:
                bot.send_message(user_id, "✅ Your ban has been lifted. You can now use the bot again.")
            except Exception as e:
                logger.error(f"Failed to notify unbanned user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to unban user.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error unbanning user: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def user_info_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👤 Enter User ID to get info\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_user_info)

def process_user_info(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Info request cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        # Gather user information
        info_parts = []
        
        # Basic info
        info_parts.append(f"👤 **User ID:** `{user_id}`")
        
        # Status
        if user_id == OWNER_ID:
            info_parts.append("👑 **Status:** Owner")
        elif user_id in admin_ids:
            info_parts.append("🛡️ **Status:** Admin")
        elif user_id in banned_users:
            info_parts.append("🚫 **Status:** Banned")
        elif user_id in user_subscriptions:
            expiry = user_subscriptions[user_id].get('expiry')
            if expiry and expiry > datetime.now():
                days_left = (expiry - datetime.now()).days
                info_parts.append(f"⭐ **Status:** Premium (Expires in {days_left} days)")
            else:
                info_parts.append("🆓 **Status:** Free User (Expired subscription)")
        else:
            info_parts.append("🆓 **Status:** Free User")
        
        # Files
        file_count = get_user_file_count(user_id)
        file_limit = get_user_file_limit(user_id)
        info_parts.append(f"📁 **Files:** {file_count}/{file_limit if file_limit != float('inf') else 'Unlimited'}")
        
        # Custom limit
        if user_id in user_limits:
            info_parts.append(f"⚙️ **Custom Limit:** {user_limits[user_id]}")
        
        # Active scripts
        running_scripts = 0
        for file_name, _ in user_files.get(user_id, []):
            if is_bot_running(user_id, file_name):
                running_scripts += 1
        info_parts.append(f"🤖 **Running Scripts:** {running_scripts}")
        
        # Last seen (if in active users)
        if user_id in active_users:
            info_parts.append("🟢 **Status:** Active")
        
        info_text = "\n".join(info_parts)
        bot.reply_to(message, info_text, parse_mode='Markdown')
        
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error getting user info: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def all_users_callback(call):
    bot.answer_callback_query(call.id)
    try:
        if not active_users:
            bot.edit_message_text("👥 No active users yet.", call.message.chat.id, call.message.message_id)
            return
        
        users_list = list(active_users)
        chunk_size = 20
        total_pages = (len(users_list) + chunk_size - 1) // chunk_size
        
        # Create pagination
        current_page = 0
        display_users_list(call.message.chat.id, call.message.message_id, users_list, current_page, total_pages, chunk_size)
        
    except Exception as e:
        logger.error(f"Error displaying all users: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error displaying users.", show_alert=True)

def display_users_list(chat_id, message_id, users_list, page, total_pages, chunk_size):
    start_idx = page * chunk_size
    end_idx = min(start_idx + chunk_size, len(users_list))
    
    user_chunk = users_list[start_idx:end_idx]
    
    message_text = f"👥 **Active Users** (Page {page + 1}/{total_pages})\n\n"
    for i, user_id in enumerate(user_chunk, start=start_idx + 1):
        status = ""
        if user_id == OWNER_ID: status = "👑"
        elif user_id in admin_ids: status = "🛡️"
        elif user_id in banned_users: status = "🚫"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            status = "⭐"
        else: status = "🆓"
        
        message_text += f"{i}. `{user_id}` {status}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    if total_pages > 1:
        page_buttons = []
        if page > 0:
            page_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"users_page_{page-1}"))
        
        page_buttons.append(types.InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        
        if page < total_pages - 1:
            page_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"users_page_{page+1}"))
        
        markup.row(*page_buttons)
    
    markup.row(types.InlineKeyboardButton("🔙 Back to User Management", callback_data='user_management'))
    
    try:
        bot.edit_message_text(message_text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error editing users list: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('users_page_'))
def handle_users_page(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    try:
        page = int(call.data.split('_')[2])
        users_list = list(active_users)
        chunk_size = 20
        total_pages = (len(users_list) + chunk_size - 1) // chunk_size
        
        if 0 <= page < total_pages:
            bot.answer_callback_query(call.id)
            display_users_list(call.message.chat.id, call.message.message_id, users_list, page, total_pages, chunk_size)
    except Exception as e:
        logger.error(f"Error handling users page: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def set_user_limit_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🔧 Enter User ID and new limit (e.g., `12345678 50`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_set_user_limit)

def process_set_user_limit(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Limit set cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError("Format: user_id limit")
        
        user_id = int(parts[0])
        limit = int(parts[1])
        
        if user_id <= 0 or limit <= 0: raise ValueError("ID and limit must be positive")
        
        if set_user_limit_db(user_id, limit, admin_id):
            bot.reply_to(message, f"✅ Set file limit {limit} for user `{user_id}`")
            try:
                bot.send_message(user_id, f"⚙️ Your file upload limit has been set to {limit}")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to set limit.")
            
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid input: {e}\nFormat: `user_id limit`")
    except Exception as e:
        logger.error(f"Error setting user limit: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def remove_user_limit_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🗑️ Enter User ID to remove custom limit\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_remove_user_limit)

def process_remove_user_limit(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Limit removal cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        if user_id not in user_limits:
            bot.reply_to(message, f"ℹ️ User `{user_id}` has no custom limit.")
            return
        
        if remove_user_limit_db(user_id):
            bot.reply_to(message, f"✅ Removed custom limit for user `{user_id}`")
            try:
                bot.send_message(user_id, "⚙️ Your custom file limit has been removed")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to remove limit.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error removing user limit: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

# --- Admin Settings Callbacks ---
def admin_settings_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("⚙️ Admin Settings\nSelect action:", call.message.chat.id, 
                              call.message.message_id, reply_markup=create_admin_settings_menu())
    except Exception as e: logger.error(f"Error showing admin settings: {e}")

def system_info_callback(call):
    bot.answer_callback_query(call.id)
    try:
        # Get system information
        import platform
        
        info_parts = []
        
        # Bot info
        info_parts.append("🤖 **Bot Information:**")
        info_parts.append(f"• Python: {platform.python_version()}")
        info_parts.append(f"• Platform: {platform.platform()}")
        info_parts.append(f"• Uptime: {time.strftime('%H:%M:%S', time.gmtime(time.time() - psutil.boot_time()))}")
        
        # System info
        info_parts.append("\n💻 **System Information:**")
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            info_parts.append(f"• CPU Usage: {cpu_percent}%")
            info_parts.append(f"• Memory: {memory.percent}% used ({memory.used//1024//1024}MB/{memory.total//1024//1024}MB)")
            info_parts.append(f"• Disk: {disk.percent}% used ({disk.used//1024//1024}MB/{disk.total//1024//1024}MB)")
        except Exception as e:
            info_parts.append(f"• System stats error: {str(e)}")
        
        # Bot stats
        info_parts.append("\n📊 **Bot Statistics:**")
        info_parts.append(f"• Active Users: {len(active_users)}")
        info_parts.append(f"• Running Scripts: {len(bot_scripts)}")
        info_parts.append(f"• Total Files: {sum(len(files) for files in user_files.values())}")
        info_parts.append(f"• Bot Status: {'🔒 Locked' if bot_locked else '🔓 Unlocked'}")
        
        info_text = "\n".join(info_parts)
        
        bot.edit_message_text(info_text, call.message.chat.id, call.message.message_id, 
                              reply_markup=create_admin_settings_menu(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing system info: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error showing system info.", show_alert=True)

def bot_performance_callback(call):
    bot.answer_callback_query(call.id)
    try:
        # Calculate performance metrics
        performance_parts = []
        
        # Script performance
        running_scripts = len(bot_scripts)
        total_files = sum(len(files) for files in user_files.values())
        
        performance_parts.append("📈 **Bot Performance Metrics:**")
        performance_parts.append(f"• Running Scripts: {running_scripts}")
        performance_parts.append(f"• Total Scripts: {total_files}")
        performance_parts.append(f"• Uptime Ratio: {running_scripts}/{total_files} ({running_scripts/total_files*100:.1f}% if total > 0)")
        
        # Resource usage
        try:
            bot_process = psutil.Process()
            memory_usage = bot_process.memory_info().rss / 1024 / 1024  # MB
            cpu_usage = bot_process.cpu_percent(interval=0.5)
            
            performance_parts.append(f"\n💾 **Resource Usage:**")
            performance_parts.append(f"• Memory: {memory_usage:.1f} MB")
            performance_parts.append(f"• CPU: {cpu_usage:.1f}%")
        except Exception as e:
            performance_parts.append(f"\n⚠️ Resource stats error: {str(e)}")
        
        # Database stats
        performance_parts.append(f"\n🗄️ **Database:**")
        performance_parts.append(f"• Active Users: {len(active_users)}")
        performance_parts.append(f"• Subscriptions: {len(user_subscriptions)}")
        performance_parts.append(f"• Banned Users: {len(banned_users)}")
        performance_parts.append(f"• Custom Limits: {len(user_limits)}")
        
        performance_text = "\n".join(performance_parts)
        
        bot.edit_message_text(performance_text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_settings_menu(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing performance: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error showing performance.", show_alert=True)

def cleanup_files_callback(call):
    bot.answer_callback_query(call.id, "🧹 Cleaning up temporary files...")
    
    try:
        # Clean up empty user directories
        cleaned_dirs = 0
        cleaned_files = 0
        
        for user_dir in os.listdir(UPLOAD_BOTS_DIR):
            user_path = os.path.join(UPLOAD_BOTS_DIR, user_dir)
            if os.path.isdir(user_path):
                # Check if directory is empty
                if not os.listdir(user_path):
                    try:
                        os.rmdir(user_path)
                        cleaned_dirs += 1
                    except Exception as e:
                        logger.error(f"Error removing empty dir {user_path}: {e}")
                
                # Clean old log files (older than 7 days)
                else:
                    for file_name in os.listdir(user_path):
                        if file_name.endswith('.log'):
                            file_path = os.path.join(user_path, file_name)
                            try:
                                file_age = time.time() - os.path.getmtime(file_path)
                                if file_age > 7 * 24 * 3600:  # 7 days
                                    os.remove(file_path)
                                    cleaned_files += 1
                            except Exception as e:
                                logger.error(f"Error cleaning log file {file_path}: {e}")
        
        result_msg = f"🧹 **Cleanup Complete:**\n• Removed empty directories: {cleaned_dirs}\n• Cleared old log files: {cleaned_files}"
        
        bot.edit_message_text(result_msg, call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_settings_menu(), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        bot.edit_message_text(f"❌ Cleanup error: {str(e)}", call.message.chat.id, call.message.message_id)

# install_logs_callback is provided by the MongoDB implementation appended at startup.

def admin_install_callback(call):
    bot.answer_callback_query(call.id)
    _logic_admin_install(call.message)

# --- Mandatory Channels Callbacks ---
def manage_mandatory_channels_callback(call):
    """Handle mandatory channels management request"""
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("📢 Manage Mandatory Channels\nChoose desired action:",
                              call.message.chat.id, call.message.message_id, 
                              reply_markup=create_mandatory_channels_menu())
    except Exception as e:
        logger.error(f"Error showing channel management menu: {e}")

def add_mandatory_channel_callback(call):
    """Add new mandatory channel"""
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send channel ID or username (example: @channel_username or -1001234567890)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_add_channel)

def process_add_channel(message):
    """Process channel addition"""
    admin_id = message.from_user.id
    if admin_id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
        
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Channel addition cancelled.")
        return
        
    channel_identifier = message.text.strip()
    
    try:
        # Get channel info
        chat = bot.get_chat(channel_identifier)
        channel_id = str(chat.id)
        channel_username = f"@{chat.username}" if chat.username else ""
        channel_name = chat.title
        
        # Ensure bot is admin in the channel
        try:
            bot_member = bot.get_chat_member(channel_id, bot.get_me().id)
            if bot_member.status not in ['administrator', 'creator']:
                bot.reply_to(message, f"❌ Bot is not admin in the channel! Must be promoted first.")
                return
        except Exception as e:
            bot.reply_to(message, f"❌ Bot is not admin in the channel or cannot access it!")
            return
            
        # Save channel to database
        if save_mandatory_channel(channel_id, channel_username, channel_name, admin_id):
            bot.reply_to(message, f"✅ Mandatory channel added:\n**{channel_name}**\n{channel_username or channel_id}")
        else:
            bot.reply_to(message, "❌ Failed to add channel. Try again.")
            
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        bot.reply_to(message, f"❌ Error adding channel: {str(e)}")

def remove_mandatory_channel_callback(call):
    """Remove mandatory channel"""
    if not mandatory_channels:
        bot.answer_callback_query(call.id, "❌ No mandatory channels.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup()
    for channel_id, channel_info in mandatory_channels.items():
        channel_name = channel_info.get('name', 'Unknown')
        button_text = f"🗑️ {channel_name}"
        markup.add(types.InlineKeyboardButton(button_text, callback_data=f'remove_channel_{channel_id}'))
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='manage_mandatory_channels'))
    
    try:
        bot.edit_message_text("📢 Choose channel to delete:",
                              call.message.chat.id, call.message.message_id, 
                              reply_markup=markup)
    except Exception as e:
        logger.error(f"Error showing remove channel menu: {e}")

def process_remove_channel(call):
    """Process channel removal"""
    channel_id = call.data.replace('remove_channel_', '')
    
    if channel_id in mandatory_channels:
        channel_name = mandatory_channels[channel_id].get('name', 'Unknown')
        if remove_mandatory_channel_db(channel_id):
            bot.answer_callback_query(call.id, f"✅ Channel deleted: {channel_name}")
            try:
                bot.edit_message_text(f"✅ Mandatory channel deleted: **{channel_name}**",
                                      call.message.chat.id, call.message.message_id,
                                      reply_markup=create_mandatory_channels_menu(), parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Error updating message after channel removal: {e}")
        else:
            bot.answer_callback_query(call.id, "❌ Failed to delete channel.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "❌ Channel not found.", show_alert=True)

def list_mandatory_channels_callback(call):
    """Show list of mandatory channels"""
    bot.answer_callback_query(call.id)
    
    if not mandatory_channels:
        message_text = "📢 **No mandatory channels currently**"
    else:
        message_text = "📢 **Mandatory Channels:**\n\n"
        for channel_id, channel_info in mandatory_channels.items():
            channel_name = channel_info.get('name', 'Unknown')
            channel_username = channel_info.get('username', 'No username')
            message_text += f"• **{channel_name}**\n  {channel_username or channel_id}\n\n"
    
    try:
        bot.edit_message_text(message_text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_mandatory_channels_menu(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error listing channels: {e}")

def check_subscription_status_callback(call):
    """Check subscription status"""
    user_id = call.from_user.id
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    
    if is_subscribed or user_id in admin_ids:
        bot.answer_callback_query(call.id, "✅ You are subscribed to all required channels!", show_alert=True)
        # Show main menu
        try:
            _logic_send_welcome(call.message)
        except Exception as callback_error:
            logger.debug("Welcome callback fallback: %s", callback_error)
            back_to_main_callback(call)
    else:
        bot.answer_callback_query(call.id, "❌ You haven't joined all required channels yet!", show_alert=True)
        # Update the subscription message
        subscription_message, markup = create_subscription_check_message(not_joined)
        try:
            bot.edit_message_text(subscription_message, call.message.chat.id, 
                                  call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error updating subscription message: {e}")

# --- Security Approval Callbacks ---
def process_approve_file(call):
    """Process admin approval for file"""
    data_parts = call.data.split('_')
    if len(data_parts) < 4:
        bot.answer_callback_query(call.id, "❌ Invalid data.", show_alert=True)
        return
        
    user_id = int(data_parts[2])
    file_name = '_'.join(data_parts[3:])
    
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    
    if not os.path.exists(file_path):
        bot.answer_callback_query(call.id, "❌ File not found.", show_alert=True)
        return
    
    file_ext = os.path.splitext(file_name)[1].lower()
    
    try:
        # Process the approved file
        if file_ext == '.js':
            handle_js_file(file_path, user_id, user_folder, file_name, call.message)
        elif file_ext == '.py':
            handle_py_file(file_path, user_id, user_folder, file_name, call.message)
        
        bot.answer_callback_query(call.id, "✅ File approved!")
        bot.edit_message_text(f"✅ File `{file_name}` approved for user `{user_id}`",
                              call.message.chat.id, call.message.message_id)
        
        # Notify user
        try:
            bot.send_message(user_id, f"✅ Your file `{file_name}` has been approved and started.")
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}")
            
    except Exception as e:
        logger.error(f"Error processing approved file: {e}")
        bot.answer_callback_query(call.id, "❌ Error processing file.", show_alert=True)

def process_reject_file(call):
    """Process admin rejection for file"""
    data_parts = call.data.split('_')
    if len(data_parts) < 4:
        bot.answer_callback_query(call.id, "❌ Invalid data.", show_alert=True)
        return
        
    user_id = int(data_parts[2])
    file_name = '_'.join(data_parts[3:])
    
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    
    # Delete the file
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Error deleting rejected file: {e}")
    
    bot.answer_callback_query(call.id, "❌ File rejected!")
    bot.edit_message_text(f"❌ File `{file_name}` rejected for user `{user_id}`",
                          call.message.chat.id, call.message.message_id)
    
    # Notify user
    try:
        bot.send_message(user_id, f"❌ Your file `{file_name}` has been rejected for security reasons.")
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")

def process_approve_zip(call):
    """Process admin approval for ZIP file"""
    data_parts = call.data.split('_')
    if len(data_parts) < 4:
        bot.answer_callback_query(call.id, "❌ Invalid data.", show_alert=True)
        return
        
    user_id = int(data_parts[2])
    file_name = '_'.join(data_parts[3:])
    
    # Check if we have stored file content
    if user_id in pending_zip_files and file_name in pending_zip_files[user_id]:
        file_content = pending_zip_files[user_id][file_name]
        user_folder = get_user_folder(user_id)
        temp_dir = None
        
        try:
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_approve_")
            zip_path = os.path.join(temp_dir, file_name)
            
            # Save the file content
            with open(zip_path, 'wb') as f:
                f.write(file_content)
            
            # Process the ZIP file
            process_zip_file(zip_path, user_id, user_folder, file_name, call.message, temp_dir)
            
            # Clean up pending files
            if user_id in pending_zip_files and file_name in pending_zip_files[user_id]:
                del pending_zip_files[user_id][file_name]
                if not pending_zip_files[user_id]:
                    del pending_zip_files[user_id]
            
            bot.answer_callback_query(call.id, "✅ Archive approved!")
            bot.edit_message_text(f"✅ Archive `{file_name}` approved for user `{user_id}`",
                                  call.message.chat.id, call.message.message_id)
            
            # Notify user
            try:
                bot.send_message(user_id, f"✅ Your archive `{file_name}` has been approved and processed.")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
                
        except Exception as e:
            logger.error(f"Error processing approved zip: {e}", exc_info=True)
            bot.answer_callback_query(call.id, "❌ Error processing archive.", show_alert=True)
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"Error cleaning temp dir: {e}")
    else:
        bot.answer_callback_query(call.id, "❌ File content not found. Ask user to re-upload.", show_alert=True)

def process_reject_zip(call):
    """Process admin rejection for ZIP file"""
    data_parts = call.data.split('_')
    if len(data_parts) < 4:
        bot.answer_callback_query(call.id, "❌ Invalid data.", show_alert=True)
        return
        
    user_id = int(data_parts[2])
    file_name = '_'.join(data_parts[3:])
    
    # Clean up pending files
    if user_id in pending_zip_files and file_name in pending_zip_files[user_id]:
        del pending_zip_files[user_id][file_name]
        if not pending_zip_files[user_id]:
            del pending_zip_files[user_id]
    
    bot.answer_callback_query(call.id, "❌ Archive rejected!")
    bot.edit_message_text(f"❌ Archive `{file_name}` rejected for user `{user_id}`",
                          call.message.chat.id, call.message.message_id)
    
    try:
        bot.send_message(user_id, f"❌ Your archive `{file_name}` has been rejected for security reasons.")
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")

# ============================================================
# INSTALL LOG QUERY
# ============================================================
# Replace the old SQLite-backed callback implementation with MongoDB data.
def _mongo_install_logs_callback(call):
    bot.answer_callback_query(call.id)
    try:
        logs=mongo.call(self_db.install_logs.find({}, {"user_id":1,"module_name":1,"package_name":1,"status":1,"install_date":1}).sort("install_date",-1).limit(20).to_list(20))
        if not logs:
            bot.edit_message_text("📋 No installation logs found",call.message.chat.id,call.message.message_id,reply_markup=create_admin_settings_menu()); return
        text="📋 Recent Installation Logs (Last 20):\n\n"
        for d in logs:
            icon="✅" if d.get("status")=="success" else "❌" if d.get("status")=="failed" else "⚠️"
            text+=f"{icon} `{d.get('user_id')}`: {d.get('module_name')} -> {d.get('package_name')}\n   📅 {str(d.get('install_date',''))[:19]}\n\n"
        bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=create_admin_settings_menu(),parse_mode="Markdown")
    except Exception as e:
        report_exception(e,"install_logs_callback",call.from_user.id,"INSTALL_LOGS"); bot.answer_callback_query(call.id,"Error showing logs.",show_alert=True)

# The original callback is intentionally replaced by the MongoDB implementation.
install_logs_callback=_mongo_install_logs_callback

# ============================================================
# PROCESS MONITOR / RECOVERY
# ============================================================
def _restart_allowed(script_key):
    now=time.time(); history=[t for t in restart_history.get(script_key,[]) if now-t<RESTART_WINDOW_SECONDS]
    if len(history)>=MAX_RESTARTS:
        restart_history[script_key]=history; return False
    history.append(now); restart_history[script_key]=history; return True

def monitor_hosted_bots():
    while not shutdown_event.wait(MONITOR_INTERVAL):
        for key,info in list(bot_scripts.items()):
            try:
                proc=info.get("process")
                if not proc: continue
                rc=proc.poll()
                if rc is None: continue
                owner=info.get("script_owner_id"); name=info.get("file_name"); ftype=info.get("type","py")
                try:
                    if info.get("log_file") and not info["log_file"].closed: info["log_file"].flush()
                except Exception: pass
                send_log(f"File: {name}\nExit code: {rc}","ERROR",owner,"BOT_CRASH")
                mongo.call(self_db.hosted_bots.update_one({"user_id":owner,"file_name":name},{"$set":{"status":"crashed","last_exit_code":rc,"last_error":f"Process exited with {rc}","updated_at":datetime.utcnow().isoformat()}},upsert=True))
                bot_scripts.pop(key,None)
                if not _restart_allowed(key):
                    send_log(f"File: {name}\nAutomatic restart disabled after repeated crashes.","ERROR",owner,"AUTO_RESTART_BLOCKED"); continue
                folder=info.get("user_folder") or get_user_folder(owner); path=os.path.join(folder,name)
                if not os.path.exists(path):
                    rec=mongo.call(self_db.files.find_one({"user_id":owner,"file_name":name}))
                    if rec and rec.get("database_message_id"):
                        recover_file_from_database_channel(rec,path)
                if os.path.exists(path):
                    target=run_script if ftype=="py" else run_js_script
                    dummy=SimpleNamespace(chat=SimpleNamespace(id=owner),from_user=SimpleNamespace(id=owner),message_id=0)
                    threading.Thread(target=target,args=(path,owner,folder,name,dummy),daemon=True).start()
                    send_log(f"File: {name}","WARNING",owner,"AUTO_RESTART")
            except Exception as e: report_exception(e,"monitor_hosted_bots",None,"MONITOR")

def recover_hosted_bots():
    """Recover bots using the Mongo worker loop, never sharing Motor objects across loops."""
    try:
        records=mongo.call(self_db.hosted_bots.find({"status":{"$in":["running","starting","crashed"]}}).to_list(None))
        for rec in records:
            uid=rec.get("user_id"); name=rec.get("file_name"); ftype=rec.get("file_type","py")
            if uid is None or not name: continue
            folder=get_user_folder(uid); path=os.path.join(folder,name)
            file_rec=mongo.call(self_db.files.find_one({"user_id":uid,"file_name":name}))
            if not os.path.exists(path) and file_rec:
                recover_file_from_database_channel(file_rec,path)
            if not os.path.exists(path):
                mongo.call(self_db.hosted_bots.update_one({"_id":rec["_id"]},{"$set":{"status":"recovery_failed","updated_at":datetime.utcnow().isoformat()}}))
                continue
            dummy=SimpleNamespace(chat=SimpleNamespace(id=uid),from_user=SimpleNamespace(id=uid),message_id=0)
            target=run_script if ftype=="py" else run_js_script
            threading.Thread(target=target,args=(path,uid,folder,name,dummy),daemon=True).start()
            mongo.call(self_db.hosted_bots.update_one({"_id":rec["_id"]},{"$set":{"status":"starting","updated_at":datetime.utcnow().isoformat()}}))
            send_log(f"File: {name}","INFO",uid,"STARTUP_RECOVERY")
    except Exception as e:
        report_exception(e,"recover_hosted_bots",None,"STARTUP_RECOVERY")

def background_health():
    """Periodic MongoDB health check on the Mongo worker loop."""
    while not shutdown_event.wait(300):
        try:
            mongo.call(mongo.client.admin.command("ping"), timeout=20)
        except Exception as e:
            logger.warning("MongoDB health check failed: %s", e)

# ============================================================
# GLOBAL EXCEPTION HOOKS
# ============================================================
def _sys_excepthook(exc_type,exc_value,exc_tb):
    logger.critical("Unhandled exception",exc_info=(exc_type,exc_value,exc_tb))
    try: send_log(f"Type: {exc_type.__name__}\nMessage: {exc_value}\n\nTraceback:\n{''.join(traceback.format_exception(exc_type,exc_value,exc_tb))[-6000:]}","CRITICAL",None,"GLOBAL")
    except Exception: pass
sys.excepthook=_sys_excepthook

def cleanup():
    shutdown_event.set()
    for key,info in list(bot_scripts.items()):
        try: kill_process_tree(info)
        except Exception: pass
    try: mongo.close()
    except Exception: pass
atexit.register(cleanup)

# ============================================================
# STARTUP
# ============================================================
async def main():
    register_handlers()
    keep_alive()
    monitor_thread=threading.Thread(target=monitor_hosted_bots,name="hosted-bot-monitor",daemon=True); monitor_thread.start()
    await pyro_client.start()
    me=await pyro_client.get_me()
    logger.info("Hosting bot started as @%s",me.username)
    send_log(f"Bot: @{me.username}\nPython: {sys.version.split()[0]}\nUsers: {len(active_users)}\nAdmins: {len(admin_ids)}", "INFO", None, "STARTUP")
    recover_hosted_bots()
    threading.Thread(target=background_health, name="mongo-health", daemon=True).start()
    from pyrogram import idle
    await idle()

if __name__=="__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
