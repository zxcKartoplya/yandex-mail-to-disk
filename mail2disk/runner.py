import logging
import os
import time
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import Callable, Iterable

from .config import Config
from .disk import make_disk
from .imap_source import MailSource
from .paths import lock_path, log_path, state_path
from .state import DiskStateStore, FileStateStore
from .sync import Report, Syncer

STALE_LOCK_SECONDS = 3 * 60 * 60
STATE_FILE_ON_DISK = ".mail2disk-state.json"
RETRY_DELAYS = (60, 300)
RETRY_INTERVAL = 15 * 60
RETRY_WINDOW_SECONDS = 3 * 60 * 60


class AlreadyRunning(Exception):
    pass


def setup_logging(console: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    file_handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console_handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@contextmanager
def run_lock():
    path = lock_path()
    if path.exists() and time.time() - path.stat().st_mtime > STALE_LOCK_SECONDS:
        path.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise AlreadyRunning("Выгрузка уже идёт в другом окне. Дождитесь её окончания.") from error
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def run_once(config: Config, state_on_disk: bool = False) -> Report:
    config.validate()
    disk = make_disk(config)
    mail = MailSource(config.imap_server, config.email, config.mail_password)
    if state_on_disk:
        store = DiskStateStore(disk, f"{config.disk_folder}/{STATE_FILE_ON_DISK}")
    else:
        store = FileStateStore(state_path())
    log = logging.getLogger(__name__)
    log.info("Начинаю выгрузку: %s → Яндекс Диск/%s", config.email, config.disk_folder)
    report = Syncer(config, mail, disk, store).run()
    log.info("Итог:\n%s", report.summary())
    return report


def run_with_retries(
    config: Config,
    retry_delays: Iterable[int] = RETRY_DELAYS,
    retry_interval: int = RETRY_INTERVAL,
    retry_window_seconds: int = RETRY_WINDOW_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    run_id: str | None = None,
) -> Report:
    delays = tuple(retry_delays)
    started = clock()
    attempt = 1
    run_id = run_id or uuid.uuid4().hex[:8]
    log = logging.getLogger(__name__)
    log.info("Запуск выгрузки %s (PID %d).", run_id, os.getpid())
    while True:
        report = run_once(config)
        if report.ok:
            log.info("Запуск выгрузки %s завершён успешно после %d попыток.", run_id, attempt)
            return report
        if not report.retryable:
            log.error("Запуск выгрузки %s завершён без повтора: ошибка не временная.", run_id)
            return report
        delay = delays[attempt - 1] if attempt <= len(delays) else retry_interval
        if clock() - started + delay > retry_window_seconds:
            log.error(
                "Запуск выгрузки %s завершён после %d попыток: исчерпан лимит повторов %d мин.",
                run_id,
                attempt,
                retry_window_seconds // 60,
            )
            return report
        log.warning(
            "Временный сбой в запуске %s. Повторная попытка %d через %d мин.",
            run_id,
            attempt + 1,
            max(1, delay // 60),
        )
        sleep(delay)
        attempt += 1
