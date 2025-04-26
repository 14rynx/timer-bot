import logging
from datetime import datetime, timezone, timedelta
from json import JSONDecodeError

from discord import Interaction
from preston import Preston

from models import Character, User

# Configure the logger
logger = logging.getLogger('discord.timer.warnings')

sent_warnings = {}


async def send_background_warning(channel, warning: tuple[str, str]):
    """Send a warning message to a user from a background process, making sure
    not to repeat the warning to many times and spamming the user"""

    warning_text, log_text = warning

    if log_text in sent_warnings and sent_warnings[log_text] > datetime.now(tz=timezone.utc).timestamp():
        logger.debug(f"Received warning {log_text}, waiting for next window at {sent_warnings[log_text]}")
    else:
        try:
            await channel.send(warning_text)
            logger.info(f"Sent warning {log_text}.")
        except Exception as e:
            logger.warning(f"Could not send warning {log_text}: {e}")
        else:
            # Mark this exact warning as already sent
            sent_warnings[log_text] = (datetime.now(tz=timezone.utc) + timedelta(days=1)).timestamp()


async def send_foreground_warning(interaction: Interaction, warning: tuple[str, str]):
    """Send an immediate warning to a foreground interaction."""
    warning_text, log_text = warning
    await interaction.followup.send(warning_text)


async def esi_permission_warning(character: Character, preston: Preston):
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

    log_text = f"esi_permission_warning for {character}"

    return warning_text, log_text


async def structure_permission_warning(character: Character, authed_preston: Preston):
    """A warning to users to fix in corporation permissions."""

    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        f"The following character does not have permissions to see structure info: {character_name}\n"
        f"- If you to not intend to use this character, remove him with `!revoke {character_name}`.\n"
        "- Otherwise, fix your corp permissions in-game. To do that, go to \"Corporation\" -> \"Administration\" -> "
        "\"Role Management\" -> \"Station Services\" and add the \"Station Manager\" role, then check them with `!info`."
    )

    log_text = f"structure_permission_warning for {character}"

    return warning_text, log_text


async def structure_corp_warning(character: Character, authed_preston: Preston):
    """A warning to users who have changed corp."""
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        "The following character has changed corporation and can thus no "
        f"longer see structure info of the old corporation: {character_name}.\n"
        "- If you to not intend to use this character, remove him with `!revoke {character_name}`.\n"
        "- If you want to use this character again with the new corporation, `!auth` again.\n"
        "- If you want to use this character again with the old corporation, re-join the old corporation in-game."
    )

    log_text = f"structure_corp_warning for {character}"

    return warning_text, log_text


async def structure_other_warning(character: Character, authed_preston: Preston, error_value: str):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = (
        "### WARNING\n"
        f"The following character does not have permissions to see structure info: {character_name}\n"
        f"This is due to the following error: {error_value}\n"
        "There are no specific instructions to fix this error, so you have to try for yourself.\n"
        "- In case you no longer need structure pings, you can remove the character with `!revoke {character_name}`.\n"
        "- Otherwise you can check if your permissions are correct with `!info`."
    )

    log_text = f"structure_other_warning for {character}"

    return warning_text, log_text


async def channel_warning(user):
    """A warning that the channel might not be reachable anymore"""
    warning_text = (
        "### WARNING\n"
        "The channel you are using for timer-bot callbacks is a private channel which the bot might eventually no longer "
        "be able to reach. Please use a channel in a server as your callback location."
    )

    log_text = f"channel_warning for {user}"
    return warning_text, log_text
