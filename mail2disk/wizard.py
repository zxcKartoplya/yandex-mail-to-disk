import getpass
from dataclasses import replace

from .config import TIME_RE, Config, ConfigError
from .disk import DiskError, make_disk
from .imap_source import MailError, MailSource

MAIL_PASSWORD_HELP = """\
Нужен ПАРОЛЬ ПРИЛОЖЕНИЯ для Почты (это не обычный пароль от Яндекса):
  1) откройте https://id.yandex.ru/security/app-passwords
  2) нажмите «Создать пароль приложения» → «Почта», придумайте любое название
  3) скопируйте показанный пароль
Также в Почте должен быть включён IMAP:
  Почта → Все настройки → Почтовые программы →
  «С сервера imap.yandex.ru по протоколу IMAP» (галочка)."""

DISK_PASSWORD_HELP = """\
Нужен ещё один ПАРОЛЬ ПРИЛОЖЕНИЯ, теперь для Диска:
  1) снова https://id.yandex.ru/security/app-passwords
  2) «Создать пароль приложения» → «Файлы» (WebDAV)
  3) скопируйте показанный пароль"""

SECRET_HINT = "(при вводе символы не отображаются — это нормально; можно вставить правой кнопкой мыши или Ctrl+V / Cmd+V)"


class WizardCancelled(Exception):
    pass


def ask(text: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = getpass.getpass(f"{text}: ") if secret else input(f"{text}{suffix}: ")
        except EOFError as error:
            raise WizardCancelled() from error
        value = value.strip()
        if value:
            return value
        if default is not None:
            return default
        print("  Значение не может быть пустым.")


def ask_retry() -> None:
    answer = input("Попробовать ещё раз? Enter — да, 0 — выйти: ").strip()
    if answer == "0":
        raise WizardCancelled()


def check_mail(config: Config) -> None:
    source = MailSource(config.imap_server, config.email, config.mail_password, timeout=30)
    source.connect()
    try:
        source.list_folders()
    finally:
        source.close()


def check_disk(config: Config) -> None:
    make_disk(config).check()


def run_wizard(existing: Config | None = None) -> Config:
    print()
    print("=== Настройка ===")
    print("Ответьте на несколько вопросов. В квадратных скобках — значение по умолчанию, его можно принять клавишей Enter.")
    print()

    email = ask("Шаг 1 из 5. Адрес Яндекс Почты", existing.email if existing else None)
    if "@" not in email:
        email += "@yandex.ru"
    config = replace(existing, email=email) if existing else Config(email=email, mail_password="")

    print()
    print(MAIL_PASSWORD_HELP)
    while True:
        password = ask(f"Шаг 2 из 5. Пароль приложения для Почты {SECRET_HINT}", secret=True)
        config = replace(config, mail_password=password).normalized()
        print("  Проверяю вход в почту...")
        try:
            check_mail(config)
            print("  ✓ Почта подключена.")
            break
        except MailError as error:
            print(f"  ✗ {error}")
            ask_retry()

    print()
    print(DISK_PASSWORD_HELP)
    while True:
        password = ask(f"Шаг 3 из 5. Пароль приложения для Диска {SECRET_HINT}", secret=True)
        config = replace(config, disk_password=password, disk_token="").normalized()
        print("  Проверяю доступ к Диску...")
        try:
            check_disk(config)
            print("  ✓ Диск подключён.")
            break
        except DiskError as error:
            print(f"  ✗ {error}")
            ask_retry()

    print()
    folder = ask("Шаг 4 из 5. В какую папку на Диске складывать файлы", config.disk_folder)
    config = replace(config, disk_folder=folder).normalized()

    print()
    while True:
        run_time = ask("Шаг 5 из 5. Во сколько каждый день запускать выгрузку (ЧЧ:ММ)", config.run_time)
        if TIME_RE.match(run_time):
            break
        print("  Введите время в формате ЧЧ:ММ, например 09:00.")
    config = replace(config, run_time=run_time)

    if existing is None:
        print()
        while True:
            days = ask("За сколько последних дней выгрузить вложения при первом запуске", str(config.first_run_days))
            if days.isdigit():
                break
            print("  Введите число, например 7.")
        config = replace(config, first_run_days=int(days))

    try:
        config.validate()
    except ConfigError as error:
        print(error)
        raise WizardCancelled() from error
    return config
