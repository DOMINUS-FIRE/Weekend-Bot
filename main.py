import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv


# Загружаем TOKEN.env
load_dotenv("TOKEN.env")

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

MODERATOR_IDS = {
    int(x.strip())
    for x in os.getenv("MODERATOR_IDS", "").split(",")
    if x.strip().isdigit()
}

DB_NAME = "bot.db"

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

BOT_USERNAME = ""


class ApplicationState(StatesGroup):
    waiting_application = State()


def db_connect():
    return sqlite3.connect(DB_NAME)


def init_db():
    with db_connect() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            referrer_id INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            referrer_id INTEGER,
            created_at TEXT
        )
        """)

        conn.commit()


def get_user(user_id: int):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, username, full_name, referrer_id, created_at FROM users WHERE user_id = ?",
            (user_id,)
        )
        return cur.fetchone()


def save_or_update_user(user_id: int, username: str | None, full_name: str, referrer_id: int | None):
    existing_user = get_user(user_id)

    with db_connect() as conn:
        cur = conn.cursor()

        if existing_user is None:
            cur.execute("""
            INSERT INTO users (user_id, username, full_name, referrer_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                username,
                full_name,
                referrer_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
        else:
            # Рефералка НЕ меняется после первого запуска,
            # чтобы нельзя было перезаписать, от кого пришёл человек
            cur.execute("""
            UPDATE users
            SET username = ?, full_name = ?
            WHERE user_id = ?
            """, (
                username,
                full_name,
                user_id
            ))

        conn.commit()


def save_application(user_id: int, text: str, referrer_id: int | None):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO applications (user_id, text, referrer_id, created_at)
        VALUES (?, ?, ?, ?)
        """, (
            user_id,
            text,
            referrer_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()


def get_referral_count(moderator_id: int) -> int:
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id = ?",
            (moderator_id,)
        )
        return cur.fetchone()[0]


def get_referrer_info(referrer_id: int | None):
    if referrer_id is None:
        return None

    user = get_user(referrer_id)

    if user is None:
        return {
            "id": referrer_id,
            "username": None,
            "full_name": None
        }

    return {
        "id": user[0],
        "username": user[1],
        "full_name": user[2]
    }


def main_keyboard(user_id: int):
    buttons = [
        [KeyboardButton(text="📝 Написать заявку")]
    ]

    if user_id in MODERATOR_IDS:
        buttons.append([KeyboardButton(text="📊 Реферальная программа")])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )


def parse_referrer_from_start(message: Message) -> int | None:
    """
    Ссылка будет такая:
    https://t.me/ТВОЙ_БОТ?start=ref_123456789
    """

    if not message.text:
        return None

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        return None

    payload = parts[1].strip()

    if not payload.startswith("ref_"):
        return None

    ref_id_text = payload.replace("ref_", "", 1)

    if not ref_id_text.isdigit():
        return None

    referrer_id = int(ref_id_text)

    # Засчитываем только если это реально модератор
    if referrer_id not in MODERATOR_IDS:
        return None

    # Нельзя привести самого себя
    if message.from_user and referrer_id == message.from_user.id:
        return None

    return referrer_id


def format_user_link(user_id: int, username: str | None, full_name: str | None):
    safe_name = html.escape(full_name or "Без имени")

    if username:
        return f"{safe_name} (@{html.escape(username)})\nID: <code>{user_id}</code>"

    return f'<a href="tg://user?id={user_id}">{safe_name}</a>\nID: <code>{user_id}</code>'


def format_referrer_text(referrer_id: int | None):
    if referrer_id is None:
        return "Без реферальной ссылки"

    ref = get_referrer_info(referrer_id)

    if not ref:
        return "Без реферальной ссылки"

    ref_name = ref.get("full_name") or "Модератор"
    ref_username = ref.get("username")

    if ref_username:
        return f"{html.escape(ref_name)} (@{html.escape(ref_username)})\nID модератора: <code>{referrer_id}</code>"

    return f"{html.escape(ref_name)}\nID модератора: <code>{referrer_id}</code>"


@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    referrer_id = parse_referrer_from_start(message)

    save_or_update_user(
        user_id=user_id,
        username=username,
        full_name=full_name,
        referrer_id=referrer_id
    )

    await state.set_state(ApplicationState.waiting_application)

    text = (
        "Здравствуйте!\n\n"
        "Напишите вашу заявку одним сообщением, и она будет отправлена администратору."
    )

    if user_id in MODERATOR_IDS:
        text += (
            "\n\nВы являетесь модератором. "
            "В меню вам доступна кнопка «📊 Реферальная программа»."
        )

    await message.answer(
        text,
        reply_markup=main_keyboard(user_id)
    )


@dp.message(Command("myref"))
async def myref_command(message: Message):
    await show_referral_program(message)


@dp.message(F.text == "📊 Реферальная программа")
async def referral_program_button(message: Message):
    await show_referral_program(message)


async def show_referral_program(message: Message):
    user_id = message.from_user.id

    if user_id not in MODERATOR_IDS:
        await message.answer("У вас нет доступа к реферальной программе.")
        return

    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    count = get_referral_count(user_id)

    await message.answer(
        "📊 <b>Ваша реферальная программа</b>\n\n"
        f"👥 Вы привели: <b>{count}</b> человек\n\n"
        f"🔗 Ваша ссылка:\n<code>{referral_link}</code>"
    )


@dp.message(F.text == "📝 Написать заявку")
async def write_application_button(message: Message, state: FSMContext):
    await state.set_state(ApplicationState.waiting_application)

    await message.answer(
        "Напишите вашу заявку одним сообщением."
    )


@dp.message(ApplicationState.waiting_application, F.text)
async def application_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    application_text = message.text.strip()

    if not application_text:
        await message.answer("Заявка не может быть пустой. Напишите текст заявки.")
        return

    user = get_user(user_id)

    if user is None:
        save_or_update_user(
            user_id=user_id,
            username=username,
            full_name=full_name,
            referrer_id=None
        )
        referrer_id = None
    else:
        referrer_id = user[3]

    save_application(
        user_id=user_id,
        text=application_text,
        referrer_id=referrer_id
    )

    user_text = format_user_link(
        user_id=user_id,
        username=username,
        full_name=full_name
    )

    referrer_text = format_referrer_text(referrer_id)

    admin_message = (
        "📩 <b>Новая заявка</b>\n\n"
        f"👤 <b>От пользователя:</b>\n{user_text}\n\n"
        f"🔗 <b>Пришёл по ссылке:</b>\n{referrer_text}\n\n"
        f"📝 <b>Текст заявки:</b>\n{html.escape(application_text)}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_message)
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу {admin_id}: {e}")

    await state.clear()

    await message.answer(
        "✅ Ваша заявка отправлена администратору.",
        reply_markup=main_keyboard(user_id)
    )


@dp.message(F.text)
async def other_text_handler(message: Message):
    await message.answer(
        "Чтобы отправить заявку, нажмите кнопку «📝 Написать заявку».",
        reply_markup=main_keyboard(message.from_user.id)
    )


async def main():
    global BOT_USERNAME

    if not BOT_TOKEN:
        raise ValueError("Не указан BOT_TOKEN в файле TOKEN.env")

    if not ADMIN_IDS:
        raise ValueError("Не указаны ADMIN_IDS в файле TOKEN.env")

    init_db()

    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username

    logging.info(f"Бот запущен: @{BOT_USERNAME}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())