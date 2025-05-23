from peewee import *

# Initialize the database
db = SqliteDatabase('data/bot.sqlite')


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    user_id = CharField(primary_key=True)
    callback_channel_id = CharField()
    next_warning = IntegerField(default=0) # This is no longer used

    def __repr__(self):
        return f"User(user_id={self.user_id}, callback_channel_id={self.callback_channel_id}, next_warning={self.next_warning})"

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
        return f"Character {self.character_id} by User {self.user.user_id}"


class Challenge(BaseModel):
    user = ForeignKeyField(User, backref='challenges')
    state = CharField()


class Notification(BaseModel):
    notification_id = CharField(primary_key=True)
    sent = BooleanField(default=False)


class Structure(BaseModel):
    structure_id = CharField(primary_key=True)
    last_state = CharField()
    last_fuel_warning = IntegerField()


def initialize_database():
    with db:
        db.create_tables([User, Character, Challenge, Notification, Structure])
