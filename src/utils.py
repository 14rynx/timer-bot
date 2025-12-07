import logging

import discord

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

    return channel, emergency_dm


async def send_background_message(bot, user, message, identifier="<no identifier>", quiet=False):
    """Wrapper to send a message to a user, automatically handles not being able to reach user and fallback options.
    Returns true if successful
    """

    user_channel, emergency_dm = await get_channel(user, bot)

    if user_channel is None:
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
        if emergency_dm:
            await user_channel.send(
                "### WARNING\n"
                f"<@{user.user_id}>, timer-bot could not reach you through your callback channel but only through DMs."
                f"Please use `/callback` to set up a callback channel in a server and ensure you are on a server with timer-bot."
                f"Otherwise you might eventually no longer be reachable."
            )
        await user_channel.send(message)
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException,
            discord.errors.InvalidData):
        if not quiet:
            logger.info(
                f"Sending message to {user} failed (discord permissions).\n"
                f"Recipient Identifier: {identifier}\n"
                f"Message: {message}"
            )
            no_channel_users.add(user)
        return False
    except Exception as e:
        if not quiet:
            logger.warning(
                f"Sending message to {user} failed (unknown exception).\n"
                f"Recipient Identifier: {identifier}\n"
                f"Message: {message}", exc_info=True
            )
            no_channel_users.add(user)
        return False
    else:
        if user in no_channel_users:
            no_channel_users.remove(user)
        return True
