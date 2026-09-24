import logging
import os
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from .config import Config
from .disk import make_disk
from .imap_source import MailSource
from .paths import lock_path, log_path, state_path
from .state import DiskStateStore, FileStateStore
from .sync import Report, Syncer

STALE_LOCK_SECONDS = 3 * 60 * 60
STATE_FILE_ON_DISK = ".mail2disk-state.json"


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
