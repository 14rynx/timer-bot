import logging

import discord

from models import User
from warning import channel_warning, send_background_warning, send_foreground_warning, updated_channel_warning

logger = logging.getLogger('discord.timer.utils')

no_channel_users = set()


async def get_channel(user, bot):
    """Get a discord channel for a specific user."""
    emergency_dm = False
    try:
        channel = await bot.fetch_channel(int(user.callback_channel_id))
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException,
            discord.errors.InvalidData):
        try:
            discord_user = await bot.fetch_user(int(user.user_id))
            channel = await discord_user.create_dm()
            emergency_dm = True
        except Exception as e:
            logger.warning(f"Failed to get channel or open DM channel for user {user}: {e}", exc_info=True)
            return None

    except Exception as e:
        logger.warning(f"Failed to get channel for user {user}: {e}", exc_info=True)
        return None

    if channel is None:
        return None

    # Now we should definitely have a good channel
    if isinstance(channel, discord.channel.DMChannel):
        await send_background_warning(channel, await channel_warning(user), quiet=emergency_dm)
    return channel


async def update_channel_if_broken(interaction, bot):
    user = User.get_or_none(user_id=str(interaction.user.id))
    if user is None:
        return

    try:
        await bot.fetch_channel(int(user.callback_channel_id))
        return
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException,
            discord.errors.InvalidData) as e:
        logger.info(f"update_channel_if_broken() fixed channel for {user}, broken by {e}")
    except Exception as e:
        logger.warning(
            f"update_channel_if_broken() channel broken in a different way than expected for user {user}: {e}",
            exc_info=True)

    target_channel = interaction.channel
    user.callback_channel_id = str(target_channel.id)
    user.save()

    await send_foreground_warning(interaction, await updated_channel_warning(user, target_channel))

    if isinstance(target_channel, discord.DMChannel):
        await send_foreground_warning(interaction, await channel_warning(user))


async def send_background_message(bot, user, message, identifier="<no identifier>", quiet=False):
    """Wrapper to send a message to a user, automatically handles not being able to reach user and fallback options.
    Returns true if successful
    """

    if (user_channel := await get_channel(user, bot)) is None:
        if user not in no_channel_users:
            if not quiet:
                logger.info(
                    f"Sending message to {user} failed (no channel).\n"
                    f"Recipient Identifier: {identifier}\n"
                    f"Message: {message}"
                )
            no_channel_users.add(user)
        raise

    try:
        await user_channel.send(message)
        return True
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException,
            discord.errors.InvalidData):
        if not quiet:
            logger.info(
                f"Sending message to {user} failed (discord permissions).\n"
                f"Recipient Identifier: {identifier}\n"
                f"Message: {message}"
            )
        return False
    except Exception as e:
        if not quiet:
            logger.warning(
                f"Sending message to {user} failed (unknown exception).\n"
                f"Recipient Identifier: {identifier}\n"
                f"Message: {message}", exc_info=True
            )
        return False
