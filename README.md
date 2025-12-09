
# Mafia Telegram Bot (v2)

Бот для игры в мафию с ролями:
- Дон (мафия)
- Комиссар
- Доктор
- Мирные жители

Особенности:
- Анонимное голосование днём через кнопки
- Красивое меню с кнопками в групповом чате
- Ночные действия ролей (дон убивает, комиссар проверяет, доктор лечит)

## Локальный запуск

1. Установи зависимости:
   ```bash
   pip install -r requirements.txt
   ```

2. Установи переменную окружения BOT_TOKEN (токен от @BotFather):
   ```bash
   export BOT_TOKEN=твой_токен
   ```

   В Windows (PowerShell):
   ```powershell
   setx BOT_TOKEN "твой_токен"
   ```

3. Запусти бота:
   ```bash
   python mafia_bot.py
   ```

## Деплой на Render

1. Залей файлы в GitHub (`mafia_bot.py`, `requirements.txt`, `README.md`).
2. На Render создай Web Service, выбери этот репозиторий.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python mafia_bot.py`
5. В Environment добавь переменную `BOT_TOKEN` с токеном бота.
