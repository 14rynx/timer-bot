import logging
from datetime import datetime, timezone, timedelta
import os

import dateutil.parser
from aiohttp import web
from discord.ext import tasks
from preston import Preston

from models import User, Character, Challenge, Notification, db, Structure
from actions.notification import is_structure_notification
from messaging import user_disconnected_count

# Configure the logger
logger = logging.getLogger('discord.timer.callback')


@tasks.loop()
async def webserver(bot, preston: Preston):
    routes = web.RouteTableDef()

    @routes.get('/')
    async def hello(request):
        return web.Response(text="Timer Bot Callback Server (https://github.com/14rynx/timer-bot)")

    @routes.get('/health')
    async def health(request):
        """Health check endpoint that verifies database connectivity."""
        health_status = {
            "status": "healthy",
            "database": "unknown",
            "timestamp": None
        }
        
        try:
            # Test database connection with a simple query
            if db.is_closed():
                db.connect()
            
            # Try to execute a simple query to test the connection
            structure_count = Structure.select().count()

            corporation_count = (
                Character
                .select(Character.corporation_id)
                .distinct()
                .count()
            )

            health_status.update({
                "status": "healthy",
                "database": "connected",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "counts": {
                    "structures": structure_count,
                    "corporations": corporation_count,
                },
            })
            
            logger.debug("Health check passed")
            return web.json_response(health_status, status=200)
            
        except Exception as e:
            health_status.update({
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })
            
            logger.warning(f"Health check failed: {e}")
            return web.json_response(health_status, status=503)

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
            authed_preston = await preston.authenticate(code)
        except Exception as e:
            logger.error(e)
            logger.warning("Failed to verify token")
            return web.Response(text="Authentication failed!", status=403)

        # Get character data
        character_data = await authed_preston.whoami()
        character_id = character_data.get("character_id")
        character_name = character_data.get("character_name")

        try:
            # Try fast affiliation API
            corporation_id = (await preston.post_op(
                'post_characters_affiliation',
                path_data={},
                post_data=[character_id]
            ))[0].get("corporation_id")
        except Exception as e:
            # Fall back to slow character API
            corporation_id = (await preston.get_op(
                'get_characters_character_id',
                character_id=character_id
            )).get("corporation_id")

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
        notifications = await authed_preston.get_op(
            "get_characters_character_id_notifications",
            character_id=character_id,
        )

        for notification in notifications:
            if is_structure_notification(notification):
                timestamp = dateutil.parser.isoparse(notification.get("timestamp"))

                if timestamp < datetime.now(timezone.utc) - timedelta(days=1):
                    continue

                notification, created = Notification.get_or_create(
                    notification_id=str(notification.get("notification_id")),
                    timestamp=timestamp
                )
                notification.sent = True
                notification.save()

        logger.info(f"Added character {character}.")
        if created:
            return web.Response(text=f"Successfully authenticated {character_name}!")
        else:
            return web.Response(text=f"Successfully re-authenticated {character_name}!")

    @routes.get('/unreachable')
    async def unreachable(request):
        """Return list of users who currently have no valid channel."""

        users_data = []
        for u, count in user_disconnected_count.items():
            user_id = getattr(u, "user_id", None)
            if not user_id:
                continue

            discord_user = None
            try:
                discord_user = await bot.fetch_user(int(user_id))
            except Exception as e:
                logger.debug(f"Failed to fetch user {user_id}: {e}")

            users_data.append({
                "user_id": str(user_id),
                "handle": f"{discord_user}" if discord_user else "<unknown>",
                "name": getattr(discord_user, "name", None),
                "discriminator": getattr(discord_user, "discriminator", None),
                "attempts": count,
            })

        return web.json_response({
            "count": len(users_data),
            "users": users_data
        })

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=int(os.getenv('CALLBACK_PORT', '80')))
    try:
        await site.start()
    except OSError:
        return # Callback already running
    else:
        logger.info("callback_server started")
