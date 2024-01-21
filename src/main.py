import logging

import _gdbm
import os
import secrets
import shelve
import sys
import threading

import discord
from discord.ext import commands

from callback_server import callback_server
from relay import external_pings
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
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

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


@bot.event
async def on_ready():
    external_pings.start(esi_app, esi_client, esi_security, bot)


@bot.command()
async def auth(ctx):
    """Sends you an authorization link for a character."""
    logger.info(f"{ctx.author.name} used !auth")

    user_key = str(ctx.author.id)

    # Send an authorization link
    secret_state = secrets.token_urlsafe(30)
    challenges[secret_state] = ctx
    uri = esi_security.get_auth_uri(state=secret_state, scopes=["esi-corporations.read_structures.v1",
                                                                "esi-characters.read_notifications.v1",
                                                                "esi-universe.read_structures.v1"])
    await ctx.author.send(f"Use this [authentication link]({uri}) to authorize your characters.")

    # Store the channel information associated with the user
    with shelve.open('../data/user_channels', writeback=True) as user_channels:
        user_channels[user_key] = ctx.channel.id


@bot.command()
async def callback(ctx):
    """Sets the channel where you want to be notified if something happens."""

    logger.info(f"{ctx.author.name} used !callback")

    try:
        # Store the channel information associated with the user
        user_key = str(ctx.author.id)
        with shelve.open('../data/user_channels', writeback=True) as user_channels:
            if user_key in user_channels:
                user_channels[user_key] = ctx.channel.id
                await ctx.send("Set this channel as callback for notifications.")
            else:
                await ctx.send("You do not have any authorized channels!")
    except _gdbm.error:
        await ctx.send("Currently busy with another command!")


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

            # Delete user tokens
            user_characters[user_key][character_key] = {}
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

                # Delete user tokens
                user_characters[user_key] = {}

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
    # Load the stored user-channel associations from shelf files
    challenges = {}

    callback = threading.Thread(target=lambda: callback_server(esi_app, esi_client, esi_security, challenges))
    callback.start()
    bot.run(os.environ["DISCORD_TOKEN"])
