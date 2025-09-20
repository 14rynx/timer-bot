from peewee import *
from datetime import datetime, UTC

# Initialize the database
db = SqliteDatabase('data/bot.sqlite')


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    user_id = CharField(primary_key=True)
    callback_channel_id = CharField()

    def __repr__(self):
        return f"User(user_id={self.user_id}, callback_channel_id={self.callback_channel_id})"

    def __str__(self):
        return f"User {self.user_id}"


class Character(BaseModel):
    character_id = CharField(primary_key=True)
    corporation_id = CharField()
    user = ForeignKeyField(User, backref='characters')
    token = TextField()

    def __repr__(self):
        return f"Character(character_id={self.character_id}, corporation_id{self.corporation_id}, user_id={self.user.user_id}, token={self.token})"

    def __str__(self):
        return f"Character(character_id={self.character_id}, corporation_id={self.corporation_id} user={self.user})"


class Challenge(BaseModel):
    user = ForeignKeyField(User, backref='challenges')
    state = CharField()


class Notification(BaseModel):
    notification_id = CharField()
    timestamp = DateTimeField()
    sent = BooleanField(default=False)

    class Meta:
        primary_key = CompositeKey('notification_id', 'timestamp')


class Structure(BaseModel):
    structure_id = CharField(primary_key=True)
    last_state = CharField()
    last_fuel_warning = IntegerField()


class Migration(BaseModel):
    name = CharField(unique=True)
    applied_at = DateTimeField(default=lambda: datetime.now(UTC))


def initialize_database():
    with db:
        db.create_tables([User, Character, Challenge, Notification, Structure, Migration])