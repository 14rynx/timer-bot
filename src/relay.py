import collections
import logging

from requests.exceptions import HTTPError, ConnectionError
from discord.ext import tasks

from models import Character, Structure, Notification
from poco import poco_info, is_poco_notification, poco_notification_message
from user_warnings import send_background_warning, structure_permission_warning, esi_permission_warning, \
    structure_corp_warning, structure_other_warning
from utils import with_refresh, get_channel

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

        async for character in schedule_characters(action_lock, notification_phase, NOTIFICATION_PHASES):

            if (user_channel := await get_channel(character.user, bot)) is None:
                logger.info(f"{character} has no valid channel and can not be notified, skipping...")
                return

            try:
                authed_preston = with_refresh(preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 401:
                    await send_background_warning(
                        user_channel,
                        await esi_permission_warning(character, preston)
                    )
                    logger.warning(f"{character} has no ESI permissions and can not be notified!")
                else:
                    logger.error(f"{character} got {exp.response.status_code} response, skipping...")
                continue
            except ConnectionError as exp:
                logger.warning(f"{character} got {exp.response.status_code} response, skipping...")
                continue

            try:
                response = authed_preston.get_op(
                    "get_characters_character_id_notifications",
                    character_id=character.character_id,
                )
            except ConnectionError:
                logger.warning(f"Got a network error with {character}, skipping...")
            except HTTPError as exp:
                logger.warning(
                    f"Got a error response for notification: {exp.response.text} with {character}, skipping...")
            except Exception as e:
                logger.error(f"Got an unfamiliar exceptions when fetching notifications for {character}: {e}.",
                             exc_info=True)
            else:
                for notification in reversed(response):
                    await send_notification_message(notification, user_channel, authed_preston,
                                                    identifier=str(character))

    except Exception as e:
        logger.error(f"Got an unhandled exception in notification_pings: {e}.", exc_info=True)


async def send_notification_message(notification, user_channel, authed_preston, identifier="<no identifier>"):
    """For a notification from ESI take action and inform a user if required"""
    notification_id = notification.get("notification_id")

    if not is_poco_notification(notification):
        return

    notif, created = Notification.get_or_create(notification_id=notification_id)

    if not notif.sent:
        try:
            if len(message := poco_notification_message(notification, authed_preston)) > 0:
                await user_channel.send(message)
                logger.debug(f"Sent notification to {identifier}")

        except Exception as e:
            logger.warning(f"Could not send notification to {identifier}: {e}")
        else:
            notif.sent = True
            notif.save()


@tasks.loop(seconds=STATUS_CACHE_TIME // STATUS_PHASES + 1)
async def status_pings(action_lock, preston, bot):
    """Periodically fetch structure state apu from ESI"""
    try:
        global status_phase
        status_phase = (status_phase + 1) % STATUS_PHASES
        logger.debug(f"Running status_pings in phase {status_phase}.")

        async for character in schedule_characters(action_lock, status_phase, STATUS_PHASES):

            if (user_channel := await get_channel(character.user, bot)) is None:
                logger.info(f"{character} has no valid channel and can not be notified, skipping...")
                continue

            try:
                authed_preston = with_refresh(preston, character)
            except HTTPError as exp:
                if exp.response.status_code == 403:
                    await esi_permission_warning(character, user_channel, preston)
                    logger.warning(f"{character} has no ESI permissions and can not be notified!")
                else:
                    logger.error(f"{character} got {exp.response.status_code} response, skipping...")
                continue
            except ConnectionError as exp:
                logger.warning(f"{character} got {exp.response.status_code} response, skipping...")
                continue

            try:
                response = authed_preston.get_op(
                    "get_corporations_corporation_id_structures",
                    corporation_id=character.corporation_id,
                )
            except ConnectionError:
                logger.warning(f"Got a network error with {character}, skipping...")
            except HTTPError as exp:
                response_content = exp.response.json()
                match response_content.get("error", ""):
                    case "Character does not have required role(s)":
                        await send_background_warning(
                            user_channel,
                            await structure_permission_warning(character, authed_preston),
                        )
                    case "Character is not in the corporation":
                        await send_background_warning(
                            user_channel,
                            await structure_corp_warning(character, authed_preston),
                        )
                    case _:
                        await send_background_warning(
                            user_channel,
                            await structure_other_warning(
                                character, authed_preston, response_content.get("error", "")
                            ),
                        )
            except Exception as e:
                logger.error(f"Got an unfamiliar exceptions when fetching structures for {character}: {e}.",
                             exc_info=True)
            else:
                for structure in response:
                    await send_structure_message(structure, user_channel, identifier=str(character))

    except Exception as e:
        logger.error(f"Got an unhandled exception in status_pings: {e}.", exc_info=True)


async def send_structure_message(structure, user_channel, identifier="<no identifier>"):
    """For a structure state if there are any changes, take action and inform a user"""

    structure_db, created = Structure.get_or_create(
        structure_id=structure.get('structure_id'),
        defaults={
            "last_state": structure.get('state'),
        },
    )

    if created:
        try:
            await user_channel.send(
                f"Structure {structure.get('name')} newly found in state:\n"
                f"{poco_info(structure)}"
            )
            logger.debug(f"Sent initial state to user {identifier}")
        except Exception as e:
            logger.warning(f"Could not send initial state to {identifier}: {e}")

    else:
        # Send message based on state
        if structure_db.last_state != structure.get("state"):
            try:
                await user_channel.send(
                    f"Structure {structure.get('name')} changed state:\n"
                    f"{poco_info(structure)}"
                )
                logger.debug(f"Sent state change to user {identifier}")
            except Exception as e:
                logger.warning(f"Could not send state change to user {identifier}: {e}")
            else:
                structure_db.last_state = structure.get("state")
                structure_db.save()
