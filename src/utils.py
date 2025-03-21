import logging
from datetime import datetime, timezone, timedelta

import discord
from preston import Preston

from models import Character

# Configure the logger
logger = logging.getLogger('discord.utils')
logger.setLevel(logging.WARNING)


async def lookup(preston: Preston, string: str, return_type: str) -> int:
    """Tries to find an ID related to the input.

    Parameters
    ----------
    string : str
        The character / corporation / alliance name
    return_type : str
        what kind of id should be tried to match
        can be characters, corporations and alliances

    Raises
    ------
    ValueError if the name can't be resolved
    """
    try:
        return int(string)
    except ValueError:
        try:
            result = preston.post_op(
                'post_universe_ids',
                path_data={},
                post_data=[string]
            )
            return int(max(result[return_type], key=lambda x: x["id"])["id"])
        except (ValueError, KeyError):
            raise ValueError("Could not parse that character!")


def with_refresh(preston_instance: Preston, character: Character) -> Preston:
    """Returns a similar Preston instance with the specified refresh token."""
    try:
        new_kwargs = dict(preston_instance._kwargs)
        new_kwargs["refresh_token"] = character.token
        new_kwargs["access_token"] = None
        new = Preston(**new_kwargs)
    except KeyError:
        logger.info(f"Could not authenticate with {character}.")
        raise ValueError(f"Could not authenticate with {character}.")

    return new


async def send_warning(user, channel, warning_text, log_text=""):
    """Send a warning message to the user, logs if it was successful
    and sets the warning delay for said user."""
    if user.next_warning < datetime.now(tz=timezone.utc).timestamp():
        try:
            await channel.send(warning_text)
            logger.debug(f"Sent warning {log_text}.")
        except Exception as e:
            logger.info(f"Could not send warning {log_text}: {e}")
        else:
            user.next_warning = (datetime.now(tz=timezone.utc) + timedelta(days=1)).timestamp()
            user.save()

async def send_esi_permission_warning(user, channel, character_id, preston):
    character_name = preston.get_op(
        'get_characters_character_id',
        character_id=character_id
    ).get("name")

    warning_text = f"""
    ### WARNING
    The following character does not have permissions to fetch data from ESI:
    {character_name}. If you to not intend to use this character, remove him with 
    `!revoke {character_name}`. Otherwise re-authenticate with `!auth`.
    """

    await send_warning(user, channel, warning_text, log_text="channel")

async def send_notification_permission_warning(user, channel, authed_preston):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = f"""
    ### WARNING
    The following character does not have permissions to see notification info:
    {character_name}. If you to not intend to use this character, remove him with 
    `!revoke {character_name}` otherwise, fix your notification permissions in-game.
    """

    await send_warning(user, channel, warning_text, log_text="channel")


async def send_structure_permission_warning(user, channel, authed_preston):
    character_name = authed_preston.whoami()["CharacterName"]

    warning_text = f"""
    ### WARNING
    The following character does not have permissions to see structure info:
    {character_name}. If you to not intend to use this character, remove him with 
    `!revoke {character_name}`. Otherwise, fix your corp permissions in-game and 
    check them with `!info`.
    """

    await send_warning(user, channel, warning_text, log_text="channel")


async def send_channel_warning(user, channel):
    warning_text = f"""
    ### WARNING
    The channel you are using for timer-bot callbacks is a private channel
    which the bot might eventually no longer be able to reach. Please use
    a channel in a server as your callback location
    """

    await send_warning(user, channel, warning_text, log_text="channel")


async def get_channel(user, bot):
    """Get a discord channel for a specific user."""
    channel = await bot.fetch_channel(int(user.callback_channel_id))
    if channel is not None and isinstance(channel, discord.channel.DMChannel):
        await send_channel_warning(user, channel)
    return channel
