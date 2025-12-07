import asyncio
import functools
import json
import logging
import os
import secrets
from io import BytesIO

import aiohttp
import discord
from discord import Interaction, app_commands
from discord.ext import commands
from preston import Preston

from callback import callback_server
from models import User, Challenge, Character, initialize_database
from relay import notification_pings, status_pings, no_auth_pings, cleanup_old_notifications
from structure import structure_info_text
from utils import send_background_message
from warning import esi_permission_warning, channel_warning, handle_structure_error, updated_channel_warning
from warning import send_foreground_warning

# Configure the logger
logger = logging.getLogger('discord.timer')
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logger.setLevel(log_level)

# Initialize the database
initialize_database()


# Setup ESI connection
async def refresh_token_callback(preston):
    character_data = await preston.whoami()
    if "character_id" in character_data:
        character = Character.get(character_id=character_data.get("character_id"))
        character.token = preston.refresh_token
        character.save()

base_preston = Preston(
    user_agent="Structure timer discord bot by <larynx.austrene@gmail.com>",
    client_id=os.environ["CCP_CLIENT_ID"],
    client_secret=os.environ["CCP_SECRET_KEY"],
    callback_url=os.environ["CCP_REDIRECT_URI"],
    scope="esi-corporations.read_structures.v1 esi-characters.read_notifications.v1 esi-universe.read_structures.v1",
    refresh_token_callback=refresh_token_callback,
    timeout=6,
)

# Setup Discord
intent = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intent)


async def log_statistics():
    """Log the number of users and their characters on bot startup."""
    try:
        for user in User.select():
            if user.characters.exists():
                character_list = ", ".join(
                    [character.character_id for character in user.characters.select()]
                )
            else:
                character_list = "No characters"
            logger.info(
                f"User ID: {user.user_id}, Linked Channel: {user.callback_channel_id}, Character IDs: {character_list}")

    except Exception as e:
        logger.error(f"log_statistics() error while logging users and characters: {e}", exc_info=True)


def command_error_handler(func):
    """Decorator for handling bot command logging and exceptions."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        interaction, *arguments = args
        logger.info(f"{interaction.user.name} used /{func.__name__} {arguments} {kwargs}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in /{func.__name__} command: {e}", exc_info=True)
            return None

    return wrapper


@bot.event
async def on_ready():
    # Setup Lock for actions
    action_lock = asyncio.Lock()

    # Start background tasks
    notification_pings.start(action_lock, base_preston, bot)
    status_pings.start(action_lock, base_preston, bot)
    cleanup_old_notifications.start(action_lock)
    callback_server.start(bot, base_preston)

    logger.info(f"on_ready() logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"on_ready() synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"on_ready() failed to sync slash commands: {e}", exc_info=True)

    await log_statistics()

    await asyncio.sleep(60 * 60 * 5)  # Wait 5 hours
    no_auth_pings.start(action_lock, bot)


@bot.tree.command(name="auth", description="Sends you an authorization link for characters.")
@command_error_handler
async def auth(interaction: Interaction):
    secret_state = secrets.token_urlsafe(60)

    user, created = User.get_or_create(
        user_id=str(interaction.user.id),
        defaults={"callback_channel_id": str(interaction.channel.id)},
    )
    Challenge.delete().where(Challenge.user == user).execute()
    Challenge.create(user=user, state=secret_state)

    full_link = base_preston.get_authorize_url(secret_state)
    # noinspection PyUnresolvedReferences
    await interaction.response.send_message(
        f"Use this [authentication link]({full_link}) to authorize your characters.", ephemeral=True
    )


@bot.tree.command(name="callback", description="Sets the channel where you want to be notified if something happens.")
@app_commands.describe(
    channel="Discord Channel where you want to receive structure information, of not given uses the current one.",
)
@command_error_handler
async def callback(interaction: Interaction, channel: discord.TextChannel | None = None):
    """Sets the channel where you want to be notified if something happens.

    Optionally, mention a channel (e.g. #alerts) to set it as the callback.
    """
    user = User.get_or_none(user_id=str(interaction.user.id))
    if user is None:
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            "You are not a registered user. Use `!auth` to authorize some characters first."
        )
        return

    target_channel = channel or interaction.channel
    user.callback_channel_id = str(target_channel.id)
    user.save()

    if isinstance(target_channel, discord.DMChannel):
        await send_foreground_warning(interaction, await channel_warning(user))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"Set this DM-channel as callback for notifications.")
    else:
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"Set {target_channel.mention} as callback for notifications.")


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


@bot.tree.command(name="characters", description="Shows all authorized characters")
@command_error_handler
async def characters(interaction: Interaction):
    # noinspection PyUnresolvedReferences
    await interaction.response.defer(ephemeral=True)
    """Displays your currently authorized characters."""

    await update_channel_if_broken(interaction, bot)

    character_names = []
    user = User.get_or_none(User.user_id == str(interaction.user.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = await base_preston.authenticate_from_token(character.token)
            except aiohttp.ClientResponseError as exp:
                if exp.status == 401:
                    await send_foreground_warning(
                        interaction,
                        await esi_permission_warning(character, base_preston)
                    )
                    continue
                else:
                    raise

            character_data = await authed_preston.whoami()
            character_names.append(f"- {character_data.get('character_name', 'Unknown')}")

    if not character_names:
        await interaction.followup.send("You have no authorized characters!", ephemeral=True)
        return

    character_names_body = "\n".join(character_names)
    await interaction.followup.send(
        f"You have the following character(s) authenticated:\n{character_names_body}", ephemeral=True
    )


@bot.tree.command(
    name="revoke",
    description="Revokes ESI access for your characters."
)
@app_commands.describe(
    character_name="Name of the character to revoke, revoke all if empty."
)
@command_error_handler
async def revoke(interaction: Interaction, character_name: str | None = None):
    # noinspection PyUnresolvedReferences
    await interaction.response.defer(ephemeral=True)
    user = User.get_or_none(User.user_id == str(interaction.user.id))

    if not user:
        await interaction.followup.send(
            f"You did not have any authorized characters in the first place.",
            ephemeral=True
        )

    if character_name is None:
        user_characters = Character.select().where(Character.user == user)
        if user_characters:
            for character in user_characters:
                character.delete_instance()

        user.delete_instance()

        await interaction.followup.send(f"Successfully revoked access to all your characters.", ephemeral=True)
        return

    try:
        character_id = int(character_name)
    except ValueError:
        try:
            result = await base_preston.post_op(
                'post_universe_ids',
                path_data={},
                post_data=[character_name]
            )
            character_id = int(max(result.get("characters"), key=lambda x: x.get("id")).get("id"))
        except (ValueError, KeyError):
            await  interaction.followup.send(
                f"Args `{character_name}` could not be parsed or looked up.",
                ephemeral=True
            )
            return

    character = user.characters.select().where(Character.character_id == character_id).first()
    if character:
        character.delete_instance()
        await interaction.followup.send(f"Successfully removed {character_name}.", ephemeral=True)
    else:
        await interaction.followup.send(
            "You have no character named {character_name} linked.",
            ephemeral=True
        )


@bot.tree.command(
    name="info",
    description="Returns the status of all structures linked."
)
@command_error_handler
async def info(interaction: Interaction):
    # noinspection PyUnresolvedReferences
    await interaction.response.defer()

    await update_channel_if_broken(interaction, bot)

    structures_info = {}

    user = User.get_or_none(User.user_id == str(interaction.user.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = await base_preston.authenticate_from_token(character.token)
            except aiohttp.ClientResponseError as exp:
                if exp.status == 401:
                    await send_foreground_warning(interaction, await esi_permission_warning(character, base_preston))
                    continue
                else:
                    raise

            try:
                structure_response = await authed_preston.get_op(
                    "get_corporations_corporation_id_structures",
                    corporation_id=character.corporation_id,
                )
            except ConnectionError:
                logger.warning(f"/info got a network error got {character}")
                await interaction.followup.send("Network error with /info command, try again later")
                return
            except aiohttp.ClientResponseError as exp:
                await handle_structure_error(character, authed_preston, exp, interaction=interaction)
                return
            except Exception as e:
                await interaction.followup.send(f"Got an unfamiliar error in /info command: {e}.")
                logger.error(f"/info got an unfamiliar error with {character}: {e}.", exc_info=True)
                return
            else:
                for structure in structure_response:
                    structure_id = structure.get("structure_id")
                    structures_info[structure_id] = structure_info_text(structure)

    # Build message with all structure info
    output = "\n"
    if structures_info:
        output += "".join(map(str, structures_info.values()))
    else:
        output += "No structures found!\n"

    await interaction.followup.send(output)


@bot.tree.command(
    name="action",
    description="Sends a text to all user for a call to action. Admin only."
)
@app_commands.describe(
    text="Call to action text to sed to all users."
)
@command_error_handler
async def action(interaction: Interaction, text: str):
    """Admin only: send a message to all users concerning the bot."""
    if int(interaction.user.id) != int(os.environ["ADMIN"]):
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message("You are not authorized to perform this action.")
        return

    # noinspection PyUnresolvedReferences
    await interaction.response.send_message("Sending action text...")

    used_channels = set()
    user_count = 0
    for user in User.select():
        try:
            if await send_background_message(bot, user, text):
                used_channels.add(user.callback_channel_id)
        except discord.errors.Forbidden:
            await interaction.followup.send(f"Could not reach user {user}.")
            logger.info(f"/action could not reach user {user}.")
        user_count += 1

    await interaction.followup.send(f"Sent action text to {user_count} users. The message looks like the following:")
    await interaction.followup.send(text)


@bot.tree.command(
    name="debug",
    description="Admin only: Look at ESI response for a character."
)
@app_commands.describe(
    character_id="The EVE character ID to debug."
)
@command_error_handler
async def debug(interaction: Interaction, character_id: int):
    if int(interaction.user.id) != int(os.environ["ADMIN"]):
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message("You are not authorized to perform this action.", ephemeral=True)
        return

    # noinspection PyUnresolvedReferences
    await interaction.response.defer(ephemeral=True)

    character = Character.get_or_none(Character.character_id == character_id)
    if not character:
        await interaction.followup.send("Character not found in the database.", ephemeral=True)
        return

    try:
        authed_preston = await base_preston.authenticate_from_token(character.token)
        character_data = await authed_preston.whoami()
        character_name = character_data.get("character_name", "Unknown")

        structure_response = await authed_preston.get_op(
            "get_corporations_corporation_id_structures",
            corporation_id=character.corporation_id,
        )

        notification_response = await authed_preston.get_op(
            "get_characters_character_id_notifications",
            character_id=character.character_id
        )

        structure_bytes = BytesIO(json.dumps(structure_response, indent=2).encode('utf-8'))
        notification_bytes = BytesIO(json.dumps(notification_response, indent=2).encode('utf-8'))

        await interaction.user.send(
            content=f"Raw ESI data for **{character_name}** (`{character_id}`):",
            files=[
                discord.File(structure_bytes, filename=f"character_{character_id}_structures.json"),
                discord.File(notification_bytes, filename=f"character_{character_id}_notifications.json")
            ]
        )

    except aiohttp.ClientResponseError as exp:
        await interaction.followup.send(f"HTTPError: {exp.status} - {exp.message}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Unhandled exception: {e}", ephemeral=True)


@bot.tree.command(
    name="dryrun",
    description="Send you a message in just the way a notification would work for testing purposes."
)
@command_error_handler
async def dryrun(interaction: Interaction):

    user = User.get_or_none(user_id=interaction.user.id)

    if user is not None:
        success = await send_background_message(
            bot,
            user,
            "Dry Run: You would receive callback notifications like this.",
            "dryrun",
            quiet=True
        )
        if success:
            await interaction.response.send_message(
                f"Sent you a dry run message", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Failed to send you a dry run message, try setting up a channel where the bot can write and use /callback there", ephemeral=True
            )
    else:
        await interaction.response.send_message(
            f"You are not a registered user, try the /auth command", ephemeral=True
        )

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
