import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv


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


QUESTIONS = [
    {
        "key": "name",
        "text": "Как вас зовут?",
        "title": "Имя"
    },
    {
        "key": "age",
        "text": "Сколько вам лет?",
        "title": "Возраст"
    },
    {
        "key": "city",
        "text": "Из какого вы города?",
        "title": "Город"
    },
    {
        "key": "contact",
        "text": "Оставьте контакт для связи. Можно Telegram, номер телефона или другой удобный способ.",
        "title": "Контакт"
    },
    {
        "key": "direction",
        "text": "На что именно хотите оставить заявку?",
        "title": "Направление заявки"
    },
    {
        "key": "experience",
        "text": "Расскажите коротко о себе или своём опыте.",
        "title": "О себе / опыт"
    },
    {
        "key": "time",
        "text": "Когда вам удобно начать или когда с вами лучше связаться?",
        "title": "Удобное время"
    },
    {
        "key": "comment",
        "text": "Есть ли дополнительный комментарий? Если нет — напишите «Нет».",
        "title": "Комментарий"
    }
]


class ApplicationState(StatesGroup):
    answering = State()


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
            answers TEXT,
            referrer_id INTEGER,
            created_at TEXT
        )
        """)

        conn.commit()


def get_user(user_id: int):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, username, full_name, referrer_id, created_at
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        )
        return cur.fetchone()


def save_or_update_user(
    user_id: int,
    username: str | None,
    full_name: str,
    referrer_id: int | None
):
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


def save_application(user_id: int, answers: str, referrer_id: int | None):
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO applications (user_id, answers, referrer_id, created_at)
        VALUES (?, ?, ?, ?)
        """, (
            user_id,
            answers,
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


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_moderator(user_id: int) -> bool:
    return user_id in MODERATOR_IDS


def can_send_application(user_id: int) -> bool:
    return not is_admin(user_id) and not is_moderator(user_id)


def main_keyboard(user_id: int):
    buttons = []

    if can_send_application(user_id):
        buttons.append([KeyboardButton(text="📝 Написать заявку")])

    if is_moderator(user_id):
        buttons.append([KeyboardButton(text="📊 Реферальная программа")])

    if not buttons:
        return None

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )


def parse_referrer_from_start(message: Message) -> int | None:
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

    if referrer_id not in MODERATOR_IDS:
        return None

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
        return (
            f"{html.escape(ref_name)} (@{html.escape(ref_username)})\n"
            f"ID модератора: <code>{referrer_id}</code>"
        )

    return f"{html.escape(ref_name)}\nID модератора: <code>{referrer_id}</code>"


def answers_to_text(answers: dict) -> str:
    result = []

    for question in QUESTIONS:
        key = question["key"]
        title = question["title"]
        answer = answers.get(key, "Не указано")

        result.append(
            f"<b>{html.escape(title)}:</b>\n{html.escape(answer)}"
        )

    return "\n\n".join(result)


def answers_to_db_text(answers: dict) -> str:
    result = []

    for question in QUESTIONS:
        key = question["key"]
        title = question["title"]
        answer = answers.get(key, "Не указано")

        result.append(f"{title}: {answer}")

    return "\n".join(result)


async def healthcheck(request):
    return web.Response(text="Bot is running")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", healthcheck)

    port = int(os.getenv("PORT", 10000))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logging.info(f"Web server started on port {port}")


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

    await state.clear()

    if is_admin(user_id):
        await message.answer(
            "👑 Вы администратор.\n\n"
            "Вам будут приходить заявки от клиентов.\n"
            "Отправлять заявки с этого аккаунта нельзя.",
            reply_markup=main_keyboard(user_id)
        )
        return

    if is_moderator(user_id):
        await message.answer(
            "🛡 Вы модератор.\n\n"
            "Вам доступна реферальная программа.\n"
            "Отправлять заявки с этого аккаунта нельзя.",
            reply_markup=main_keyboard(user_id)
        )
        return

    await message.answer(
        "Здравствуйте!\n\n"
        "Чтобы оставить заявку, нажмите кнопку ниже.",
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

    if not is_moderator(user_id):
        await message.answer("У вас нет доступа к реферальной программе.")
        return

    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    count = get_referral_count(user_id)

    await message.answer(
        "📊 <b>Ваша реферальная программа</b>\n\n"
        f"👥 Вы привели: <b>{count}</b> человек\n\n"
        f"🔗 Ваша ссылка:\n<code>{referral_link}</code>",
        reply_markup=main_keyboard(user_id)
    )


@dp.message(F.text == "📝 Написать заявку")
async def write_application_button(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if not can_send_application(user_id):
        await message.answer(
            "Вы не можете отправлять заявки с этого аккаунта.",
            reply_markup=main_keyboard(user_id)
        )
        return

    await state.set_state(ApplicationState.answering)
    await state.update_data(
        question_index=0,
        answers={}
    )

    await message.answer(
        f"Вопрос 1 из {len(QUESTIONS)}:\n\n{QUESTIONS[0]['text']}"
    )


@dp.message(ApplicationState.answering, F.text)
async def application_question_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if not can_send_application(user_id):
        await state.clear()
        await message.answer(
            "Вы не можете отправлять заявки с этого аккаунта.",
            reply_markup=main_keyboard(user_id)
        )
        return

    data = await state.get_data()

    question_index = data.get("question_index", 0)
    answers = data.get("answers", {})

    current_question = QUESTIONS[question_index]
    answer_text = message.text.strip()

    if not answer_text:
        await message.answer("Ответ не может быть пустым. Напишите ответ.")
        return

    answers[current_question["key"]] = answer_text

    question_index += 1

    if question_index < len(QUESTIONS):
        await state.update_data(
            question_index=question_index,
            answers=answers
        )

        next_question = QUESTIONS[question_index]

        await message.answer(
            f"Вопрос {question_index + 1} из {len(QUESTIONS)}:\n\n"
            f"{next_question['text']}"
        )
        return

    user = get_user(user_id)

    if user is None:
        referrer_id = None
        save_or_update_user(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            referrer_id=None
        )
    else:
        referrer_id = user[3]

    db_answers = answers_to_db_text(answers)

    save_application(
        user_id=user_id,
        answers=db_answers,
        referrer_id=referrer_id
    )

    user_text = format_user_link(
        user_id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name
    )

    referrer_text = format_referrer_text(referrer_id)
    application_text = answers_to_text(answers)

    admin_message = (
        "📩 <b>Новая заявка</b>\n\n"
        f"👤 <b>От пользователя:</b>\n{user_text}\n\n"
        f"🔗 <b>Пришёл по ссылке:</b>\n{referrer_text}\n\n"
        f"📝 <b>Анкета:</b>\n\n"
        f"{application_text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_message)
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу {admin_id}: {e}")

    await state.clear()

    await message.answer(
        "✅ Ваша заявка отправлена администратору.\n\n"
        "Спасибо! С вами свяжутся в ближайшее время.",
        reply_markup=main_keyboard(user_id)
    )


@dp.message(F.text)
async def other_text_handler(message: Message):
    user_id = message.from_user.id

    if is_admin(user_id):
        await message.answer(
            "👑 Вы администратор.\n\n"
            "Вам будут приходить заявки от клиентов.",
            reply_markup=main_keyboard(user_id)
        )
        return

    if is_moderator(user_id):
        await message.answer(
            "🛡 Вы модератор.\n\n"
            "Нажмите «📊 Реферальная программа», чтобы получить ссылку и посмотреть статистику.",
            reply_markup=main_keyboard(user_id)
        )
        return

    await message.answer(
        "Чтобы отправить заявку, нажмите кнопку «📝 Написать заявку».",
        reply_markup=main_keyboard(user_id)
    )


async def main():
    global BOT_USERNAME

    if not BOT_TOKEN:
        raise ValueError("Не указан BOT_TOKEN")

    if not ADMIN_IDS:
        raise ValueError("Не указаны ADMIN_IDS")

    init_db()

    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username

    logging.info(f"Бот запущен: @{BOT_USERNAME}")

    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())