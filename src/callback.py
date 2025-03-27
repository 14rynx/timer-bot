import logging

from aiohttp import web
from discord.ext import tasks
from preston import Preston

from models import User, Character, Challenge, Notification
from structure import is_structure_notification

# Configure the logger
logger = logging.getLogger('discord.timer.callback')

@tasks.loop()
async def callback_server(preston: Preston):
    routes = web.RouteTableDef()

    @routes.get('/')
    async def hello(request):
        return web.Response(text="Hangar Script Callback Server")

    @routes.get('/callback/')
    async def callback(request):
        # Get the code and state from the login process
        code = request.query.get('code')
        state = request.query.get('state')

        # Verify the state and get the user ID
        challenge = Challenge.get_or_none(Challenge.state == state)
        if not challenge:
            logger.warning("Failed to verify challenge")
            return web.Response(text="Authentication failed: State mismatch", status=403)

        # Authenticate using the code
        try:
            authed_preston = preston.authenticate(code)
        except Exception as e:
            logger.error(e)
            logger.warning("Failed to verify token")
            return web.Response(text="Authentication failed!", status=403)

        # Get character data
        character_data = authed_preston.whoami()
        character_id = character_data["CharacterID"]
        character_name = character_data["CharacterName"]

        corporation_id = authed_preston.get_op(
            'get_characters_character_id',
            character_id=character_id
        ).get("corporation_id")

        # Create / Update user and store refresh_token
        user = User.get_or_none(user_id=challenge.user.user_id)

        if not user:
            return web.Response(text=f"Error: User does not exist!", status=400)

        character, created = Character.get_or_create(
            character_id=character_id, user=user,
            defaults={"token": authed_preston.refresh_token, "corporation_id": corporation_id}
        )
        character.corporation_id = corporation_id
        character.token = authed_preston.refresh_token
        character.save()

        # Mark old notifications as skipped
        notifications = authed_preston.get_op(
            "get_characters_character_id_notifications",
            character_id=character_id,
        )

        for notification in notifications:
            if is_structure_notification(notification):
                notification, created = Notification.get_or_create(
                    notification_id=str(notification.get("notification_id")),
                )
                notification.sent = True
                notification.save()

        logger.info(f"Added character {character}.")
        if created:
            return web.Response(text=f"Successfully authenticated {character_name}!")
        else:
            return web.Response(text=f"Successfully re-authenticated {character_name}!")

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=80)
    await site.start()
