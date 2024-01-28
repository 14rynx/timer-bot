import _gdbm
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


@bot.event
async def on_ready():
    notification_pings.start(esi_app, esi_client, esi_security, bot)
    status_pings.start(esi_app, esi_client, esi_security, bot)
    callback_server.start(esi_security)


@bot.command()
async def auth(ctx):
    """Sends you an authorization link for a character."""
    logger.info(f"{ctx.author.name} used !auth")

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
async def callback(ctx):
    """Sets the channel where you want to be notified if something happens."""
    logger.info(f"{ctx.author.name} used !callback")
    await set_callback(ctx, overwrite=True)


@bot.command()
async def characters(ctx):
    """Displays your currently authorized characters."""

    logger.info(f"{ctx.author.name} used !characters")

    try:
        user_key = str(ctx.author.id)
        with shelve.open('../data/user_characters', writeback=True) as user_characters:

            character_names = []
            for character_id, tokens in user_characters[user_key].items():
                # Refresh ESI Token
                esi_security.update_token(tokens)
                user_characters[user_key][character_id] = esi_security.refresh()

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
async def revoke(ctx, *character_name):
    """Revokes ESI access from your characters."""

    logger.info(f"{ctx.author.name} used !revoke {character_name}")

    try:
        user_characters = shelve.open('../data/user_characters', writeback=True)
        user_key = str(ctx.author.id)

        if character_name:
            character_name = " ".join(character_name)
            op = esi_app.op['post_universe_ids'](names=[character_name])
            character_key = str(esi_client.request(op).data.get("characters", {})[0].get("id", ""))

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
async def info(ctx):
    """Returns the status of all structures linked."""

    logger.info(f"{ctx.author.name} used !info")

    try:
        user_characters = shelve.open('../data/user_characters', writeback=True)
        structures_info = []
        characters_without_permissions = []

        user_key = str(ctx.author.id)

        for character_id, tokens in user_characters[user_key].items():
            # Refresh ESI Token
            esi_security.update_token(tokens)
            user_characters[user_key][character_id] = esi_security.refresh()
            character_name = esi_security.verify()['name']

            # Get corporation ID from character
            op = esi_app.op['get_characters_character_id'](character_id=character_id)
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
