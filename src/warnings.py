import logging
from datetime import datetime, timezone, timedelta

# Configure the logger
logger = logging.getLogger('discord.warnings')
logger.setLevel(logging.INFO)


async def send_warning(user, channel, warning_text, log_text=""):
    """Send a warning message to the user, logs if it was successful
    and sets the warning delay for said user."""
    if user.next_warning < datetime.now(tz=timezone.utc).timestamp():
        try:
            await channel.send(warning_text)
            logger.info(f"Sent warning {log_text}.")
        except Exception as e:
            logger.warning(f"Could not send warning {log_text}: {e}")
        user.next_warning = (datetime.now(tz=timezone.utc) + timedelta(days=3)).timestamp()


async def send_notification_permission_warning(user, channel, authed_preston):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = f"""
    ### Warning
    The following character does not have permissions to see notification info:
    {character_name}. If you to not intend to use this character, remove him with 
    `!revoke {character_name}` otherwise, fix your notification permissions in-game.
    """

    await send_warning(user, channel, warning_text, log_text="channel")


async def send_structure_permission_warning(user, channel, authed_preston):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = f"""
    ### Warning
    The following character does not have permissions to see structure info:
    {character_name}. If you to not intend to use this character, remove him with 
    `!revoke {character_name}`. Otherwise, fix your corp permissions in-game and 
    check them with `!info`.
    """

    await send_warning(user, channel, warning_text, log_text="channel")


async def send_channel_warning(user, channel):
    warning_text = f"""
    ### Warning
    The channel you are using for timer-bot callbacks is a private channel
    which the bot might eventually no longer be able to reach. Please use
    a channel in a server as your callback location
    """

    await send_warning(user, channel, warning_text, log_text="channel")
