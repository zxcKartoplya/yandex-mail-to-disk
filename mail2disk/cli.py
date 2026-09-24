import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from . import APP_TITLE, __version__
from .config import Config, ConfigError, load_config, load_env_file, save_config
from .disk import DiskError
from .imap_source import MailError
from .paths import app_dir, config_path, log_path, state_path
from .runner import AlreadyRunning, run_lock, run_once, setup_logging
from .scheduler import SchedulerError, install, is_installed, uninstall
from .state import FileStateStore
from .wizard import WizardCancelled, check_disk, check_mail, run_wizard

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_NOT_CONFIGURED = 2
EXIT_ALREADY_RUNNING = 3

log = logging.getLogger(__name__)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _load(env_file: str | None) -> Config | None:
    if env_file:
        return Config.from_env({**os.environ, **load_env_file(Path(env_file))})
    return load_config(config_path())


def do_run(config: Config) -> int:
    try:
        with run_lock():
            report = run_once(config)
    except AlreadyRunning as error:
        log.warning(str(error))
        return EXIT_ALREADY_RUNNING
    except ConfigError as error:
        log.error(str(error))
        return EXIT_NOT_CONFIGURED
    return EXIT_OK if report.ok else EXIT_ERRORS


def do_setup(existing: Config | None) -> int:
    try:
        config = run_wizard(existing)
    except (WizardCancelled, KeyboardInterrupt):
        print("\nНастройка прервана, ничего не сохранено.")
        return EXIT_NOT_CONFIGURED
    save_config(config, config_path())
    print(f"\n✓ Настройки сохранены: {config_path()}")
    print("\nЗапускаю первую выгрузку, это может занять несколько минут...\n")
    code = do_run(config)
    print()
    try:
        hour, minute = config.run_hour_minute
        print("✓ " + install(hour, minute))
    except SchedulerError as error:
        print(f"✗ {error}")
        return EXIT_ERRORS
    print("\nГотово. Программу можно закрыть — дальше всё будет происходить само.")
    return code


def print_status(config: Config | None) -> None:
    print(f"\n{APP_TITLE} (версия {__version__})")
    if config is None:
        print("Программа ещё не настроена.")
        return
    state = FileStateStore(state_path()).load()
    print(f"Почта: {config.email}")
    print(f"Папка на Диске: {config.disk_folder}")
    print(f"Автозапуск: {'включён, каждый день в ' + config.run_time if is_installed() else 'выключен'}")
    print(f"Последняя успешная выгрузка: {state.last_success.replace('T', ' ') if state.last_success else 'ещё не было'}")
    print(f"Журнал работы: {log_path()}")


def open_folder() -> None:
    folder = str(app_dir())
    try:
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)
    except OSError:
        print(folder)


def do_remove() -> int:
    try:
        print(uninstall())
    except SchedulerError as error:
        print(f"✗ {error}")
        return EXIT_ERRORS
    config_path().unlink(missing_ok=True)
    state_path().unlink(missing_ok=True)
    print("Настройки удалены. Файлы на Яндекс Диске не тронуты.")
    return EXIT_OK


def interactive_menu() -> int:
    while True:
        try:
            config = load_config(config_path())
        except ConfigError as error:
            print(error)
            config = None
        if config is None:
            print(f"\nДобро пожаловать! Это программа «{APP_TITLE}».")
            print("Она раз в день забирает вложения из писем и складывает их на Яндекс Диск по папкам с датами.")
            return do_setup(None)
        print_status(config)
        print()
        print("1 — Выгрузить сейчас")
        print("2 — Изменить настройки")
        print("3 — Открыть папку с журналом и настройками")
        print("4 — Отключить автозапуск и удалить настройки")
        print("0 — Выход")
        choice = input("Выберите пункт и нажмите Enter: ").strip()
        if choice == "1":
            print()
            do_run(config)
        elif choice == "2":
            do_setup(config)
        elif choice == "3":
            open_folder()
        elif choice == "4":
            if input("Точно отключить? Введите «да»: ").strip().lower() in ("да", "yes", "y"):
                do_remove()
                return EXIT_OK
        elif choice == "0" or choice == "":
            return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="YandexMailToDisk", description=APP_TITLE)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true", help="выгрузить без вопросов (для автозапуска)")
    group.add_argument("--setup", action="store_true", help="пройти настройку заново")
    group.add_argument("--status", action="store_true", help="показать состояние")
    group.add_argument("--check", action="store_true", help="проверить доступ к Почте и Диску")
    group.add_argument("--remove", action="store_true", help="отключить автозапуск и удалить настройки")
    parser.add_argument("--env-file", help="взять настройки из файла KEY=VALUE вместо сохранённых")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _check(config: Config) -> int:
    code = EXIT_OK
    try:
        check_mail(config)
        print("✓ Почта: вход выполнен")
    except MailError as error:
        print(f"✗ Почта: {error}")
        code = EXIT_ERRORS
    try:
        check_disk(config)
        print(f"✓ Диск: доступ есть ({'токен' if config.disk_mode == 'token' else 'пароль приложения'})")
    except DiskError as error:
        print(f"✗ Диск: {error}")
        code = EXIT_ERRORS
    return code


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    interactive = not (args.run or args.status or args.check or args.remove)
    setup_logging(console=True)
    try:
        if args.run or args.check or args.status:
            config = _load(args.env_file)
            if args.status:
                print_status(config)
                return EXIT_OK
            if config is None:
                log.error("Программа не настроена. Запустите её двойным щелчком и пройдите настройку.")
                return EXIT_NOT_CONFIGURED
            return _check(config) if args.check else do_run(config)
        if args.remove:
            return do_remove()
        if args.setup:
            return do_setup(load_config(config_path()))
        return interactive_menu()
    except ConfigError as error:
        log.error(str(error))
        return EXIT_NOT_CONFIGURED
    except KeyboardInterrupt:
        return EXIT_ERRORS
    except Exception:
        log.exception("Непредвиденная ошибка")
        return EXIT_ERRORS
    finally:
        if interactive and sys.stdin and sys.stdin.isatty():
            try:
                input("\nНажмите Enter, чтобы закрыть окно...")
            except (EOFError, KeyboardInterrupt):
                pass
