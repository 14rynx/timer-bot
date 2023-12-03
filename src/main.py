import os
import secrets
import shelve
import sys
import threading

import discord
from discord.ext import commands, tasks

from callback_server import callback_server
from structure_info import structure_info, fuel_warning

# Fix for Mutable Mapping collection being moved
if sys.version_info.major == 3 and sys.version_info.minor >= 10:
    import collections

    setattr(collections, "MutableMapping", collections.abc.MutableMapping)
    setattr(collections, "Mapping", collections.abc.Mapping)

# Import esipy with mutable mappings changes
from esipy import EsiApp, EsiClient, EsiSecurity
from esipy.exceptions import APIException

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
    external_pings.start(esi_app)


@bot.command()
async def auth(ctx):
    """Sends you an authorization link for a character."""
    user_key = str(ctx.author.id)

    # Send an authorization link
    secret_state = secrets.token_urlsafe(30)
    challenges[secret_state] = ctx
    uri = esi_security.get_auth_uri(state=secret_state, scopes=['esi-corporations.read_structures.v1'])
    await ctx.author.send(f"Use this [authentication link]({uri}) to authorize your characters.")

    # Store the channel information associated with the user
    user_channels[user_key] = ctx.channel.id


@bot.command()
async def characters(ctx):
    """Displays your currently authorized characters."""
    user_key = str(ctx.author.id)
    try:
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
            character_names_body = "\n".join(character_names)
            await ctx.send(f"You have the following character(s) authenticated:\n"
                           f"{character_names_body}")
        else:
            await ctx.send("You have no authorized characters!")

    except APIException:
        await ctx.send("Authorization ran out!")
    except KeyError:
        await ctx.send("You have no authorized characters!")


@bot.command()
async def revoke(ctx, *character_name):
    """Revokes ESI access from your characters."""
    user_key = str(ctx.author.id)

    try:
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
    except KeyError:
        await ctx.send(f"Could not find character!\n")


@bot.command()
async def info(ctx):
    """Returns the status of all structures linked."""
    try:
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

    except APIException:
        await ctx.send("Authorization ran out!")
    except KeyError:
        await ctx.send("You have no authorized characters!")


@tasks.loop(seconds=300)
async def external_pings(esi_app):
    """Displays your currently authorized characters."""
    for user, characters in user_characters.items():
        # Retrieve the channel associated with the user
        channel_id = user_channels.get(user)
        user_channel = bot.get_channel(channel_id)

        for character_id, tokens in characters.items():
            # Refresh ESI Token
            esi_security.update_token(tokens)
            user_characters[user][character_id] = esi_security.refresh()

            # Get corporation ID from character
            op = esi_app.op['get_characters_character_id'](character_id=character_id)
            corporation_id = esi_client.request(op).data.get("corporation_id")

            # Fetch structure data from character
            op = esi_app.op['get_corporations_corporation_id_structures'](corporation_id=corporation_id)
            results = esi_client.request(op)

            # Extracting and formatting data
            for structure in results.data:
                # Fail if the character does not have permissions. TODO: Fail loud the first time this happens
                if type(structure) is str:
                    continue

                state = structure.get('state')
                structure_name = structure.get('name')
                structure_key = str(structure.get('structure_id'))

                if structure_key in structure_states:
                    if not structure_states[structure_key] == state:
                        try:
                            await user_channel.send(
                                f"Structure {structure_name} changed state:\n"
                                f"{structure_info(structure)}"
                            )
                        except Exception as e:
                            print(e)
                        else:
                            # The message has been sent without any exception, so we can update our db
                            structure_fuel[structure_key] = fuel_warning(structure)
                else:
                    # Update structure state and let user know
                    await user_channel.send(
                        f"Structure {structure_name} newly found in state:\n"
                        f"{structure_info(structure)}"
                    )
                    structure_states[structure_key] = state

                if structure_key in structure_fuel:
                    if not structure_fuel[structure_key] == fuel_warning(structure):
                        try:
                            await user_channel.send(
                                f"{fuel_warning(structure)}-day warning, structure {structure_name} is running low on fuel:\n"
                                f"{structure_info(structure)}"
                            )
                        except Exception as e:
                            print(e)
                        else:
                            # The message has been sent without any exception, so we can update our db
                            structure_fuel[structure_key] = fuel_warning(structure)

                else:
                    # Add structure to fuel db quietly
                    structure_fuel[structure_key] = fuel_warning(structure)


if __name__ == "__main__":
    # Load the stored user-channel associations from shelf files
    user_channels = shelve.open('../data/user_channels', writeback=True)
    user_characters = shelve.open('../data/user_characters', writeback=True)
    structure_states = shelve.open('../data/structure_states', writeback=True)
    structure_fuel = shelve.open('../data/structure_fuel', writeback=True)
    challenges = {}

    # Run main thread and callback server
    try:
        callback = threading.Thread(target=lambda: callback_server(esi_security, challenges, user_characters))
        callback.start()
        bot.run(os.environ["DISCORD_TOKEN"])

    # Close files on exit
    except KeyboardInterrupt:
        user_channels.close()
        user_characters.close()
        structure_states.close()
        structure_fuel.close()
