import logging

from mail2disk.config import Config
from mail2disk.runner import run_once

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    report = run_once(Config.from_env(), state_on_disk=True)
    return {"statusCode": 200 if report.ok else 500, "body": report.summary()}
