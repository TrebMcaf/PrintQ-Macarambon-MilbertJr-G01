from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Show which database PrintQ is connected to and confirm the connection works."

    def handle(self, *args, **opts):
        s = connection.settings_dict
        self.stdout.write(f"Engine:  {s['ENGINE']}")
        if connection.vendor == "sqlite":
            self.stdout.write(self.style.WARNING(f"SQLite file: {s['NAME']} (set DATABASE_URL to use Supabase)"))
            return
        self.stdout.write(f"Host:    {s.get('HOST')}:{s.get('PORT')}")
        self.stdout.write(f"DB/user: {s.get('NAME')} / {s.get('USER')}")
        try:
            with connection.cursor() as cur:
                cur.execute("select version()")
                version = cur.fetchone()[0]
                cur.execute(
                    "select count(*) from information_schema.tables "
                    "where table_schema = 'public' and table_name = any(%s)",
                    [["users", "shops", "print_rates", "orders", "order_items", "order_status_logs"]],
                )
                found = cur.fetchone()[0]
        except Exception as exc:  # report connection problems plainly
            raise CommandError(f"Could not connect: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Connected: {version.split(',')[0]}"))
        self.stdout.write(f"PrintQ tables present: {found}/6" + ("" if found == 6 else "  → run: python manage.py migrate"))
