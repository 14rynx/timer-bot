from json import JSONDecodeError

import discord
from preston import Preston
from requests import ReadTimeout

from models import Character
from user_warnings import send_channel_warning


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
    except (KeyError, JSONDecodeError, TimeoutError, ReadTimeout):
        raise ValueError(f"Could not authenticate with {character}.")

    return new


async def get_channel(user, bot):
    """Get a discord channel for a specific user."""
    channel = await bot.fetch_channel(int(user.callback_channel_id))
    if channel is not None and isinstance(channel, discord.channel.DMChannel):
        await send_channel_warning(user, channel)
    return channel


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
