# Yandex Mail → Yandex Disk

Раз в день забирает вложения из новых писем Яндекс Почты (IMAP) и складывает их на Яндекс Диск
по папкам с датой получения письма. Один код — три способа запуска: программа для Windows (`.exe`),
программа для macOS, облачная функция Yandex Cloud.

Инструкция для конечного пользователя — [ИНСТРУКЦИЯ.md](ИНСТРУКЦИЯ.md).

## Как устроено

| Модуль | Что делает |
|---|---|
| `mail2disk/imap_source.py` | IMAP: список папок, `UID SEARCH`, `BODY.PEEK[]` в режиме `EXAMINE` — письма не помечаются прочитанными |
| `mail2disk/attachments.py` | Достаёт вложения из письма, пропускает мелкие картинки из подписей |
| `mail2disk/names.py` | Декодирует имена (RFC 2047/2231, cp1251, koi8-r, «сырые» байты) и делает их безопасными для Windows/macOS/Диска |
| `mail2disk/disk.py` | Диск через WebDAV (пароль приложения) или REST API (OAuth-токен); таймауты и повторы |
| `mail2disk/sync.py` | Основная логика: какие письма новые, куда класть, что делать при сбоях |
| `mail2disk/state.py` | Запоминает последний обработанный UID по каждой папке |
| `mail2disk/scheduler.py` | Автозапуск: Планировщик заданий Windows / launchd на macOS |
| `mail2disk/wizard.py`, `cli.py` | Мастер настройки и меню для пользователя |
| `cloud/index.py` | Точка входа для Yandex Cloud Functions |

Поведение:

- Первый запуск берёт письма за последние `first_run_days` дней (по умолчанию 7), дальше — только
  письма с UID больше последнего обработанного. Пропущенные дни догоняются автоматически.
- Проверяются все папки, кроме спама, корзины, отправленных, черновиков и исходящих
  (по флагам `\Junk \Trash \Sent \Drafts` и по названиям). Список можно задать явно: `include_folders`.
- Путь на Диске: `<disk_folder>/<ГГГГ-ММ-ДД>/<имя>`. При совпадении имени сравнивается MD5:
  тот же файл пропускается, другой сохраняется как `имя (2).ext`.
- Если письмо не удалось обработать, прогресс по папке останавливается на нём и следующий запуск
  повторяет попытку. После 3 неудачных запусков письмо пропускается и попадает в отчёт.
- Настройки, состояние и журнал: Windows — `%APPDATA%\YandexMailToDisk\`,
  macOS — `~/Library/Application Support/YandexMailToDisk/`. Переопределяется `MAIL2DISK_HOME`.
- При настройке собранная программа копирует себя в эту же папку и автозапуск указывает на копию,
  поэтому исходный файл можно удалить.

## Разработка

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m mail2disk --help
```

Проверка на реальном аккаунте без сохранения настроек — файл `test.env` (в `.gitignore`):

```
MAIL2DISK_EMAIL=test@yandex.ru
MAIL2DISK_MAIL_PASSWORD=...
MAIL2DISK_DISK_PASSWORD=...
MAIL2DISK_DISK_TOKEN=
MAIL2DISK_DISK_FOLDER=Mail2DiskTest
```

```bash
MAIL2DISK_HOME=/tmp/m2d .venv/bin/python -m mail2disk --check --env-file test.env
MAIL2DISK_HOME=/tmp/m2d .venv/bin/python -m mail2disk --run --env-file test.env
```

Ключи командной строки: `--run` (без вопросов, для автозапуска), `--setup`, `--status`, `--check`,
`--remove`, `--env-file`. Коды выхода: `0` — успех, `1` — были ошибки, `2` — не настроено,
`3` — уже идёт другая выгрузка.

## Сборка

PyInstaller собирает только под ту ОС, на которой запущен.

- **macOS:** `./build_mac.sh` → `dist/YandexMailToDisk`. Архитектура совпадает с машиной сборки
  (arm64 на Apple Silicon). Для Intel-маков собирать на Intel или через universal2-Python.
- **Windows:** на любом Windows-компьютере с Python 3.12+ дважды щёлкнуть `build_windows.bat`
  → `dist\YandexMailToDisk.exe`.

Оба скрипта перед сборкой прогоняют тесты.

## Облако (Yandex Cloud Functions)

Состояние хранится на самом Диске: `<disk_folder>/.mail2disk-state.json`.

```bash
cloud/build_zip.sh
yc serverless function create --name mail2disk
yc serverless function version create \
  --function-name mail2disk --runtime python312 --entrypoint index.handler \
  --memory 256m --execution-timeout 600s --source-path dist/cloud-function.zip \
  --environment MAIL2DISK_EMAIL=...,MAIL2DISK_DISK_FOLDER="Вложения из почты",TZ=Europe/Moscow \
  --secret environment-variable=MAIL2DISK_MAIL_PASSWORD,id=<lockbox-id>,key=mail \
  --secret environment-variable=MAIL2DISK_DISK_PASSWORD,id=<lockbox-id>,key=disk \
  --service-account-id <sa-с-доступом-к-lockbox>
yc serverless trigger create timer --name mail2disk-daily \
  --cron-expression "0 6 ? * * *" \
  --invoke-function-name mail2disk --invoke-function-service-account-id <sa-id>
```

Cron-выражение триггера задаётся в UTC (`0 6` = 09:00 МСК).

## Ограничения

- IMAP должен быть включён в настройках Почты, нужны пароли приложений (обычный пароль не подойдёт).
- Файлы больше ~25 МБ Яндекс отправляет не вложением, а ссылкой на Диск — такие программа не видит.
- Письмо целиком читается в память — для очень больших писем (сотни МБ) нужна потоковая загрузка.
- Вложенные письма (`.eml` внутри письма) не сохраняются как файл, но их вложения выгружаются.
- Вложения из `winmail.dat` (Outlook RTF) не распаковываются.
- Пароли хранятся в файле настроек в профиле пользователя (на macOS — с правами 600), без шифрования.
- Локальный вариант работает, только когда компьютер включён; пропуск догоняется при следующем запуске.
  На Windows задача запускается только при вошедшем пользователе и на несколько секунд показывает окно.
- `.exe` и Mac-программа не подписаны — Windows SmartScreen и macOS Gatekeeper показывают предупреждение.
- Скорость загрузки через WebDAV Яндекс может ограничивать; для больших объёмов лучше OAuth-токен
  (`disk_token`), он использует REST API.
- OAuth-токен (если используется) живёт около года.
