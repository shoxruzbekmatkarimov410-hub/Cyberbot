import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web
from groq import Groq

# Sozlamalarni yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")  # Kalit koddansiz, Render Environment Variables'dan olinadi
PORT = int(os.getenv("PORT", 8080))

groq_client = Groq(api_key=GROQ_API_KEY)
DB_NAME = "cybercode.db"

class AIState(StatesGroup):
    waiting_for_custom_cmd = State()

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
            daily_limit INTEGER DEFAULT 5,
            last_request_date TEXT,
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
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    if not cursor.fetchone():
        today_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO users (user_id, referrer_id, daily_limit, last_request_date, joined_date) VALUES (?, ?, 5, ?, ?)",
            (user_id, referrer_id, today_date, today_time)
        )
        
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
    cursor.execute("SELECT ref_count, is_vip, vip_expire_time, daily_limit, last_request_date FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def check_and_update_limit(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute("SELECT is_vip, daily_limit, last_request_date FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    if not res:
        conn.close()
        return False, "User not found"
        
    is_vip, daily_limit, last_date = res
    
    if is_vip == 1:
        conn.close()
        return True, "VIP"
        
    if last_date != today_date:
        daily_limit = 5
        cursor.execute("UPDATE users SET daily_limit = 5, last_request_date = ? WHERE user_id = ?", (today_date, user_id))
        conn.commit()
        
    if daily_limit > 0:
        cursor.execute("UPDATE users SET daily_limit = daily_limit - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True, daily_limit - 1
    else:
        conn.close()
        return False, 0

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

categories_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📱 Termux", callback_data="cat_termux"), InlineKeyboardButton(text="🐉 Kali Linux", callback_data="cat_kali")],
    [InlineKeyboardButton(text="🐧 Linux OS", callback_data="cat_linux"), InlineKeyboardButton(text="❓ Boshqa (AI)", callback_data="cat_ai")]
])

PRESET_COMMANDS = {
    "termux": {
        "1. Paketlarni yangilash": "pkg update && pkg upgrade -y\nℹ️ Barcha o'rnatilgan paketlarni so'nggi versiyaga o'tkazadi.",
        "2. Storage ruxsati berish": "termux-setup-storage\nℹ️ Xotiraga kirish ruxsatini beradi.",
        "3. Python o'rnatish": "pkg install python -y\nℹ️ Python muhitini o'rnatadi.",
        "4. Git o'rnatish": "pkg install git -y\nℹ️ GitHub loyihalarini yuklash uchun Git o'rnatadi."
    },
    "kali": {
        "1. Nmap port skaner": "nmap -sV target_ip\nℹ️ Nishondagi ochiq portlarni skanerlaydi.",
        "2. Metasploit console": "msfconsole\nℹ️ Eksploit konsolini ochadi.",
        "3. SQLMap skaner": "sqlmap -u 'http://site.com/page.php?id=1' --dbs\nℹ️ SQL Injection zaifligini va Baza ma'lumotlarini aniqlaydi."
    },
    "linux": {
        "1. Tizim resurslari": "htop\nℹ️ Process va RAM ishlatilishini ko'rsatadi.",
        "2. Root huquqi": "sudo su\nℹ️ Tizimda to'liq administrator huquqini oladi.",
        "3. Portlarni tekshirish": "netstat -tulnp\nℹ️ Barcha aktiv tarmoq portlarini ko'rsatadi."
    }
}

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    rewarded_user = add_user(message.from_user.id, referrer_id)
    
    if rewarded_user:
        try:
            await bot.send_message(
                rewarded_user,
                "🎉 **Tabriklaymiz!** 5 ta do'st taklif qildingiz va sizga **1 kunlik VIP (Cheksiz limit)** status berildi! 🔥"
            )
        except Exception:
            pass

    await message.answer(
        f"Xush kelibsiz, **{message.from_user.first_name}**!\n"
        f"`Cybercode` terminaliga xush kelibsiz. Bo'limlardan birini tanlang:",
        reply_markup=main_kb,
        parse_mode="Markdown"
    )

@dp.message(F.text == "💻 Commands Base")
async def commands_base_handler(message: types.Message):
    await message.answer("📌 Qaysi Operatsion Tizim bo'yicha buyruqlar kerak?", reply_markup=categories_kb)

@dp.callback_query(F.data.startswith("cat_"))
async def process_category(call: CallbackQuery, state: FSMContext):
    cat = call.data.split("_")[1]
    if cat == "ai":
        await state.set_state(AIState.waiting_for_custom_cmd)
        await call.message.edit_text("⚙️ Sizga qanday maxsus buyruq yoki skript kerak? Batafsil yozing:", parse_mode="Markdown")
        return

    cmds = PRESET_COMMANDS.get(cat, {})
    buttons = [[InlineKeyboardButton(text=title, callback_data=f"cmd_{cat}_{title[:10]}")] for title in cmds.keys()]
    buttons.append([InlineKeyboardButton(text="❓ Ro'yxatda yo'qmi? AI'dan so'rash", callback_data="cat_ai")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_cats")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text(f"🛠 **{cat.upper()}** buyruqlar bazasi:", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_cats")
async def back_cats(call: CallbackQuery):
    await call.message.edit_text("📌 Qaysi Operatsion Tizim bo'yicha buyruqlar kerak?", reply_markup=categories_kb)

@dp.callback_query(F.data.startswith("cmd_"))
async def show_command_detail(call: CallbackQuery):
    parts = call.data.split("_")
    cat, title_start = parts[1], parts[2]
    
    for title, detail in PRESET_COMMANDS.get(cat, {}).items():
        if title.startswith(title_start):
            msg = f"📌 **{title}**\n\n```bash\n{detail}\n```"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❓ Boshqa buyruq (AI)", callback_data="cat_ai")],
                [InlineKeyboardButton(text="⬅️ Ro'yxatga qaytish", callback_data=f"cat_{cat}")]
            ])
            await call.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")
            break

@dp.message(AIState.waiting_for_custom_cmd)
async def process_ai_custom_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    
    allowed, remaining = check_and_update_limit(message.from_user.id)
    
    if not allowed:
        await message.answer(
            "⚠️ **Kunlik AI so'rovi limiti tugadi!**\n\n"
            "Bugungi 5 ta tekin so'rovdan foydalanib bo'ldingiz.\n"
            "🔥 **Cheksiz so'rovlar** uchun 5 ta do'stingizni taklif qiling va **1 kunlik VIP** oling!",
            parse_mode="Markdown"
        )
        return

    status_msg = await message.answer("⚡ *Terminal ma'lumot qidirmoqda...*", parse_mode="Markdown")
    
    system_prompt = (
        "Siz 'Cybercode' tizimining White Hat xakerisiz (Pentester). "
        "Foydalanuvchi so'ragan buyruq yoki skriptni o'zbek tilida, professional va aniq ko'rsatib bering. "
        "O'zingizni AI deb aytmang, 'Men White Hat Pentesterman' deb do'stona va xushmuomala muloqot qiling."
    )
    
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.text}
            ],
            temperature=0.6,
            max_tokens=1024
        )
        
        limit_text = f"\n\n_ℹ️ Bugungi qolgan so'rovlaringiz: {remaining} ta_" if remaining != "VIP" else "\n\n_💎 VIP Status (Cheksiz so'rovlar)_"
        await status_msg.edit_text(completion.choices[0].message.content + limit_text, parse_mode="Markdown")
    except Exception:
        await status_msg.edit_text("❌ Aloqa uzildi. Bir ozdan so'ng qayta urinib ko'ring.")

@dp.message(F.text == "🤖 AI Search")
async def ai_search_info(message: types.Message, state: FSMContext):
    await state.set_state(AIState.waiting_for_custom_cmd)
    await message.answer("🧠 Menga istalgan kiberxavfsizlik, dasturlash yoki Linux/Termux bo'yicha savolingizni yozing:")

@dp.message(F.text == "👥 Referal & VIP")
async def ref_handler(message: types.Message):
    u_data = get_user_data(message.from_user.id)
    ref_count = u_data[0] if u_data else 0
    is_vip = "Faol 🟢 (Cheksiz)" if u_data and u_data[1] == 1 else "No-faol 🔴 (Kunlik 5 ta)"
    rem_limit = u_data[3] if u_data else 5
    
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    
    msg = (
        f"<b>👥 Referal & Limit Tizimi:</b>\n\n"
        f"Sizning taklif qilgan do'stlaringiz: <b>{ref_count} / 5</b> ta\n"
        f"VIP Statusingiz: <b>{is_vip}</b>\n"
        f"Bugungi qolgan AI limit: <b>{rem_limit}</b> ta\n\n"
        f"🎁 <b>5 ta do'st</b> taklif qiling va <b>1 kunlik VIP (Cheksiz)</b> statusini bepul qo'lga kiriting!\n\n"
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

# ----------------- SCHEDULER & SERVER -----------------
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
            await bot.send_message(u_id, "⏳ Sizning 1 kunlik VIP statusingiz muddati tugadi. Kunlik limit: 5 ta.")
        except Exception:
            pass

async def handle_ping(request):
    return web.Response(text="Bot is running live with Groq AI!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await start_web_server()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_report_job, 'cron', hour=0, minute=0)
    scheduler.add_job(check_vip_expiry_job, 'interval', minutes=10)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
