import discord
import logging

from models import User
from warning import channel_warning, send_background_warning

logger = logging.getLogger('discord.timer.utils')


async def get_channel(user, bot):
    """Get a discord channel for a specific user."""
    emergency_dm = False
    try:
        channel = await bot.fetch_channel(int(user.callback_channel_id))
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException, discord.errors.InvalidData):
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
    except (discord.errors.Forbidden, discord.errors.NotFound, discord.errors.HTTPException,
            discord.errors.InvalidData) as e:
        logger.info(f"update_channel_if_broken() fixed channel for {user}, broken by {e}")
        user.callback_channel_id = str(interaction.channel.id)
        user.save()
    except Exception as e:
        logger.warning(f"update_channel_if_broken() channel broken in a different way than expected for user {user}: {e}", exc_info=True)
        user.callback_channel_id = str(interaction.channel.id)
        user.save()


async def send_large_message(ctx, message, max_chars=1994, delimiter='\n', **kwargs):
    open_code_block = False

    while len(message) > 0:
        # If the remaining message fits within max_chars, send it as is
        if len(message) <= max_chars:
            # Prepend message if the previous one ended with an open code-block
            if open_code_block:
                message = f"```{message}"

            await ctx.send(message, **kwargs)
            break

        # Find the last newline within the max_chars limit
        last_newline_index = message.rfind(delimiter, 0, max_chars)

        # If no newline found within limit, cut at max_chars
        if last_newline_index == -1:
            part = message[:max_chars]
            message = message[max_chars:]
        else:
            part = message[:last_newline_index]
            message = message[last_newline_index + 1:]

        # Count the number of backticks to see if the split ends in a codeblock
        code_block_count = part.count("```")

        # Prepend message if the previous one ended with an open code-block
        if open_code_block:
            part = f"```{part}"

        # Toggle if we are in a code-block
        if code_block_count % 2 == 1:
            open_code_block = not open_code_block

        # Post-pend message if we are still in a code-block
        if open_code_block:
            part = f"{part}```"

        # Send the current part
        await ctx.send(part, **kwargs)
