import asyncio
import functools
import logging
import os
import secrets

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from preston import Preston
from requests.exceptions import HTTPError

from callback import callback_server
from models import User, Challenge, Character, initialize_database
from relay import notification_pings, status_pings
from structure import structure_info
from user_warnings import send_foreground_warning, esi_permission_warning, structure_permission_warning, \
    structure_corp_warning, structure_other_warning, channel_warning
from utils import lookup, with_refresh, get_channel

# Configure the logger
logger = logging.getLogger('discord.timer')
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logger.setLevel(log_level)

# Initialize the database
initialize_database()

# Setup ESI connection
base_preston = Preston(
    user_agent="Hangar organizing discord bot by larynx.austrene@gmail.com",
    client_id=os.environ["CCP_CLIENT_ID"],
    client_secret=os.environ["CCP_SECRET_KEY"],
    callback_url=os.environ["CCP_REDIRECT_URI"],
    scope="esi-corporations.read_structures.v1 esi-characters.read_notifications.v1 esi-universe.read_structures.v1",
    timeout=6,
)

# Setup Discord
intent = discord.Intents.default()
intent.messages = True
intent.message_content = True
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
        logger.error(f"Error while logging users and characters: {e}")


def command_error_handler(func):
    """Decorator for handling bot command logging and exceptions."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        interaction, *arguments = args
        logger.info(f"{interaction.user.name} used !{func.__name__} {arguments} {kwargs}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in !{func.__name__} command: {e}", exc_info=True)
            await interaction.response.send_message(f"An error occurred in !{func.__name__}.")

    return wrapper


@bot.event
async def on_ready():
    # Setup Lock for actions
    action_lock = asyncio.Lock()
    notification_pings.start(action_lock, base_preston, bot)
    status_pings.start(action_lock, base_preston, bot)
    callback_server.start(base_preston)

    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}", exc_info=True)

    await log_statistics()


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

    full_link = f"{base_preston.get_authorize_url()}&state={secret_state}"
    await interaction.response.send_message(
        f"Use this [authentication link]({full_link}) to authorize your characters.", ephemeral=True
    )


@bot.tree.command(name="callback", description="Sets the channel where you want to be notified if something happens.")
@app_commands.describe(
    channel="Discord Channel where you want to recieve structure information, of not given uses the current one.",
)
@command_error_handler
async def callback(interaction: Interaction, channel: discord.TextChannel | None = None):
    """Sets the channel where you want to be notified if something happens.

    Optionally, mention a channel (e.g. #alerts) to set it as the callback.
    """
    user = User.get_or_none(user_id=str(interaction.user.id))
    if user is None:
        await interaction.response.send_message(
            "You are not a registered user. Use `!auth` to authorize some characters first."
        )
        return

    target_channel = channel or interaction.channel
    user.callback_channel_id = str(target_channel.id)
    user.save()

    if isinstance(target_channel, discord.DMChannel):
        await send_foreground_warning(interaction, await channel_warning(user))
        await interaction.response.send_message(f"Set this DM-channel as callback for notifications.")
    else:
        await interaction.response.send_message(f"Set {target_channel.mention} as callback for notifications.")


@bot.tree.command(name="characters", description="Shows all authorized characters")
@command_error_handler
async def characters(interaction: Interaction):
    await interaction.response.defer(ephemeral=True)
    """Displays your currently authorized characters."""

    character_names = []
    user = User.get_or_none(User.user_id == str(interaction.user.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = with_refresh(base_preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 401:
                    await send_foreground_warning(
                        interaction,
                        await esi_permission_warning(character, base_preston)
                    )
                    continue
                else:
                    raise

            character_name = authed_preston.whoami()['CharacterName']
            character_names.append(f"- {character_name}")

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

    else:
        try:
            character_id = await lookup(base_preston, character_name, return_type="characters")
        except ValueError:
            await  interaction.followup.send(
                f"Args `{character_name}` could not be parsed or looked up.",
                ephemeral=True
            )
        else:
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
    await interaction.response.defer()
    structures_info = {}

    user = User.get_or_none(User.user_id == str(interaction.user.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = with_refresh(base_preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 401:
                    await send_foreground_warning(interaction, esi_permission_warning(character, base_preston))
                    continue
                else:
                    raise

            corporation_id = authed_preston.get_op(
                'get_characters_character_id',
                character_id=character.character_id
            ).get("corporation_id")

            # Get structure data and build info for this structure
            structure_response = authed_preston.get_op(
                'get_corporations_corporation_id_structures',
                corporation_id=corporation_id
            )

            if type(structure_response) is dict:
                if "error" in structure_response:
                    match structure_response["error"]:
                        case "Character does not have required role(s)":
                            await send_foreground_warning(
                                interaction,
                                await structure_permission_warning(character, authed_preston)
                            )
                        case "Character is not in the corporation":
                            await send_foreground_warning(
                                interaction,
                                await structure_corp_warning(character, authed_preston)
                            )
                        case _:
                            await send_foreground_warning(
                                interaction,
                                await structure_other_warning(
                                    character, authed_preston, structure_response.get("error", "")
                                )
                            )
                else:
                    logger.error(f"Got an unfamiliar response for {character}: {structure_response}.", exc_info=True)
                continue

            for structure in structure_response:
                structure_id = structure.get("structure_id")
                structures_info[structure_id] = structure_info(structure)

    # Build message with all structure info
    output = "\n"
    if structures_info:
        output += "".join(map(str, structures_info.values()))
    else:
        output += "No structures found!\n"

    await interaction.followup.send(output)


@bot.tree.command(
    name="action",
    description="Sends a text to all user for a call to action."
)
@app_commands.describe(
    text="Call to action text to sed to all users."
)
@command_error_handler
async def action(interaction: Interaction, text: str):
    """Admin only: send a message to all users concerning the bot."""
    if int(interaction.user.id) != int(os.environ["ADMIN"]):
        await interaction.response.send_message("You are not authorized to perform this action.")

    await interaction.response.send_message("Sending action text...")

    used_channels = set()
    user_count = 0
    for user in User.select():
        try:
            channel = await get_channel(user, bot)
            if channel.id not in used_channels:
                await channel.send(text)
                used_channels.add(channel.id)
        except discord.errors.Forbidden:
            await interaction.followup.send(f"Could not reach user {user}.")
            logger.info(f"Could not reach user {user}.")
        user_count += 1

    await interaction.followup.send(f"Sent action text to {user_count} users. The message looks like the following:")
    await interaction.followup.send(text)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
