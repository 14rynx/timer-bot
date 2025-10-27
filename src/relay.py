import collections
import logging
from datetime import datetime, time, timedelta, UTC

from discord.ext import tasks
from requests.exceptions import HTTPError, ConnectionError

from models import Character, User, Notification
from notification import send_notification_message
from structure import send_structure_message
from utils import get_channel
from warning import no_channel_anymore_log, handle_auth_error, handle_structure_error, handle_notification_error

# Constants
NOTIFICATION_CACHE_TIME = 600
NOTIFICATION_PHASES = 12

STATUS_CACHE_TIME = 3600
STATUS_PHASES = 12

# Configure the logger
logger = logging.getLogger('discord.timer.relay')

# Configure iteration variables
notification_phase = -1
status_phase = -1


def is_server_downtime_now(extended=False):
    now_utc = datetime.now(UTC).time()
    if extended:
        return time(11, 0) <= now_utc < time(12, 0)
    return time(11, 0) <= now_utc < time(11, 10)


async def schedule_characters(action_lock, phase, total_phases):
    """Returns a subset of characters depending on the current phase, such that in total_phases
    all characters are used exactly once, and characters in the same corp are spread as evenly as possible."""

    async with action_lock:
        corporation_characters = collections.defaultdict(list)
        for character in Character.select():
            corporation_characters[character.corporation_id].append(character)

        # Now go through each corporation and run depending on the phase
        for corporation_id, characters in corporation_characters.items():
            for i, character in enumerate(characters):
                if phase == int(i / len(characters) * total_phases):
                    logger.debug(f"Scheduling Corporation: {corporation_id} Character: {character}.")
                    yield character


@tasks.loop(seconds=NOTIFICATION_CACHE_TIME // NOTIFICATION_PHASES + 1)
async def notification_pings(action_lock, preston, bot):
    """Periodically fetch notifications from ESI"""
    try:
        global notification_phase
        notification_phase = (notification_phase + 1) % NOTIFICATION_PHASES
        logger.debug(f"Running notification_pings in phase {notification_phase}.")
        if is_server_downtime_now():
            logger.info("ESI is probably down (11:00–11:10 UTC). Skipping this run.")
            return

        async for character in schedule_characters(action_lock, notification_phase, NOTIFICATION_PHASES):
            try:
                if (user_channel := await get_channel(character.user, bot)) is None:
                    await no_channel_anymore_log(character)
                    return

                try:
                    try:
                        authed_preston = preston.authenticate_from_token(character.token)
                    except HTTPError as exp:
                        await handle_auth_error(character, user_channel, preston, exp)
                        continue
                    try:
                        response = authed_preston.get_op(
                            "get_characters_character_id_notifications",
                            character_id=character.character_id,
                        )
                    except HTTPError as exp:
                        await handle_notification_error(character, exp)
                        continue
                except ConnectionError as exp:
                    if not is_server_downtime_now(extended=True):
                        logger.warning(
                            f"notification_pings got a ConnectionError ({exp.response.status_code}: {exp.response.text})"
                            f" for {character}, skipping..."
                        )
                except Exception as e:
                    logger.error(
                        f"notification_pings got an unfamiliar exception for {character}: {e}.",  exc_info=True
                    )
                else:
                    for notification in reversed(response):
                        await send_notification_message(
                            notification, user_channel, authed_preston, identifier=str(character)
                        )
            except Exception as e:
                logger.error(f"notification_pings got an unhandled exception for {character}: {e}.", exc_info=True)

    except Exception as e:
        logger.error(f"notification_pings SCHEDULING got an unhandled exception: {e}.", exc_info=True)


@tasks.loop(seconds=STATUS_CACHE_TIME // STATUS_PHASES + 1)
async def status_pings(action_lock, preston, bot):
    """Periodically fetch structure state apu from ESI"""
    try:
        global status_phase
        status_phase = (status_phase + 1) % STATUS_PHASES
        logger.debug(f"Running status_pings in phase {status_phase}.")
        if is_server_downtime_now():
            logger.info("ESI is probably down (11:00–11:10 UTC). Skipping this run.")
            return

        async for character in schedule_characters(action_lock, status_phase, STATUS_PHASES):
            try:
                if (user_channel := await get_channel(character.user, bot)) is None:
                    await no_channel_anymore_log(character)
                    continue

                try:
                    try:
                        authed_preston = preston.authenticate_from_token(character.token)
                    except HTTPError as exp:
                        await handle_auth_error(character, user_channel, preston, exp)
                        continue
                    try:
                        response = authed_preston.get_op(
                            "get_corporations_corporation_id_structures",
                            corporation_id=character.corporation_id,
                        )
                    except HTTPError as exp:
                        await handle_structure_error(character, authed_preston, exp, channel=user_channel)
                        continue
                except ConnectionError as exp:
                    if not is_server_downtime_now(extended=True):
                        logger.warning(
                            f"status_pings got a ConnectionError ({exp.response.status_code}: {exp.response.text})"
                            f" for {character}, skipping..."
                        )
                except Exception as e:
                    logger.error(
                        f"status_pings got an unfamiliar exception for {character}: {e}.", exc_info=True
                    )
                else:
                    for structure in response:
                        await send_structure_message(structure, user_channel, identifier=str(character))
            except Exception as e:
                logger.error(f"status_pings got an unhandled exception for {character}: {e}.", exc_info=True)

    except Exception as e:
        logger.error(f"status_pings SCHEDULING got an unhandled exception: {e}.", exc_info=True)


@tasks.loop(hours=42)
async def no_auth_pings(action_lock, bot):
    """Periodically remind users that don't have characters linked so they don't get surprised."""
    async with action_lock:
        try:
            for user in User.select():
                if not user.characters.exists():
                    # noinspection PyBroadException
                    try:
                        user_channel = await bot.fetch_channel(int(user.callback_channel_id))
                    except Exception:
                        # There is nothing to salvage for this user anyway
                        continue

                    warning_text = (
                        "### WARNING\n"
                        f"<@{user.user_id}>, your discord account is linked to timer-bot, but you have not authorized any characters.\n"
                        f"This means you will not get any notifications about reinforced structures or fuel"
                        f"- If you to not intend to use this bot anymore, write `/revoke` to de-register.\n"
                        f"- Otherwise add some character with `/auth`"
                    )

                    # noinspection PyBroadException
                    try:
                        await user_channel.send(warning_text)
                    except Exception:
                        # There is nothing to salvage for this user anyway
                        continue

        except Exception as e:
            logger.error(f"Error while trying to notify users without auth: {e}")


@tasks.loop(hours=1)
async def cleanup_old_notifications(action_lock):
    """Delete notifications older than 4 weeks."""
    async with action_lock:
        try:
            threshold = datetime.now(UTC) - timedelta(days=2)
            deleted = Notification.delete().where(Notification.timestamp < threshold).execute()
            logger.debug(f"cleanup_old_notifications() deleted {deleted} old notifications older than 2 days.")
        except Exception as e:
            logger.error(f"cleanup_old_notifications() unhandled exception: {e}", exc_info=True)
