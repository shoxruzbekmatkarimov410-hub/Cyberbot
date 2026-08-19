import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# Sozlamalarni yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 8080))  # Render ajratadigan port

DB_NAME = "cybercode.db"

# ----------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referrer_id INTEGER,
            ref_count INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            vip_expire_time TEXT,
            joined_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id, referrer_id=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    
    reward_referrer = None
    if not cursor.fetchone():
        today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO users (user_id, referrer_id, joined_date) VALUES (?, ?, ?)",
                       (user_id, referrer_id, today))
        
        if referrer_id and referrer_id != user_id:
            cursor.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?", (referrer_id,))
            cursor.execute("SELECT ref_count, is_vip FROM users WHERE user_id = ?", (referrer_id,))
            res = cursor.fetchone()
            if res and res[0] >= 5 and res[1] == 0:
                vip_until = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("UPDATE users SET is_vip = 1, vip_expire_time = ? WHERE user_id = ?",
                               (vip_until, referrer_id))
                reward_referrer = referrer_id

        conn.commit()
    conn.close()
    return reward_referrer

def get_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count, is_vip, vip_expire_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def check_and_expire_vips():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT user_id FROM users WHERE is_vip = 1 AND vip_expire_time <= ?", (now_str,))
    expired = [u[0] for u in cursor.fetchall()]
    for u_id in expired:
        cursor.execute("UPDATE users SET is_vip = 0, vip_expire_time = NULL WHERE user_id = ?", (u_id,))
    conn.commit()
    conn.close()
    return expired

def get_admin_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_start = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_date LIKE ?", (f"{today_start}%",))
    today_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1")
    active_vips = cursor.fetchone()[0]
    conn.close()
    return total_users, today_users, active_vips

# ----------------- BOT LOGIC -----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💻 Commands Base"), KeyboardButton(text="🤖 AI Search")],
        [KeyboardButton(text="👥 Referal & VIP"), KeyboardButton(text="👨‍💻 Admin bilan aloqa")]
    ],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    rewarded_user = add_user(message.from_user.id, referrer_id)
    
    if rewarded_user:
        try:
            await bot.send_message(
                rewarded_user,
                "🎉 **Tabriklaymiz!** siz 5 ta do'st taklif qildingiz va sizga **1 kunlik VIP** status avtomatik berildi! 🔥"
            )
        except Exception:
            pass

    await message.answer(
        f"Xush kelibsiz, **{message.from_user.first_name}**!\n"
        f"`Cybercode` botiga hush kelibsiz. Bo'limlardan birini tanlang:",
        reply_markup=main_kb,
        parse_mode="Markdown"
    )

@dp.message(F.text == "👥 Referal & VIP")
async def ref_handler(message: types.Message):
    u_data = get_user_data(message.from_user.id)
    ref_count = u_data[0] if u_data else 0
    is_vip = "Faol 🟢" if u_data and u_data[1] == 1 else "No-faol 🔴"
    
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    
    msg = (
        f"<b>👥 Referal Tizimi:</b>\n\n"
        f"Sizning taklif qilgan do'stlaringiz: <b>{ref_count} / 5</b> ta\n"
        f"VIP Statusingiz: <b>{is_vip}</b>\n\n"
        f"🎁 <b>5 ta do'st</b> taklif qiling va <b>1 kunlik VIP</b> statusini bepul qo'lga kiriting!\n\n"
        f"🔗 Sizning taklif havolangiz:\n<code>{ref_link}</code>"
    )
    await message.answer(msg, parse_mode="HTML")

@dp.message(F.text == "👨‍💻 Admin bilan aloqa")
async def contact_admin(message: types.Message):
    await message.answer("Sizni qiziqtirgan savol yoki taklifni yozing. Admin tez orada javob beradi:")

@dp.message(F.reply_to_message & (F.from_user.id == ADMIN_ID))
async def admin_reply(message: types.Message):
    if message.reply_to_message.forward_from:
        user_id = message.reply_to_message.forward_from.id
        await bot.send_message(user_id, f"<b>👨‍💻 Admin Javobi:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("Javob foydalanuvchiga yuborildi! ✅")

@dp.message(F.chat.type == "private")
async def forward_to_admin(message: types.Message):
    if message.text in ["💻 Commands Base", "🤖 AI Search", "👥 Referal & VIP", "👨‍💻 Admin bilan aloqa"]:
        return
    if message.from_user.id != ADMIN_ID:
        await bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        await message.answer("Xabaringiz adminga yetkazildi! 📥")

# ----------------- SCHEDULER JOBS -----------------
async def daily_report_job():
    if ADMIN_ID:
        total, today, vips = get_admin_stats()
        report = (
            f"📊 **Kunlik Bot Statistikasi:**\n\n"
            f"👤 Jami foydalanuvchilar: **{total}** ta\n"
            f"🆕 Bugun qo'shilganlar: **{today}** ta\n"
            f"💎 Faol VIP foydalanuvchilar: **{vips}** ta"
        )
        try:
            await bot.send_message(ADMIN_ID, report, parse_mode="Markdown")
        except Exception:
            pass

async def check_vip_expiry_job():
    expired_users = check_and_expire_vips()
    for u_id in expired_users:
        try:
            await bot.send_message(u_id, "⏳ Sizning 1 kunlik VIP statusingiz muddati tugadi.")
        except Exception:
            pass

# ----------------- DUMMY WEB SERVER FOR RENDER FREE PLAN -----------------
async def handle_ping(request):
    return web.Response(text="Bot is running live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# ----------------- MAIN RUNNER -----------------
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    # Render Port uchun Veb-serverni yoqish
    await start_web_server()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_report_job, 'cron', hour=0, minute=0)
    scheduler.add_job(check_vip_expiry_job, 'interval', minutes=10)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
