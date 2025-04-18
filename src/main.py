import asyncio
import functools
import logging
import os
import secrets

import discord
from requests.exceptions import HTTPError, ConnectionError
from discord.ext import commands
from preston import Preston

from callback import callback_server
from models import User, Challenge, Character, initialize_database
from relay import notification_pings, status_pings
from structure import structure_info
from user_warnings import send_esi_permission_warning, send_structure_permission_warning, send_structure_corp_warning, \
    send_structure_other_warning, send_channel_warning
from utils import lookup, with_refresh, get_channel, send_large_message

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
        ctx = args[0]
        logger.info(f"{ctx.author.name} used !{func.__name__}")

        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in !{func.__name__} command: {e}", exc_info=True)
            await ctx.send(f"An error occurred in !{func.__name__} ({e.__class__.__name__}).")

    return wrapper


@bot.event
async def on_ready():
    # Setup Lock for actions
    action_lock = asyncio.Lock()
    notification_pings.start(action_lock, base_preston, bot)
    status_pings.start(action_lock, base_preston, bot)
    callback_server.start(base_preston)
    await log_statistics()


@bot.command()
@command_error_handler
async def auth(ctx):
    """Sends you an authorization link for a character."""

    secret_state = secrets.token_urlsafe(60)

    user, created = User.get_or_create(
        user_id=str(ctx.author.id),
        defaults={"callback_channel_id": str(ctx.channel.id)},
    )
    Challenge.delete().where(Challenge.user == user).execute()
    Challenge.create(user=user, state=secret_state)

    full_link = f"{base_preston.get_authorize_url()}&state={secret_state}"
    await ctx.author.send(f"Use this [authentication link]({full_link}) to authorize your characters.")


@bot.command()
@command_error_handler
async def callback(ctx, channel: discord.TextChannel = None):
    """Sets the channel where you want to be notified if something happens.

    Optionally, mention a channel (e.g. #alerts) to set it as the callback.
    """
    user = User.get_or_none(user_id=str(ctx.author.id))
    if user:
        target_channel = channel or ctx.channel
        user.callback_channel_id = str(target_channel.id)
        user.save()

        if isinstance(target_channel, discord.DMChannel):
            await send_channel_warning(user, target_channel, send_now=True)
            await ctx.send(f"Set this DM-channel as callback for notifications.")
        else:
            await ctx.send(f"Set {target_channel.mention} as callback for notifications.")
    else:
        await ctx.send("You are not a registered user. Use `!auth` to authorize some characters first.")


@bot.command()
@command_error_handler
async def characters(ctx):
    """Displays your currently authorized characters."""

    character_names = []
    user = User.get_or_none(User.user_id == str(ctx.author.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = with_refresh(base_preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 401:
                    await send_esi_permission_warning(character, ctx, base_preston)
                    continue
                else:
                    raise

            character_name = authed_preston.whoami()['CharacterName']
            character_names.append(f"- {character_name}")

    if character_names:
        character_names_body = "\n".join(character_names)
        await send_large_message(
            ctx,
            f"You have the following character(s) authenticated:\n{character_names_body}"
        )
    else:
        await ctx.send("You have no authorized characters!")


@bot.command()
@command_error_handler
async def revoke(ctx, *args):
    """Revokes ESI access from all your characters.
    :args: Character that you want to revoke access to."""

    user = User.get_or_none(User.user_id == str(ctx.author.id))

    if not user:
        await ctx.send(f"You did not have any authorized characters in the first place.")

    if len(args) == 0:
        user_characters = Character.select().where(Character.user == user)
        if user_characters:
            for character in user_characters:
                character.delete_instance()

        user.delete_instance()

        await ctx.send(f"Successfully revoked access to all your characters.")

    else:
        try:
            character_id = await lookup(base_preston, " ".join(args), return_type="characters")
        except ValueError:
            args_concatenated = " ".join(args)
            await ctx.send(f"Args `{args_concatenated}` could not be parsed or looked up.")
        else:
            character = user.characters.select().where(Character.character_id == character_id).first()
            if character:
                character.delete_instance()
                await ctx.send(f"Successfully removed your character.")
            else:
                await ctx.send("You have no character with that name linked.")


@bot.command()
@command_error_handler
async def info(ctx):
    """Returns the status of all structures linked."""

    structures_info = {}

    user = User.get_or_none(User.user_id == str(ctx.author.id))
    if user:
        for character in user.characters:
            try:
                authed_preston = with_refresh(base_preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 401:
                    await send_esi_permission_warning(character, ctx, base_preston)
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
                            await send_structure_permission_warning(character, ctx, authed_preston, send_now=True)
                        case "Character is not in the corporation":
                            await send_structure_corp_warning(character, ctx, authed_preston, send_now=True)
                        case _:
                            await send_structure_other_warning(
                                character, ctx, authed_preston,
                                structure_response["error"],
                                send_now=True
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

    await send_large_message(ctx, output)


@bot.command()
@command_error_handler
async def action(ctx, *action_text):
    """Admin only: send a message to all users concerning the bot."""
    if int(ctx.author.id) != int(os.environ["ADMIN"]):
        await ctx.send("You are not authorized to perform this action.")

    action_text_concatenated = " ".join(action_text)

    user_count = 0
    for user in User.select():
        try:
            channel = await get_channel(user, bot)
            await channel.send(action_text_concatenated)
        except discord.errors.Forbidden:
            await ctx.send(f"Could not reach user {user}.")
            logger.info(f"Could not reach user {user}.")
        user_count += 1

    await ctx.send(f"Sent action text to {user_count} users. The message looks like the following:")
    await ctx.send(action_text_concatenated)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
