import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from json import JSONDecodeError

import aiohttp
from discord import Interaction
from preston import Preston

from models import Character
from utils import send_background_message

# Configure the logger
logger = logging.getLogger('discord.timer.warnings')

sent_warnings = {}
no_channel_characters = set()
disconnected_character_cycles = defaultdict(int)


async def send_background_warning(bot, user, warning: tuple[str, str], quiet: bool = False):
    """Send a warning message to a user from a background process, making sure
    not to repeat the warning to many times and spamming the user"""

    warning_text, log_text = warning

    if log_text in sent_warnings and sent_warnings[log_text] > datetime.now(tz=timezone.utc).timestamp():
        logger.debug(f"Received warning {log_text}, waiting for next window at {sent_warnings[log_text]}")
        return True
    else:
        if await send_background_message(bot, user, warning_text, quiet=quiet):
            sent_warnings[log_text] = (datetime.now(tz=timezone.utc) + timedelta(days=1)).timestamp()
            return True
        else:
            return False


async def send_foreground_warning(interaction: Interaction, warning: tuple[str, str]):
    """Send an immediate warning to a foreground interaction."""
    warning_text, log_text = warning
    await interaction.followup.send(warning_text)


async def esi_permission_warning(character: Character, preston: Preston):
    """Send a warning to users to fix ESI permissions."""
    try:
        character_name = await preston.get_op(
            'get_characters_character_id',
            character_id=character.character_id
        ).get("name")

        warning_text = (
            "### WARNING\n"
            f"<@{character.user.user_id}>, the following character does not have permissions to fetch data from ESI: {character_name}\n"
            "- If you to not intend to use this character anymore, remove him with `/revoke {character_name}`.\n"
            "- Otherwise re-authenticate with `/auth`."
        )
    except (ValueError, KeyError, JSONDecodeError):
        warning_text = (
            "### WARNING\n"
            f"<@{character.user.user_id}>, your characters do not have permissions to fetch data from ESI.\n"
            "- If you to not intend to use this bot anymore, remove your characters with `/revoke`.\n"
            "- Otherwise re-authenticate with `/auth`."
        )

    log_text = f"esi_permission_warning for {character}"

    return warning_text, log_text


async def structure_permission_warning(character: Character, authed_preston: Preston):
    """A warning to users to fix in corporation permissions."""

    character_name = (await authed_preston.whoami()).get("character_name")

    warning_text = (
        "### WARNING\n"
        f"<@{character.user.user_id}>, The following character does not have permissions to see structure info: {character_name}\n"
        f"- If you to not intend to use this character, remove him with `/revoke {character_name}`.\n"
        "- Otherwise, fix your corp permissions in-game. To do that, go to \"Corporation\" -> \"Administration\" -> "
        "\"Role Management\" -> \"Station Services\" and add the \"Station Manager\" role, then check them with `/info`."
    )

    log_text = f"structure_permission_warning for {character}"

    return warning_text, log_text


async def structure_corp_warning(character: Character, authed_preston: Preston):
    """A warning to users who have changed corp."""
    character_name = (await authed_preston.whoami()).get("character_name")

    warning_text = (
        "### WARNING\n"
        f"<@{character.user.user_id}>, the following character has changed corporation and can thus no "
        f"longer see structure info of the old corporation: {character_name}.\n"
        "- If you to not intend to use this character, remove him with `/revoke {character_name}`.\n"
        "- If you want to use this character again with the new corporation, `/auth` again.\n"
        "- If you want to use this character again with the old corporation, re-join the old corporation in-game."
    )

    log_text = f"structure_corp_warning for {character}"

    return warning_text, log_text


async def structure_other_warning(character: Character, authed_preston: Preston, error_value: str):
    character_name = (await authed_preston.whoami()).get("character_name")

    warning_text = (
        "### WARNING\n"
        f"<@{character.user.user_id}>, the following character does not have permissions to see structure info: {character_name}\n"
        f"This is due to the following error: {error_value}\n"
        "There are no specific instructions to fix this error, so you have to try for yourself.\n"
        "- In case you no longer need structure pings, you can remove the character with `/revoke {character_name}`.\n"
        "- Otherwise you can check if your permissions are correct with `/info`."
    )

    log_text = f"structure_other_warning for {character}"

    return warning_text, log_text


async def channel_warning(user):
    """A warning that the channel might not be reachable anymore"""
    warning_text = (
        "### WARNING\n"
        f"<@{user.user_id}>, the channel you are using for timer-bot callbacks is a private channel which the bot might eventually no longer "
        "be able to reach. Please use a channel in a server as your callback location."
    )

    log_text = f"channel_warning for {user}"
    return warning_text, log_text


async def updated_channel_warning(user, channel):
    """A warning that the channel might not be reachable anymore"""
    warning_text = (
        "### WARNING\n"
        f"<@{user.user_id}>, the channel you were using for timer-bot callbacks was no longer reachable,"
        f"so the channel {channel.mention} is now used instead. Use /callback to set up a different channel."
    )

    log_text = f"updated_channel_warning for {user}"
    return warning_text, log_text


async def no_channel_anymore_log(character):
    if character not in no_channel_characters:
        logger.info(f"{character} has no valid channel and can not be notified, skipping...")
        no_channel_characters.add(character)



def get_error_text(exception: aiohttp.ClientResponseError):
    if hasattr(exception, 'message') and exception.message:
        try:
            response_content = json.loads(exception.message)
            return response_content.get("error", "")
        except (ValueError, TypeError):
            return exception.message
    elif hasattr(exception, 'status'):
        return f"HTTP {exception.status}"
    else:
        return ""


async def handle_auth_error(character, bot, user, preston, exception: aiohttp.ClientResponseError):
    if getattr(exception, "status", 0) in [400, 401]:
        success = await send_background_warning(
            bot, user,
            await esi_permission_warning(character, preston)
        )

        if not success:
            disconnected_character_cycles[character.character_id] += 1
        else:
            disconnected_character_cycles[character.character_id] = 0

        if disconnected_character_cycles[character.character_id] > 100:
            logger.error(
                f"{character} can not be reached on either side (ESI & Discord) and will be deleted."
            )
            character = Character.get_or_none(character.character_id)
            character.delete_instance()

    else:
        disconnected_character_cycles[character.character_id] = 0
        logger.warning(
            f"Auth for {character} encountered ClientResponseError: status={getattr(exception, 'status', None)}, message={get_error_text(exception)}"
        )


async def handle_structure_error(character, authed_preston, exception: aiohttp.ClientResponseError,
                                 bot=None, user=None, interaction=None):
    error_text = get_error_text(exception)
    if error_text == "Character does not have required role(s)":
        warning_text = await structure_permission_warning(character, authed_preston)
        if interaction is not None:
            await send_foreground_warning(interaction, warning_text)
        if bot is not None and user is not None:
            await send_background_warning(bot, user, warning_text)

    elif error_text in ["Character is not in the corporation", "Forbidden"]:
        try:
            # Try fast affiliation API
            new_corporation = (await authed_preston.post_op(
                'post_characters_affiliation',
                path_data={},
                post_data=[character.character_id]
            ))[0].get("corporation_id")
        except Exception as e:
            # Fall back to slow character API
            new_corporation = (await authed_preston.get_op(
                'get_characters_character_id',
                character_id=character.character_id
            )).get("corporation_id")

        if character.corporation_id == new_corporation:
            warning_text = await structure_corp_warning(character, authed_preston)
            if interaction is not None:
                await send_foreground_warning(interaction, warning_text)
            if bot is not None and user is not None:
                await send_background_warning(bot, user, warning_text)
        else:
            old_corporation = character.corporation_id
            character.corporation_id = new_corporation
            character.save()
            if interaction is not None:
                await interaction.followup.send(
                    f"Your character’s corporation ID `{old_corporation}` changed to `{new_corporation}`, which is now updated. Please retry the last command."
                )

    else:
        warning_text = await structure_other_warning(character, authed_preston, error_text)
        if interaction is not None:
            await send_foreground_warning(interaction, warning_text)
        if bot is not None and user is not None:
            await send_background_warning(bot, user, warning_text)

    logger.warning(
        f"Structure fetch for {character} encountered ClientResponseError: status={getattr(exception, 'status', None)}, message={error_text}"
    )


async def handle_notification_error(character, exception: aiohttp.ClientResponseError):
    logger.warning(
        f"Notification fetch for {character} encountered ClientResponseError: status={getattr(exception, 'status', None)}, message={get_error_text(exception)}"
    )

