# Мини-CRM для заявок агентства

Лиды из Telegram-бота, из обычной лички и заведённые руками — в одном списке, с тегами
и фильтром по тегу. Тестовое задание; набросок продукта — в `product_sketch.md`,
разбор работы — в `postmortem.md`.

## Стек

FastAPI · SQLAlchemy 2 (async) · SQLite (aiosqlite) · Jinja2 · httpx (Bot API) ·
Telethon (userbot) · pytest.

Бот и юзербот — фоновые задачи внутри того же процесса, что и веб (`lifespan`).

## Запуск

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env          # заполнить BOT_TOKEN и CRM_PASSWORD
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

После изменения кода перезапустите сервер: эта команда запускает его без `--reload`.
Проверяйте не только `/healthz`, но и `/leads` — новые шаблоны со старыми роутами
могут вернуть 500 при работающем health-check.

CRM — на http://127.0.0.1:8000/leads, вход по `CRM_USER` / `CRM_PASSWORD` из `.env`.

### Переменные окружения

| Переменная | Зачем |
|---|---|
| `BOT_TOKEN` | токен от @BotFather, без него бот не стартует |
| `TG_API_ID`, `TG_API_HASH` | ключи с my.telegram.org, нужны только юзерботу (пункт 2) |
| `CRM_USER`, `CRM_PASSWORD` | вход в веб-CRM |
| `DATABASE_URL` | по умолчанию `sqlite+aiosqlite:///crm.db` |

### Живая ссылка

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

### Юзербот (пункт 2)

Нужны `TG_API_ID` / `TG_API_HASH` в `.env`, затем разовый вход по номеру:

```bash
.venv\Scripts\python.exe scripts\userbot_login.py
```

Создастся `userbot.session`, дальше основной процесс подхватит юзербот сам.

## Тесты и проверка

Результаты проверки пунктов 1–4 на живом стенде — в [verification.md](verification.md).

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\smoke_check.py https://<живая-ссылка>
```

`smoke_check.py` проходит сквозной сценарий по HTTP: список лидов, тег, ручной лид,
поиск вместе с фильтром по тегу, проверка авторизации. Сам создаёт два проверочных
лида и удаляет их вместе с неиспользуемыми проверочными тегами, в том числе при
ошибке проверки. Работает на пустой базе; живой диалог Telegram проверяется отдельно.
Для очистки используется защищённый `POST /leads/{lead_id}/delete`.

В списке есть поиск по имени, контакту и запросу (`ILIKE`) и страницы по 50 лидов.
Поиск сохраняется при смене тега, оба фильтра сохраняются при переходе между страницами.
В SQLite регистронезависимость `ILIKE` по умолчанию распространяется на ASCII;
регистр кириллицы нужно учитывать при вводе запроса.

46 тестов проверяют создание и атомарность сохранения, поля и статусы, теги,
HTML/JSON-ошибки, поиск и пагинацию, диалог и ответы Bot API без сети,
работу smoke на пустой базе и очистку после ошибки, запуск юзербота без ключей.
Сценарий «диалог бота → лид → тег → фильтр» проходит одним тестом без сети.
Также проверены сохранение черновика при ошибке БД, повтор подтверждения,
восстановление polling после некорректного JSON, валидация форм и разделение
лидов бота и лички при совпадении `tg_chat_id`.
Обработчик юзербота проверяется с подменённым клиентом: первый входящий создаёт
лид, следующий дописывает запрос; группы, исходящие и сообщения ботов игнорируются.
Это проверка без сети, а не подтверждение подключения обычного Telegram-аккаунта.

## Структура

```
app/
  config.py    настройки из .env
  models.py    Lead, Tag, many-to-many
  db.py        async-движок и сессии
  crud.py      операции над лидами — общие для веба, бота и юзербота
  bot.py       Telegram-бот: заявка в три шага, long polling
  userbot.py   Telethon: входящие из лички становятся лидами
  web.py       роуты CRM и basic auth
  main.py      FastAPI + фоновые задачи
  templates/   leads.html, lead.html, base.html, error.html
scripts/       userbot_login.py, smoke_check.py, render_preview.py
tests/         test_crm.py
```
