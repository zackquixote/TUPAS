"""Logging filters shared by the LOGGING dict in config/settings.py."""

import logging


class SkipBrokenPipeFilter(logging.Filter):
    """Drop dev-server 'Broken pipe' lines from the console.

    They are client disconnects, not application errors, and they drown
    real signal. They stay out of the way; the access log still records
    the requests that completed.
    """

    def filter(self, record):
        try:
            return 'Broken pipe' not in record.getMessage()
        except Exception:
            return True


class RecordDefaultsFilter(logging.Filter):
    """Guarantee request_id / username exist for the file formatter."""

    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = '-'
        if not hasattr(record, 'username'):
            record.username = '-'
        return True
