from models import db, Notification, User, Migration
from peewee import Model, CharField, BooleanField, DateTimeField, IntegerField
from datetime import datetime

def run_migration():
    with db.atomic():
        if Migration.select().where(Migration.name == "2025_07_12_notification_schema").exists():
            return  # Already run

        db.execute_sql("ALTER TABLE user RENAME TO user_old;")

        db.create_tables([User])

        class OldUser(Model):
            user_id = CharField(primary_key=True)
            callback_channel_id = CharField()
            next_warning = IntegerField(default=0)

            class Meta:
                database = db
                table_name = "user_old"


        for old in OldUser.select():
            User.create(
                user_id=old.user_id,
                callback_channel_id=old.callback_channel_id,
            )

        db.execute_sql("DROP TABLE user_old;")

        Migration.create(name="2025_07_08_notification_schema")
