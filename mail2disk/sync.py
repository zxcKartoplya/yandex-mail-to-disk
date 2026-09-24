import email
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .attachments import Attachment, extract_attachments
from .config import Config
from .disk import DiskAuthError, DiskConnectionError, DiskError
from .imap_source import Folder, MailAuthError, MailConnectionError, MailError, should_scan
from .names import decode_filename, numbered_filename
from .state import FolderState, State

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_SAME_NAMES = 500
FATAL_ERRORS = (DiskAuthError, DiskConnectionError, MailAuthError, MailConnectionError)


@dataclass
class Report:
    messages: int = 0
    uploaded: list[str] = field(default_factory=list)
    duplicates: int = 0
    given_up: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [
            f"Просмотрено новых писем: {self.messages}",
            f"Загружено файлов на Диск: {len(self.uploaded)}",
        ]
        if self.duplicates:
            lines.append(f"Уже были на Диске (пропущены): {self.duplicates}")
        if self.given_up:
            lines.append(f"Письма, которые не удалось обработать после {MAX_ATTEMPTS} попыток: {len(self.given_up)}")
            lines.extend(f"  - {item}" for item in self.given_up)
        if self.errors:
            lines.append("Ошибки:")
            lines.extend(f"  - {item}" for item in self.errors)
        return "\n".join(lines)


class Syncer:
    def __init__(self, config: Config, mail, disk, store, now: datetime | None = None):
        self.config = config
        self.mail = mail
        self.disk = disk
        self.store = store
        self.now = now or datetime.now()

    def run(self) -> Report:
        report = Report()
        state = self.store.load()
        try:
            self.disk.ensure_folder(self.config.disk_folder)
            self.mail.connect()
        except (MailError, DiskError) as error:
            report.errors.append(str(error))
            return report
        try:
            folders = [f for f in self.mail.list_folders() if should_scan(f, self.config.include_folders)]
            log.info("Папки для проверки: %s", ", ".join(f.display_name for f in folders) or "нет")
            for folder in folders:
                try:
                    self._sync_folder(folder, state, report)
                except FATAL_ERRORS as error:
                    report.errors.append(str(error))
                    break
                except MailError as error:
                    report.errors.append(f"Папка «{folder.display_name}»: {error}")
        except MailError as error:
            report.errors.append(str(error))
        finally:
            self.mail.close()
        if report.ok:
            state.last_success = self.now.isoformat(timespec="seconds")
        self.store.save(state)
        return report

    def _start_date(self, state: State, reset: bool) -> date:
        if reset and state.last_success:
            return datetime.fromisoformat(state.last_success).date() - timedelta(days=1)
        return self.now.date() - timedelta(days=self.config.first_run_days)

    def _sync_folder(self, folder: Folder, state: State, report: Report) -> None:
        uidvalidity, uidnext = self.mail.folder_status(folder.name)
        known = state.folders.get(folder.name)
        self.mail.open_folder(folder.name)
        if known is not None and known.uidvalidity == uidvalidity:
            cursor = known.last_uid
            uids = self.mail.search_after(cursor)
        else:
            if known is not None:
                log.warning("Папка «%s» была пересоздана на сервере, проверяю письма с даты последней выгрузки.", folder.display_name)
            cursor = 0
            uids = self.mail.search_since(self._start_date(state, reset=known is not None))
        log.info("Папка «%s»: новых писем %d", folder.display_name, len(uids))

        stopped = False
        for uid in uids:
            key = f"{folder.name}|{uidvalidity}|{uid}"
            uploaded_before = len(report.uploaded)
            try:
                self._process_message(uid, report)
            except FATAL_ERRORS:
                state.folders[folder.name] = FolderState(uidvalidity, max(cursor, uid - 1))
                raise
            except (DiskError, MailError) as error:
                attempts = state.failures.get(key, 0) + 1
                if attempts < MAX_ATTEMPTS:
                    state.failures[key] = attempts
                    report.errors.append(
                        f"Письмо {uid} в папке «{folder.display_name}» (попытка {attempts} из {MAX_ATTEMPTS}): {error}"
                    )
                    cursor = max(cursor, uid - 1)
                    stopped = True
                    break
                state.failures.pop(key, None)
                report.given_up.append(f"письмо {uid} в папке «{folder.display_name}»: {error}")
                log.error("Пропускаю письмо %s в папке «%s» после %d попыток: %s", uid, folder.display_name, attempts, error)
            else:
                state.failures.pop(key, None)
            cursor = uid
            state.folders[folder.name] = FolderState(uidvalidity, cursor)
            if len(report.uploaded) != uploaded_before:
                self.store.save(state)

        if not stopped:
            cursor = max(cursor, uidnext - 1)
        state.folders[folder.name] = FolderState(uidvalidity, cursor)

    def _process_message(self, uid: int, report: Report) -> None:
        raw, received = self.mail.fetch(uid)
        message = email.message_from_bytes(raw)
        report.messages += 1
        attachments = extract_attachments(message, self.config.min_inline_image_kb * 1024)
        if not attachments:
            return
        subject = decode_filename(message.get("Subject")) or "(без темы)"
        log.info("Письмо «%s»: вложений %d", subject, len(attachments))
        day = (received or self.now).strftime("%Y-%m-%d")
        target = f"{self.config.disk_folder}/{day}"
        self.disk.ensure_folder(target)
        for attachment in attachments:
            self._upload_unique(target, attachment, report)

    def _upload_unique(self, folder: str, attachment: Attachment, report: Report) -> None:
        md5 = hashlib.md5(attachment.data).hexdigest()
        for number in range(1, MAX_SAME_NAMES + 1):
            path = f"{folder}/{numbered_filename(attachment.filename, number)}"
            existing = self.disk.info(path)
            if existing is None:
                self.disk.upload(path, attachment.data)
                report.uploaded.append(path)
                log.info("Загружен %s (%d КБ)", path, max(1, len(attachment.data) // 1024))
                return
            if existing.md5 == md5:
                report.duplicates += 1
                log.info("Уже есть на Диске: %s", path)
                return
        raise DiskError(f"Слишком много файлов с именем {attachment.filename} в папке {folder}.")
