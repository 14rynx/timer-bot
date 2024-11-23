import asyncio
import functools
import logging
import os
import secrets

import discord
from discord.ext import commands
from preston import Preston

from callback import callback_server
from models import User, Challenge, Character, initialize_database
from relay import notification_pings, status_pings
from structure import structure_info
from utils import lookup, with_refresh, get_channel

# Configure the logger
logger = logging.getLogger('discord.main')
logger.setLevel(logging.INFO)

# Initialize the database
initialize_database()

# Setup ESI connection
base_preston = Preston(
    user_agent="Hangar organizing discord bot by larynx.austrene@gmail.com",
    client_id=os.environ["CCP_CLIENT_ID"],
    client_secret=os.environ["CCP_SECRET_KEY"],
    callback_url=os.environ["CCP_REDIRECT_URI"],
    scope="esi-corporations.read_structures.v1 esi-characters.read_notifications.v1 esi-universe.read_structures.v1",
)

# Setup Discord
intent = discord.Intents.default()
intent.messages = True
intent.message_content = True
bot = commands.Bot(command_prefix='!', intents=intent)


async def set_callback(ctx):
    user = User.get(user_id=str(ctx.author.id))
    user.callback_channel_id = str(ctx.channel.id)
    user.save()

    if isinstance(ctx.channel, discord.channel.DMChannel):
        await ctx.send(
            "### WARNING\n"
            "This channel can only temporarily be used for notifications,"
            " as it changes IDs and eventually will no longer be available to the bot!\n"
            "Use `!callback` outside of a DM channel e.g. in a server so the bot can reach you indefinitely."
        )

    else:
        await ctx.send("Set this channel as callback for notifications.")


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
            await ctx.send(f"An error occurred in !{func.__name__}.")

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
async def callback(ctx):
    """Sets the channel where you want to be notified if something happens."""
    await set_callback(ctx)


@bot.command()
@command_error_handler
async def characters(ctx):
    """Displays your currently authorized characters."""

    character_names = []
    user = User.get_or_none(User.user_id == str(ctx.author.id))
    if user:
        for character in user.characters:
            authed_preston = with_refresh(base_preston, character.token)
            character_name = authed_preston.whoami()['CharacterName']
            character_names.append(f"- {character_name}")

    if character_names:
        character_names_body = "\n".join(character_names)
        await ctx.send(f"You have the following character(s) authenticated:\n{character_names_body}")
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

    structures_info = []
    characters_without_permissions = []

    user = User.get_or_none(User.user_id == str(ctx.author.id))
    if user:
        for character in user.characters:
            authed_preston = with_refresh(base_preston, character.token)

            character_name = authed_preston.whoami()['CharacterName']

            corporation_id = authed_preston.get_op(
                'get_characters_character_id',
                character_id=character.character_id
            ).get("corporation_id")

            # Get structure data and build info for this structure
            structures = authed_preston.get_op(
                'get_corporations_corporation_id_structures',
                corporation_id=corporation_id
            )

            for structure in structures:
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
        output += "### WARNING\n"
        output += "The following characters do not have permissions to see structure info:\n"
        output += "\n".join(characters_without_permissions)

    await ctx.send(output)


@bot.command()
@command_error_handler
async def action(ctx, action_text):
    """Admin only: send a message to all users concerning the bot."""
    if int(ctx.author.id) != int(os.environ["ADMIN"]):
        await ctx.send("You are not authorized to perform this action.")

    action_text_concatenated = " ".join(action_text)

    user_count = 0
    for user in User.select():
        if user.characters.exists():
            channel = await get_channel(user, bot)
            channel.send(action_text_concatenated)
            user_count += 1

    await ctx.send(f"Sent action text to {user_count} users. The message looks like the following:")
    await ctx.send(action_text_concatenated)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
