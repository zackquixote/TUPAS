"""Prune the ActivityLog trail and expired sessions."""

from datetime import timedelta

from django.contrib.sessions.management.commands.clearsessions import (
    Command as ClearSessionsCommand,
)
from django.core.management.base import BaseCommand
from django.utils import timezone

from tupas.models import ActivityLog


class Command(BaseCommand):
    help = (
        'Delete ActivityLog rows older than --days (default TUPAS_LOG_RETENTION_DAYS '
        'or 180) and expired sessions. The trail is otherwise append-only.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Retention window in days. Default: TUPAS_LOG_RETENTION_DAYS or 180.',
        )
        parser.add_argument(
            '--keep-sessions',
            action='store_true',
            help='Do not also run clearsessions.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be deleted without deleting.',
        )

    def handle(self, *args, **options):
        from django.conf import settings

        days = options['days']
        if days is None:
            days = int(getattr(settings, 'TUPAS_LOG_RETENTION_DAYS', 180))
        days = max(1, int(days))
        cutoff = timezone.now() - timedelta(days=days)

        qs = ActivityLog.objects.filter(created_at__lt=cutoff)
        count = qs.count()
        if options['dry_run']:
            self.stdout.write(f'Would delete {count} ActivityLog row(s) older than {cutoff}.')
        else:
            deleted_total, by_model = qs.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Deleted {by_model.get("tupas.ActivityLog", deleted_total)} '
                    f'ActivityLog row(s) older than {days} days.'
                )
            )

        if not options['keep_sessions']:
            ClearSessionsCommand().execute(stdout=self.stdout)
            self.stdout.write('Cleared expired sessions.')
