
import asyncio
import os
import random
from enum import Enum

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# TOKEN берём из переменной окружения BOT_TOKEN
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("Не установлен BOT_TOKEN в переменных окружения.")

bot = Bot(TOKEN)
dp = Dispatcher()


# ------------ МОДЕЛЬ ИГРЫ ------------

class Phase(str, Enum):
    LOBBY = "lobby"
    NIGHT = "night"
    DAY_DISCUSSION = "day_discussion"
    DAY_VOTE = "day_vote"
    FINISHED = "finished"


class Role(str, Enum):
    DON = "don"
    COMMISSAR = "commissar"
    DOCTOR = "doctor"
    CIVIL = "civil"


class Game:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.phase: Phase = Phase.LOBBY

        # игроки: user_id -> {"name": str, "alive": bool, "num": int}
        self.players: dict[int, dict] = {}
        # роли: user_id -> Role
        self.roles: dict[int, Role] = {}

        # роли-идентификаторы
        self.don_id: int | None = None
        self.commissar_id: int | None = None
        self.doctor_id: int | None = None

        # для победы используем don как мафию
        self.mafia_id: int | None = None

        # голосование
        self.votes: dict[int, int] = {}       # voter_id -> target_id

        # ночные действия
        self.night_kill_target_id: int | None = None
        self.night_heal_target_id: int | None = None
        self.night_check_target_id: int | None = None

    @property
    def alive_players(self) -> list[int]:
        return [uid for uid, p in self.players.items() if p["alive"]]

    def players_list_text(self, only_alive: bool = False) -> str:
        lines = []
        for uid, p in self.players.items():
            if only_alive and not p["alive"]:
                continue
            status = "😵" if not p["alive"] else ""
            lines.append(f'{p["num"]}. {p["name"]} {status}')
        return "\n".join(lines)

    def get_role(self, uid: int) -> Role | None:
        return self.roles.get(uid)


# Для простоты — одна игра на один чат
games: dict[int, Game] = {}


def get_or_create_game(chat_id: int) -> Game:
    if chat_id not in games:
        games[chat_id] = Game(chat_id)
    return games[chat_id]


# ------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ------------

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Присоединиться"), KeyboardButton(text="📋 Состояние")],
            [KeyboardButton(text="🚀 Старт игры"), KeyboardButton(text="🗳 Начать голосование")],
        ],
        resize_keyboard=True,
    )


def build_vote_keyboard(game: Game) -> InlineKeyboardMarkup:
    buttons = []
    for uid in game.alive_players:
        p = game.players[uid]
        buttons.append(
            [InlineKeyboardButton(text=f"{p['num']}. {p['name']}", callback_data=f"vote:{p['num']}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_night_keyboard(game: Game, exclude_self_id: int, action_prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    for uid in game.alive_players:
        if uid == exclude_self_id and action_prefix == "kill":
            # дон не может убить себя
            continue
        p = game.players[uid]
        buttons.append(
            [InlineKeyboardButton(text=f"{p['num']}. {p['name']}", callback_data=f"night_{action_prefix}:{p['num']}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def start_night(game: Game):
    game.phase = Phase.NIGHT
    game.night_kill_target_id = None
    game.night_heal_target_id = None
    game.night_check_target_id = None

    # дон
    if game.don_id and game.players.get(game.don_id, {}).get("alive"):
        try:
            await bot.send_message(
                game.don_id,
                "🌙 Ночь. Ты — ДОН (мафия). Выбери, кого убить:",
                reply_markup=build_night_keyboard(game, exclude_self_id=game.don_id, action_prefix="kill"),
            )
        except Exception:
            pass

    # комиссар
    if game.commissar_id and game.players.get(game.commissar_id, {}).get("alive"):
        try:
            await bot.send_message(
                game.commissar_id,
                "🌙 Ночь. Ты — КОМИССАР. Выбери, кого проверить:",
                reply_markup=build_night_keyboard(game, exclude_self_id=game.commissar_id, action_prefix="check"),
            )
        except Exception:
            pass

    # доктор
    if game.doctor_id and game.players.get(game.doctor_id, {}).get("alive"):
        try:
            await bot.send_message(
                game.doctor_id,
                "🌙 Ночь. Ты — ДОКТОР. Выбери, кого лечить (можно себя):",
                reply_markup=build_night_keyboard(game, exclude_self_id=-1, action_prefix="heal"),
            )
        except Exception:
            pass

    await bot.send_message(
        game.chat_id,
        "🌙 Наступила НОЧЬ. Город засыпает... Роли делают свои ходы."
    )


async def try_resolve_night(game: Game):
    # Проверяем, все ли живые роли сходили
    don_alive = game.don_id and game.players.get(game.don_id, {}).get("alive")
    commissar_alive = game.commissar_id and game.players.get(game.commissar_id, {}).get("alive")
    doctor_alive = game.doctor_id and game.players.get(game.doctor_id, {}).get("alive")

    if don_alive and game.night_kill_target_id is None:
        return
    if commissar_alive and game.night_check_target_id is None:
        return
    if doctor_alive and game.night_heal_target_id is None:
        return

    # Все ходы сделаны — разрешаем ночь
    killed_player_name = None
    saved = False

    # лечение
    if game.night_kill_target_id is not None:
        if game.night_kill_target_id == game.night_heal_target_id:
            # вылечен
            saved = True
        else:
            # умирает
            if game.players[game.night_kill_target_id]["alive"]:
                game.players[game.night_kill_target_id]["alive"] = False
                killed_player_name = game.players[game.night_kill_target_id]["name"]

    # результат проверки комиссару
    if game.night_check_target_id is not None and commissar_alive:
        target_id = game.night_check_target_id
        role = game.get_role(target_id)
        is_mafia_side = role == Role.DON
        text = (
            f"Проверка завершена. Игрок {game.players[target_id]['name']} — "
            + ("МАФИЯ (ДОН) 💀" if is_mafia_side else "НЕ мафия (мирный/доктор) 😇")
        )
        try:
            await bot.send_message(game.commissar_id, text)
        except Exception:
            pass

    # сообщение в чат
    if killed_player_name and not saved:
        night_result_text = f"🌙 Ночь закончилась. Убит(а): {killed_player_name}."
    else:
        night_result_text = "🌙 Ночь закончилась. Никто не погиб этой ночью."

    await bot.send_message(
        game.chat_id,
        night_result_text + "\n\nНаступает ДЕНЬ. Обсуждайте, кто мафия!"
    )

    # проверка победы
    await check_win_and_continue(game, after_night=True)


# ------------ ХЭНДЛЕРЫ КОМАНД ------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != "private":
        await message.answer(
            "Я бот для игры в Мафию. Нажмите /menu, чтобы открыть кнопки.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await message.answer(
            "Привет! Я бот для игры в Мафию.\n"
            "Добавь меня в групповой чат и используй команды:\n"
            "/join — присоединиться к игре (в группе)\n"
            "/startgame — начать игру (в группе)"
        )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if message.chat.type == "private":
        await message.answer("Кнопки меню доступны в групповом чате 🙂")
        return
    await message.answer("Меню:", reply_markup=main_menu_keyboard())


@dp.message(Command("join"))
async def cmd_join(message: Message):
    if message.chat.type == "private":
        await message.answer("Присоединяться нужно в групповом чате 😊")
        return

    game = get_or_create_game(message.chat.id)

    if game.phase != Phase.LOBBY:
        await message.answer("Игра уже началась, жди следующей!")
        return

    uid = message.from_user.id
    if uid in game.players:
        await message.answer("Ты уже в игре!")
        return

    num = len(game.players) + 1
    game.players[uid] = {
        "name": message.from_user.full_name,
        "alive": True,
        "num": num,
    }

    await message.answer(
        f"{message.from_user.full_name} присоединился к игре!\n"
        f"Всего игроков: {len(game.players)}"
    )


# Обработка текстовых кнопок меню
@dp.message(F.text == "👥 Присоединиться")
async def on_join_button(message: Message):
    await cmd_join(message)


@dp.message(F.text == "📋 Состояние")
async def on_state_button(message: Message):
    await cmd_state(message)


@dp.message(F.text == "🚀 Старт игры")
async def on_startgame_button(message: Message):
    await cmd_startgame(message)


@dp.message(F.text == "🗳 Начать голосование")
async def on_startvote_button(message: Message):
    await cmd_startvote(message)


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if message.chat.type == "private":
        await message.answer("Игру нужно запускать в групповом чате.")
        return

    game = get_or_create_game(message.chat.id)

    if game.phase != Phase.LOBBY:
        await message.answer("Игра уже запущена.")
        return

    if len(game.players) < 4:
        await message.answer("Недостаточно игроков! Нужно минимум 4 (Дон, Комиссар, Доктор, Мирный).")
        return

    # раздача ролей
    all_ids = list(game.players.keys())
    random.shuffle(all_ids)

    game.don_id = all_ids[0]
    game.commissar_id = all_ids[1]
    game.doctor_id = all_ids[2]
    game.mafia_id = game.don_id

    for uid in all_ids:
        if uid == game.don_id:
            game.roles[uid] = Role.DON
        elif uid == game.commissar_id:
            game.roles[uid] = Role.COMMISSAR
        elif uid == game.doctor_id:
            game.roles[uid] = Role.DOCTOR
        else:
            game.roles[uid] = Role.CIVIL

    # отправляем роли в личку
    for uid, role in game.roles.items():
        text = ""
        if role == Role.DON:
            text = (
                "Твоя роль: ДОН (мафия) 💀\n"
                "Ты убиваешь по ночам. Жди кнопки с выбором цели."
            )
        elif role == Role.COMMISSAR:
            text = (
                "Твоя роль: КОМИССАР 🕵️‍♂️\n"
                "Каждую ночь ты можешь проверить одного игрока."
            )
        elif role == Role.DOCTOR:
            text = (
                "Твоя роль: ДОКТОР 🩺\n"
                "Каждую ночь ты лечишь одного игрока (можно себя)."
            )
        else:
            text = "Твоя роль: МИРНЫЙ ЖИТЕЛЬ 🙂\nПопытайся вычислить мафию."

        try:
            await bot.send_message(uid, text)
        except Exception:
            pass

    await message.answer(
        "Игра началась!\n"
        f"Игроки:\n{game.players_list_text()}\n\n"
        "Все роли розданы, в личных сообщениях у каждого указана роль."
    )

    # запускаем ночь
    await start_night(game)


@dp.message(Command("state"))
async def cmd_state(message: Message):
    if message.chat.type == "private":
        await message.answer("Команда /state работает в групповом чате.")
        return

    game = get_or_create_game(message.chat.id)

    await message.answer(
        f"Фаза: {game.phase}\n\n"
        "Игроки:\n"
        + game.players_list_text()
    )


@dp.message(Command("startvote"))
async def cmd_startvote(message: Message):
    if message.chat.type == "private":
        await message.answer("Эту команду нужно вызывать в групповом чате.")
        return

    game = get_or_create_game(message.chat.id)

    if game.phase not in [Phase.DAY_DISCUSSION, Phase.NIGHT]:
        await message.answer("Сейчас нельзя начинать голосование.")
        return

    game.phase = Phase.DAY_VOTE
    game.votes.clear()

    await message.answer(
        "Начинается голосование! 🗳\n"
        "Нажмите на кнопку с игроком, за которого голосуете.\n"
        "Голоса анонимные — в чат не пишется, кто за кого голосовал.\n\n"
        "Живые игроки:\n" + game.players_list_text(only_alive=True),
        reply_markup=build_vote_keyboard(game),
    )


# ------------ ГОЛОСОВАНИЕ (АНОНИМНОЕ ЧЕРЕЗ КНОПКИ) ------------

@dp.callback_query(F.data.startswith("vote:"))
async def on_vote_callback(callback: CallbackQuery):
    if not callback.message:
        return

    chat_id = callback.message.chat.id
    game = get_or_create_game(chat_id)

    if game.phase != Phase.DAY_VOTE:
        await callback.answer("Сейчас не идёт голосование.", show_alert=True)
        return

    voter_id = callback.from_user.id
    if voter_id not in game.players or not game.players[voter_id]["alive"]:
        await callback.answer("Ты не участвуешь в игре или уже выбыл.", show_alert=True)
        return

    _, num_str = callback.data.split(":", 1)
    if not num_str.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return

    target_num = int(num_str)

    # находим цель по номеру
    target_id = None
    for uid, p in game.players.items():
        if p["num"] == target_num and p["alive"]:
            target_id = uid
            break

    if target_id is None:
        await callback.answer("Живого игрока с таким номером нет.", show_alert=True)
        return

    # записываем голос
    game.votes[voter_id] = target_id

    await callback.answer("Голос принят! ✅", show_alert=False)

    # если все живые проголосовали — подводим итоги
    if len(game.votes) == len(game.alive_players):
        await finish_vote(game)


async def finish_vote(game: Game):
    # считаем голоса
    counter: dict[int, int] = {}
    for _, target in game.votes.items():
        counter[target] = counter.get(target, 0) + 1

    # находим игрока с макс голосов
    max_votes = -1
    eliminated_id = None
    for uid, count in counter.items():
        if count > max_votes:
            max_votes = count
            eliminated_id = uid

    if eliminated_id is None:
        await bot.send_message(game.chat_id, "Не удалось посчитать голоса, что-то пошло не так.")
        return

    game.players[eliminated_id]["alive"] = False
    eliminated_name = game.players[eliminated_id]["name"]

    await bot.send_message(
        game.chat_id,
        f"ГОЛОСОВАНИЕ ЗАКОНЧЕНО.\n"
        f"С наибольшим количеством голосов изгнан(а): {eliminated_name}.\n"
    )

    game.votes.clear()

    # проверяем победу
    await check_win_and_continue(game, after_night=False)


# ------------ НОЧНЫЕ КОЛЛБЭКИ (ДОН / КОМИССАР / ДОКТОР) ------------

def find_game_for_player_as_role(user_id: int, role_attr: str) -> Game | None:
    for g in games.values():
        rid = getattr(g, role_attr)
        if rid == user_id and g.phase == Phase.NIGHT and g.players.get(user_id, {}).get("alive"):
            return g
    return None


@dp.callback_query(F.data.startswith("night_kill:"))
async def on_night_kill(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = find_game_for_player_as_role(user_id, "don_id")
    if not game:
        await callback.answer("Сейчас не твой ход или не ночь.", show_alert=True)
        return

    _, num_str = callback.data.split(":", 1)
    if not num_str.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    target_num = int(num_str)

    target_id = None
    for uid, p in game.players.items():
        if p["num"] == target_num:
            target_id = uid
            break

    if target_id is None or not game.players[target_id]["alive"]:
        await callback.answer("Игрок недоступен.", show_alert=True)
        return
    if target_id == user_id:
        await callback.answer("Нельзя убить себя.", show_alert=True)
        return

    game.night_kill_target_id = target_id
    await callback.answer("Цель для убийства выбрана.", show_alert=False)

    await try_resolve_night(game)


@dp.callback_query(F.data.startswith("night_check:"))
async def on_night_check(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = find_game_for_player_as_role(user_id, "commissar_id")
    if not game:
        await callback.answer("Сейчас не твой ход или не ночь.", show_alert=True)
        return

    _, num_str = callback.data.split(":", 1)
    if not num_str.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    target_num = int(num_str)

    target_id = None
    for uid, p in game.players.items():
        if p["num"] == target_num:
            target_id = uid
            break

    if target_id is None or not game.players[target_id]["alive"]:
        await callback.answer("Игрок недоступен.", show_alert=True)
        return

    game.night_check_target_id = target_id
    await callback.answer("Игрок выбран для проверки.", show_alert=False)

    await try_resolve_night(game)


@dp.callback_query(F.data.startswith("night_heal:"))
async def on_night_heal(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = find_game_for_player_as_role(user_id, "doctor_id")
    if not game:
        await callback.answer("Сейчас не твой ход или не ночь.", show_alert=True)
        return

    _, num_str = callback.data.split(":", 1)
    if not num_str.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    target_num = int(num_str)

    target_id = None
    for uid, p in game.players.items():
        if p["num"] == target_num:
            target_id = uid
            break

    if target_id is None or not game.players[target_id]["alive"]:
        await callback.answer("Игрок недоступен.", show_alert=True)
        return

    game.night_heal_target_id = target_id
    await callback.answer("Игрок выбран на лечение.", show_alert=False)

    await try_resolve_night(game)


# ------------ ПРОВЕРКА ПОБЕДЫ И ПЕРЕХОД ФАЗ ------------

async def check_win_and_continue(game: Game, after_night: bool):
    mafia_alive = game.mafia_id is not None and game.players[game.mafia_id]["alive"]
    alive_count = len(game.alive_players)

    if not mafia_alive:
        game.phase = Phase.FINISHED
        await bot.send_message(
            game.chat_id,
            "🎉 МИРНЫЕ ПОБЕДИЛИ! Мафия поймана.\n"
            "Можно начинать новую игру: /join, /startgame."
        )
        return

    # Если живых ≤ 2 (дон и один мирный или подобное) — победа мафии
    if alive_count <= 2:
        game.phase = Phase.FINISHED
        await bot.send_message(
            game.chat_id,
            "💀 МАФИЯ ПОБЕДИЛА! Количество мирных критически мало.\n"
            "Можно начинать новую игру: /join, /startgame."
        )
        return

    if after_night:
        game.phase = Phase.DAY_DISCUSSION
        await bot.send_message(
            game.chat_id,
            "Начинается ДЕНЬ. Обсуждайте, кто мафия.\n"
            "Когда будете готовы голосовать — используйте кнопку '🗳 Начать голосование' или команду /startvote."
        )
    else:
        # после голосования — новая ночь
        await start_night(game)


# ------------ ЗАПУСК ------------

async def main():
    print("Bot started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
