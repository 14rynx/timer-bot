import _gdbm
import asyncio
import functools
import logging
import os
import secrets
import shelve
import sys

import discord
from discord.ext import commands

from callback_server import callback_server
from relay import notification_pings, status_pings
from structure_info import structure_info

# Fix for Mutable Mapping collection being moved
if sys.version_info.major == 3 and sys.version_info.minor >= 10:
    import collections

    setattr(collections, "MutableMapping", collections.abc.MutableMapping)
    setattr(collections, "Mapping", collections.abc.Mapping)

# Import esipy with mutable mappings changes
from esipy import EsiApp, EsiClient, EsiSecurity
from esipy.exceptions import APIException

# Configure the logger
logger = logging.getLogger('discord.main')
logger.setLevel(logging.INFO)

# Setup ESIpy
esi_app = EsiApp().get_latest_swagger
esi_security = EsiSecurity(
    redirect_uri=os.environ["CCP_REDIRECT_URI"],
    client_id=os.environ["CCP_CLIENT_ID"],
    secret_key=os.environ["CCP_SECRET_KEY"],
    headers={'User-Agent': 'Timer discord bot by larynx.austrene@gmail.com'},
)

esi_client = EsiClient(
    retry_requests=True,
    headers={'User-Agent': 'Timer discord bot by larynx.austrene@gmail.com'},
    security=esi_security
)

# Setup Discord
intent = discord.Intents.default()
intent.messages = True
intent.message_content = True
bot = commands.Bot(command_prefix='!', intents=intent)

# Setup Lock for actions
action_lock = asyncio.Lock()


async def set_callback(ctx, overwrite=False):
    try:
        user_key = str(ctx.author.id)
        with shelve.open('../data/user_channels', writeback=True) as user_channels:
            if overwrite or user_key not in user_channels:
                user_channels[user_key] = ctx.channel.id
                if isinstance(ctx.channel, discord.channel.DMChannel):
                    await ctx.send(
                        "### WARNING\n"
                        "This channel can only temporarily be used for notifications,"
                        " as it changes IDs and eventually will no longer be available to the bot!\n"
                        "Use `!callback` outside of a DM channel e.g. in a server so the bot can reach you indefinitely."
                    )

                else:
                    await ctx.send("Set this channel as callback for notifications.")
    except _gdbm.error:
        await ctx.send("Currently busy with another command!")


async def log_statistics():
    """Log the number of users and their characters on bot startup."""
    try:
        with shelve.open('../data/user_characters', writeback=False) as user_characters:
            with shelve.open('../data/user_channels', writeback=False) as user_channels:
                total_users = len(user_characters)
                logger.info(f"Total users: {total_users}")

                for user_key, characters in user_characters.items():
                    linked_channel = user_channels.get(user_key, "-")

                    character_list = ", ".join(characters.keys())
                    logger.info(f"User ID: {user_key}, Linked Channel: {linked_channel}, Character IDs: {character_list}")

    except Exception as e:
        logger.error(f"Error while logging users and characters: {e}")


def command_error_handler(func):
    """Decorator for handling bot command logging and exceptions."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = args[0]
        logger.info(f"{ctx.author.name} used !{func.__name__}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in !{func.__name__} command: {e}", exc_info=True)
            await ctx.send(f"An error occurred in !{func.__name__}.")

    return wrapper

@bot.event
async def on_ready():
    notification_pings.start(action_lock, esi_app, esi_client, esi_security, bot)
    status_pings.start(action_lock, esi_app, esi_client, esi_security, bot)
    callback_server.start(action_lock, esi_app, esi_client, esi_security)
    await log_statistics()


@bot.command()
@command_error_handler
async def auth(ctx):
    """Sends you an authorization link for a character."""

    # Send an authorization link
    secret_state = secrets.token_urlsafe(30)
    with shelve.open('../data/challenges', writeback=True) as challenges:
        challenges[secret_state] = ctx.author.id
    uri = esi_security.get_auth_uri(state=secret_state, scopes=[
        "esi-corporations.read_structures.v1",
        "esi-characters.read_notifications.v1",
        "esi-universe.read_structures.v1"
    ])
    await ctx.author.send(f"Use this [authentication link]({uri}) to authorize your characters.")
    await set_callback(ctx, overwrite=False)


@bot.command()
@command_error_handler
async def callback(ctx):
    """Sets the channel where you want to be notified if something happens."""
    await set_callback(ctx, overwrite=True)


@bot.command()
@command_error_handler
async def characters(ctx):
    """Displays your currently authorized characters."""

    try:
        user_key = str(ctx.author.id)
        with shelve.open('../data/user_characters', writeback=True) as user_characters:

            character_names = []
            for character_key, tokens in user_characters[user_key].items():
                # Refresh ESI Token
                esi_security.update_token(tokens)
                user_characters[user_key][character_key] = esi_security.refresh()

                # Get character name
                character_name = esi_security.verify()['name']
                character_names.append(f"- {character_name}")

            # Compile message of all character names
            if character_names:
                names_body = "\n".join(character_names)
                await ctx.send(f"You have the following character(s) authenticated:\n{names_body}")
            else:
                await ctx.send("You have no authorized characters!")

    except APIException:
        await ctx.send("Authorization ran out!")
    except KeyError:
        await ctx.send("You have no authorized characters!")
    except _gdbm.error:
        await ctx.send("Currently busy with another command!")


@bot.command()
@command_error_handler
async def revoke(ctx, *character_name):
    """Revokes ESI access from your characters."""

    try:
        user_characters = shelve.open('../data/user_characters', writeback=True)
        user_key = str(ctx.author.id)

        if character_name:
            character_name = " ".join(character_name)
            op = esi_app.op['post_universe_ids'](names=[character_name])
            character_key = str(esi_client.request(op).data.get("characters", {})[0].get("id"))

            # Try to authenticate and revoke access, if it fails don't worry
            tokens = user_characters[user_key][character_key]
            try:
                esi_security.update_token(tokens)
                esi_security.refresh()
                esi_security.revoke()
            except APIException:
                pass

            # Delete character tokens
            del user_characters[user_key][character_key]
            # Delete entire user if no characters are left
            if len(user_characters[user_key]) == 0:
                del user_characters[user_key]

            await ctx.send(f"Revoked {character_name}'s API access!\n")

        else:
            for character_id, tokens in user_characters[user_key].items():
                # Try to authenticate and revoke access, if it fails don't worry
                try:
                    esi_security.update_token(tokens)
                    esi_security.refresh()
                    esi_security.revoke()
                except APIException:
                    pass

                # Delete entire user
                del user_characters[user_key]

            await ctx.send("Revoked all characters API access!\n")
        user_characters.close()
    except KeyError:
        await ctx.send(f"Could not find character!\n")
    except _gdbm.error:
        await ctx.send("Currently busy with another command!")


@bot.command()
@command_error_handler
async def info(ctx):
    """Returns the status of all structures linked."""

    try:
        user_characters = shelve.open('../data/user_characters', writeback=True)
        structures_info = []
        characters_without_permissions = []

        user_key = str(ctx.author.id)

        for character_key, tokens in user_characters[user_key].items():
            # Refresh ESI Token
            esi_security.update_token(tokens)
            user_characters[user_key][character_key] = esi_security.refresh()
            character_name = esi_security.verify()['name']

            # Get corporation ID from character
            op = esi_app.op['get_characters_character_id'](character_id=character_key)
            corporation_id = esi_client.request(op).data.get("corporation_id")

            # Get structure data and build info for this structure
            op = esi_app.op['get_corporations_corporation_id_structures'](corporation_id=corporation_id)

            for structure in esi_client.request(op).data:
                if type(structure) is str:
                    characters_without_permissions.append(f"- {character_name}")
                else:
                    structures_info.append(structure_info(structure))

        # Build message with all structure info
        output = "\n"
        if structures_info:
            output += "\n\n".join(structures_info)
        else:
            output += "No structures found!\n"
        if characters_without_permissions:
            output += "### Warning\n"
            output += "The following characters do not have permissions to see structure info:\n"
            output += "\n".join(characters_without_permissions)

        await ctx.send(output)

        user_characters.close()

    except APIException:
        await ctx.send("Authorization ran out!")
    except KeyError:
        await ctx.send("You have no authorized characters!")
    except _gdbm.error:
        await ctx.send("Currently busy with another command!")


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
