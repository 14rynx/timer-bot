import logging
from datetime import datetime, timezone, timedelta
from json import JSONDecodeError

from preston import Preston

from models import Character, User

# Configure the logger
logger = logging.getLogger('discord.timer.warnings')

async def send_warning(user: User, channel, warning_text: str, log_text: str = "", send_now: bool = False):
    """Send a warning message to the user, logs if it was successful
    and sets the warning delay for said user."""
    if user.next_warning < datetime.now(tz=timezone.utc).timestamp() or send_now:
        try:
            await channel.send(warning_text)
            logger.info(f"Sent warning {log_text}.")
        except Exception as e:
            logger.warning(f"Could not send warning {log_text}: {e}")
        else:
            if not send_now:
                user.next_warning = (datetime.now(tz=timezone.utc) + timedelta(days=1)).timestamp()
                user.save()
    else:
        logger.debug(f"Received warning {log_text}, waiting for next window at {user.next_warning}")


async def send_esi_permission_warning(character: Character, channel, preston: Preston, **kwargs):
    """Send a warning to users to fix ESI permissions."""
    try:
        character_name = preston.get_op(
            'get_characters_character_id',
            character_id=character.character_id
        ).get("name")

        warning_text = (
            "### WARNING\n"
            f"The following character does not have permissions to fetch data from ESI: {character_name}\n"
            "- If you to not intend to use this character anymore, remove him with `!revoke {character_name}`.\n"
            "- Otherwise re-authenticate with `!auth`."
        )
    except (ValueError, KeyError, JSONDecodeError):
        warning_text = (
            "### WARNING\n"
            "One of your characters does not have permissions to fetch data from ESI.\n"
            "- If you to not intend to use this bot anymore, remove your characters with `!revoke`.\n"
            "- Otherwise re-authenticate with `!auth`."
        )

    await send_warning(character.user, channel, warning_text, log_text=f"esi_permission_warning for {character}",
                       **kwargs)


async def send_structure_permission_warning(character: Character, channel, authed_preston: Preston, **kwargs):
    """Send a warning to users to fix in corporation permissions."""

    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        f"The following character does not have permissions to see structure info: {character_name}\n"
        f"- If you to not intend to use this character, remove him with `!revoke {character_name}`.\n"
        "- Otherwise, fix your corp permissions in-game. To do that, go to \"Corporation\" -> \"Administration\" -> "
        "\"Role Management\" -> \"Station Services\" and add the \"Station Manager\" role, then check them with `!info`."
    )

    await send_warning(character.user, channel, warning_text, log_text=f"structure_permission_warning for {character}",
                       **kwargs)


async def send_structure_corp_warning(character: Character, channel, authed_preston: Preston, **kwargs):
    """Send a warning to users who have changed corp."""
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        "The following character has changed corporation and can thus no "
        f"longer see structure info of the old corporation: {character_name}.\n"
        "- If you to not intend to use this character, remove him with `!revoke {character_name}`.\n"
        "- If you want to use this character again with the new corporation, `!auth` again.\n"
        "- If you want to use this character again with the old corporation, re-join the old corporation in-game."
    )

    await send_warning(character.user, channel, warning_text, log_text=f"structure_corp_warning for {character}",
                       **kwargs)


async def send_structure_other_warning(character: Character, channel, authed_preston: Preston, error_value: str,
                                       **kwargs):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        f"The following character does not have permissions to see structure info: {character_name}\n"
        f"This is due to the following error: {error_value}\n"
        "There are no specific instructions to fix this error, so you have to try for yourself.\n"
        "- In case you no longer need structure pings, you can remove the character with `!revoke {character_name}`.\n"
        "- Otherwise you can check if your permissions are correct with `!info`."
    )

    await send_warning(character.user, channel, warning_text, log_text=f"structure_other_warning for {character}",
                       **kwargs)


async def send_channel_warning(user, channel, **kwargs):
    warning_text = (
        "### WARNING\n"
        "The channel you are using for timer-bot callbacks is a private channel which the bot might eventually no longer "
        "be able to reach. Please use a channel in a server as your callback location."
    )

    await send_warning(user, channel, warning_text, log_text=f"channel_warning for {user}", **kwargs)