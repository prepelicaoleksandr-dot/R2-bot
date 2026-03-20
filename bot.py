import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# =========================
# НАСТРОЙКА
# =========================
TOKEN = os.getenv("BOT_TOKEN", "8496463384:AAFuSjaz6NWix9aB8A76ftLY5rPjjpjh0r0")
GROUP_ID = int(os.getenv("GROUP_ID", "-1003738631279"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


# =========================
# СОСТОЯНИЕ
# =========================
@dataclass
class OccupiedState:
    user_id: int
    username: str


@dataclass
class DailyState:
    constance_done: bool = False
    bears_done: bool = False


@dataclass
class BotState:
    status_message_id: Optional[int] = None
    occupied: Optional[OccupiedState] = None
    thread_id: Optional[int] = None
    dailies: DailyState = field(default_factory=DailyState)


state = BotState()
state_lock = asyncio.Lock()


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================
def message_in_target_group(message: types.Message) -> bool:
    return message.chat.id == GROUP_ID


def callback_in_target_group(callback: types.CallbackQuery) -> bool:
    return bool(callback.message and callback.message.chat.id == GROUP_ID)


def display_name(user: types.User) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def get_thread_id_from_message(message: types.Message) -> Optional[int]:
    return getattr(message, "message_thread_id", None)


def bool_mark(value: bool) -> str:
    return "✅" if value else "⬜"


def render_status_text(occupied: Optional[OccupiedState], dailies: DailyState) -> str:
    if occupied is None:
        status_block = "<b>Перс свободен</b>"
    else:
        status_block = (
            "<b>Перс занят</b>\n"
            f"Занял: <b>{occupied.username}</b>"
        )

    dailies_block = (
        "\n\n<b>Ежедневки:</b>\n"
        f"Констанция: {bool_mark(dailies.constance_done)}\n"
        f"Квест на мишочки: {bool_mark(dailies.bears_done)}"
    )
    return status_block + dailies_block


def build_keyboard(
    occupied: Optional[OccupiedState],
    *,
    reset_mode: bool = False,
) -> InlineKeyboardMarkup:
    if reset_mode:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚠️ Да, сбросить", callback_data="reset_yes")],
                [InlineKeyboardButton(text="Отмена", callback_data="reset_no")],
            ]
        )

    rows = []
    if occupied is None:
        rows.append([
            InlineKeyboardButton(text="✅ Занять перса", callback_data="occupy")
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🔓 Освободить", callback_data="release")
        ])

    rows.append([
        InlineKeyboardButton(text="Констанция", callback_data="toggle_constance"),
        InlineKeyboardButton(text="Мишочки", callback_data="toggle_bears"),
    ])
    rows.append([
        InlineKeyboardButton(text="↩️ Сбросить", callback_data="reset_confirm")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_topic_message(text: str) -> None:
    async with state_lock:
        thread_id = state.thread_id

    await bot.send_message(
        chat_id=GROUP_ID,
        text=text,
        message_thread_id=thread_id,
        disable_notification=False,
    )


async def ensure_status_message() -> int:
    async with state_lock:
        if state.status_message_id is not None:
            return state.status_message_id
        thread_id = state.thread_id
        occupied = state.occupied
        dailies = DailyState(
            constance_done=state.dailies.constance_done,
            bears_done=state.dailies.bears_done,
        )

    sent = await bot.send_message(
        chat_id=GROUP_ID,
        text=render_status_text(occupied, dailies),
        reply_markup=build_keyboard(occupied),
        message_thread_id=thread_id,
    )

    async with state_lock:
        state.status_message_id = sent.message_id

    try:
        await bot.pin_chat_message(
            chat_id=GROUP_ID,
            message_id=sent.message_id,
            disable_notification=True,
        )
        logger.info("Сообщение закреплено: %s", sent.message_id)
    except Exception as exc:
        logger.error("Ошибка закрепления: %s", exc)

    return sent.message_id


async def refresh_status_message(*, reset_mode: bool = False) -> None:
    message_id = await ensure_status_message()

    async with state_lock:
        occupied = state.occupied
        dailies = DailyState(
            constance_done=state.dailies.constance_done,
            bears_done=state.dailies.bears_done,
        )

    text = render_status_text(occupied, dailies)
    if reset_mode and occupied is not None:
        text += "\n\nСбросить статус? Возможно игрок забыл снять."

    await bot.edit_message_text(
        chat_id=GROUP_ID,
        message_id=message_id,
        text=text,
        reply_markup=build_keyboard(occupied, reset_mode=reset_mode),
    )


# =========================
# КОМАНДЫ
# =========================
@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    if not message_in_target_group(message):
        return
    await message.answer("Бот запущен.")


@dp.message(F.text == "/init_status")
async def init_status(message: types.Message):
    if not message_in_target_group(message):
        return

    async with state_lock:
        state.status_message_id = None
        state.occupied = None
        state.thread_id = get_thread_id_from_message(message)
        state.dailies = DailyState()

    await ensure_status_message()
    await message.answer("Статусное сообщение создано в текущей теме.")


@dp.message(F.text == "/reset_dailies")
async def reset_dailies(message: types.Message):
    if not message_in_target_group(message):
        return

    async with state_lock:
        state.dailies = DailyState()

    await refresh_status_message()
    await message.answer("Ежедневки сброшены.")


# =========================
# CALLBACK-КНОПКИ
# =========================
@dp.callback_query(F.data == "occupy")
async def occupy_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    if callback.from_user is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    username = display_name(callback.from_user)

    async with state_lock:
        if state.occupied is not None:
            await callback.answer(
                f"Перс уже занят: {state.occupied.username}",
                show_alert=True,
            )
            return

        state.occupied = OccupiedState(
            user_id=callback.from_user.id,
            username=username,
        )

    await refresh_status_message()
    await send_topic_message(f"🔔 {username} занял перса")
    await callback.answer("Перс занят")


@dp.callback_query(F.data == "release")
async def release_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    if callback.from_user is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    async with state_lock:
        if state.occupied is None:
            await callback.answer("Перс уже свободен", show_alert=True)
            return

        if callback.from_user.id != state.occupied.user_id:
            await callback.answer(
                "Освободить может только тот, кто занял",
                show_alert=True,
            )
            return

        username = state.occupied.username
        state.occupied = None

    await refresh_status_message()
    await send_topic_message(f"✅ {username} освободил перса")
    await callback.answer("Перс освобожден")


@dp.callback_query(F.data == "toggle_constance")
async def toggle_constance_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    async with state_lock:
        state.dailies.constance_done = not state.dailies.constance_done
        done = state.dailies.constance_done

    await refresh_status_message()
    await callback.answer(
        "Констанция отмечена" if done else "Констанция снята"
    )


@dp.callback_query(F.data == "toggle_bears")
async def toggle_bears_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    async with state_lock:
        state.dailies.bears_done = not state.dailies.bears_done
        done = state.dailies.bears_done

    await refresh_status_message()
    await callback.answer(
        "Мишочки отмечены" if done else "Мишочки сняты"
    )


@dp.callback_query(F.data == "reset_confirm")
async def reset_confirm_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    async with state_lock:
        occupied = state.occupied

    if occupied is None:
        await callback.answer("Перс уже свободен", show_alert=True)
        return

    await refresh_status_message(reset_mode=True)
    await callback.answer()


@dp.callback_query(F.data == "reset_no")
async def reset_no_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    await refresh_status_message()
    await callback.answer("Отменено")


@dp.callback_query(F.data == "reset_yes")
async def reset_yes_handler(callback: types.CallbackQuery):
    if not callback_in_target_group(callback):
        await callback.answer()
        return

    async with state_lock:
        old_username = state.occupied.username if state.occupied else "Неизвестно"
        state.occupied = None

    await refresh_status_message()
    await send_topic_message(
        f"↩️ Статус был сброшен. Перс снова свободен. Последний занявший: {old_username}"
    )
    await callback.answer("Статус сброшен")


# =========================
# ЗАПУСК
# =========================
async def on_startup() -> None:
    logger.info("Бот запущен. Вызовите /init_status в нужной теме.")


async def keep_alive() -> None:
    while True:
        try:
            logger.info("Keep alive ping")
            await bot.get_me()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Keep alive ping failed")
        await asyncio.sleep(3600)  # 10 минут


async def main() -> None:
    if TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения.")

    logger.info("Запуск бота...")
    await on_startup()
    asyncio.create_task(keep_alive())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(bot.session.close())
