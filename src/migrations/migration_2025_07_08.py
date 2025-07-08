from models import db, Notification, User, Migration
from peewee import Model, CharField, BooleanField, DateTimeField, IntegerField
from datetime import datetime

def run_migration():
    with db.atomic():
        if Migration.select().where(Migration.name == "2025_07_08_notification_schema").exists():
            return  # Already run

        db.execute_sql("ALTER TABLE notification RENAME TO notification_old;")
        db.execute_sql("ALTER TABLE user RENAME TO user_old;")

        db.create_tables([Notification, User])

        class OldNotification(Model):
            notification_id = CharField(primary_key=True)
            sent = BooleanField(default=False)

            class Meta:
                database = db
                table_name = "notification_old"

        class OldUser(Model):
            user_id = CharField(primary_key=True)
            callback_channel_id = CharField()
            next_warning = IntegerField(default=0)

            class Meta:
                database = db
                table_name = "user_old"

        for old in OldNotification.select():
            Notification.create(
                notification_id=old.notification_id,
                timestamp=datetime.utcnow(),
                sent=old.sent,
            )

        for old in OldUser.select():
            User.create(
                user_id=old.user_id,
                callback_channel_id=old.callback_channel_id,
            )

        # Step 5: Drop old tables
        db.execute_sql("DROP TABLE notification_old;")
        db.execute_sql("DROP TABLE user_old;")

        # Step 6: Mark migration as applied
        Migration.create(name="2025_07_08_notification_schema")
