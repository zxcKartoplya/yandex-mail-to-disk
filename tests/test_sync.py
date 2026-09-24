from datetime import datetime, timedelta

from mail2disk.config import Config
from mail2disk.sync import MAX_ATTEMPTS, Syncer

from .fakes import FakeDisk, FakeFolder, FakeMail, MemoryStore, make_message

NOW = datetime(2026, 9, 24, 9, 0)
ROOT = "Вложения из почты"


def config(**overrides):
    base = dict(email="me@yandex.ru", mail_password="x", disk_password="y", first_run_days=7)
    base.update(overrides)
    return Config(**base)


def msg(name="file.pdf", data=b"DATA", days_ago=0, subject="Тема"):
    return make_message(subject=subject, attachments=[(name, data)]), NOW - timedelta(days=days_ago)


def run(mail, disk, store, now=NOW, **overrides):
    return Syncer(config(**overrides), mail, disk, store, now=now).run()


def test_first_run_uploads_last_n_days_into_date_folders():
    inbox = FakeFolder(messages={1: msg("old.pdf", days_ago=30), 2: msg("a.pdf", days_ago=1), 3: msg("b.pdf")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()

    report = run(mail, disk, store)

    assert report.ok
    assert set(disk.files) == {f"{ROOT}/2026-09-23/a.pdf", f"{ROOT}/2026-09-24/b.pdf"}
    assert store.state.folders["INBOX"].last_uid == 3
    assert store.state.last_success == "2026-09-24T09:00:00"
    assert mail.connected is False


def test_second_run_takes_only_new_messages():
    inbox = FakeFolder(messages={1: msg("a.pdf")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    run(mail, disk, store)

    inbox.messages[2] = msg("b.pdf", data=b"NEW")
    mail.fetched.clear()
    report = run(mail, disk, store, now=NOW + timedelta(days=1))

    assert mail.fetched == [("INBOX", 2)]
    assert len(report.uploaded) == 1
    assert f"{ROOT}/2026-09-24/b.pdf" in disk.files


def test_missed_days_are_caught_up():
    inbox = FakeFolder(messages={1: msg("a.pdf")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    run(mail, disk, store)

    inbox.messages[2] = msg("day2.pdf", data=b"2")
    inbox.messages[3] = msg("day3.pdf", data=b"3")
    report = run(mail, disk, store, now=NOW + timedelta(days=5))

    assert len(report.uploaded) == 2


def test_same_name_different_content_is_not_overwritten():
    inbox = FakeFolder(messages={1: msg("invoice.pdf", b"ONE"), 2: msg("invoice.pdf", b"TWO")})
    disk = FakeDisk()
    run(FakeMail({"INBOX": inbox}), disk, MemoryStore())

    assert disk.files[f"{ROOT}/2026-09-24/invoice.pdf"] == b"ONE"
    assert disk.files[f"{ROOT}/2026-09-24/invoice (2).pdf"] == b"TWO"


def test_identical_file_is_not_uploaded_twice():
    inbox = FakeFolder(messages={1: msg("invoice.pdf", b"SAME"), 2: msg("invoice.pdf", b"SAME")})
    disk = FakeDisk()
    report = run(FakeMail({"INBOX": inbox}), disk, MemoryStore())

    assert len(disk.files) == 1
    assert report.duplicates == 1


def test_rerun_after_crash_does_not_duplicate():
    inbox = FakeFolder(messages={1: msg("a.pdf")})
    disk = FakeDisk()
    run(FakeMail({"INBOX": inbox}), disk, MemoryStore())
    run(FakeMail({"INBOX": inbox}), disk, MemoryStore())

    assert list(disk.files) == [f"{ROOT}/2026-09-24/a.pdf"]


def test_spam_trash_sent_are_skipped():
    folders = {
        "INBOX": FakeFolder(messages={1: msg("in.pdf", b"1")}),
        "Работа": FakeFolder(messages={1: msg("work.pdf", b"2")}),
        "Spam": FakeFolder(messages={1: msg("spam.pdf", b"3")}, flags=frozenset({"\\junk"})),
        "Sent": FakeFolder(messages={1: msg("sent.pdf", b"4")}, flags=frozenset({"\\sent"})),
    }
    disk = FakeDisk()
    run(FakeMail(folders), disk, MemoryStore())

    names = sorted(path.rsplit("/", 1)[1] for path in disk.files)
    assert names == ["in.pdf", "work.pdf"]


def test_empty_folder_on_first_run_sets_baseline():
    inbox = FakeFolder(messages={1: msg("old.pdf", days_ago=100)})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    run(mail, disk, store)

    assert store.state.folders["INBOX"].last_uid == 1
    run(mail, disk, store)
    assert disk.files == {}


def test_temporary_upload_failure_is_retried_next_time():
    inbox = FakeFolder(messages={1: msg("a.pdf", b"A"), 2: msg("b.pdf", b"B")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    disk.fail_uploads = 1

    first = run(mail, disk, store)
    assert not first.ok
    assert disk.files == {}
    assert store.state.folders["INBOX"].last_uid == 0
    assert store.state.last_success is None

    second = run(mail, disk, store)
    assert second.ok
    assert len(disk.files) == 2
    assert store.state.failures == {}


def test_failure_on_later_message_keeps_earlier_progress():
    inbox = FakeFolder(messages={1: msg("a.pdf", b"A"), 2: msg("b.pdf", b"B"), 3: msg("c.pdf", b"C")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    mail.fetch_errors = {2: 1}

    run(mail, disk, store)
    assert store.state.folders["INBOX"].last_uid == 1

    mail.fetched.clear()
    run(mail, disk, store)
    assert mail.fetched == [("INBOX", 2), ("INBOX", 3)]
    assert len(disk.files) == 3


def test_permanently_broken_message_is_skipped_after_max_attempts():
    inbox = FakeFolder(messages={1: msg("a.pdf", b"A"), 2: msg("b.pdf", b"B")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    mail.fetch_errors = {1: 100}

    reports = [run(mail, disk, store) for _ in range(MAX_ATTEMPTS)]

    assert all(not r.ok for r in reports[:-1])
    assert reports[-1].given_up
    assert list(disk.files) == [f"{ROOT}/2026-09-24/b.pdf"]
    assert store.state.folders["INBOX"].last_uid == 2


def test_uidvalidity_change_rescans_from_last_success():
    inbox = FakeFolder(messages={1: msg("a.pdf", b"A")})
    mail, disk, store = FakeMail({"INBOX": inbox}), FakeDisk(), MemoryStore()
    run(mail, disk, store)

    inbox.uidvalidity = 2
    inbox.messages = {10: msg("a.pdf", b"A"), 11: msg("new.pdf", b"N")}
    mail.since_calls.clear()
    report = run(mail, disk, store, now=NOW + timedelta(days=1))

    assert mail.since_calls == [NOW.date() - timedelta(days=1)]
    assert report.duplicates == 1
    assert len(report.uploaded) == 1
    assert store.state.folders["INBOX"].uidvalidity == 2


def test_disk_auth_error_stops_everything():
    inbox = FakeFolder(messages={1: msg("a.pdf")})
    disk = FakeDisk()
    disk.auth_broken = True
    report = run(FakeMail({"INBOX": inbox}), disk, MemoryStore())

    assert not report.ok
    assert "нет доступа" in report.errors[0]


def test_messages_without_attachments_do_not_create_folders():
    inbox = FakeFolder(messages={1: (make_message(), NOW)})
    disk = FakeDisk()
    report = run(FakeMail({"INBOX": inbox}), disk, MemoryStore())

    assert report.ok and report.messages == 1
    assert disk.folders == {ROOT}
