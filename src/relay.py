import collections
import logging

from discord.ext import tasks

from models import Character, Structure, Notification
from structure import structure_notification_message, structure_info, fuel_warning, is_structure_notification
from utils import with_refresh, get_channel, send_structure_permission_warning

# Constants
NOTIFICATION_CACHE_TIME = 600
NOTIFICATION_PHASES = 12

STATUS_CACHE_TIME = 3600
STATUS_PHASES = 12

# Configure the logger
logger = logging.getLogger('discord.relay')
logger.setLevel(logging.WARNING)

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
        # Increment phase
        global notification_phase
        notification_phase = (notification_phase + 1) % NOTIFICATION_PHASES
        logger.debug(f"Running notification_pings in phase {notification_phase}.")

        async for character in schedule_characters(action_lock, notification_phase, NOTIFICATION_PHASES):

            if (user_channel := await get_channel(character.user, bot)) is None:
                logger.info(f"{character} has no valid channel and can not be notified!")
                return

            authed_preston = with_refresh(preston, character.token)

            notifications = authed_preston.get_op(
                "get_characters_character_id_notifications",
                character_id=character.character_id,
            )

            for notification in reversed(notifications):
                await send_notification_message(notification, user_channel, authed_preston, identifier=str(character))

    except Exception as e:
        logger.error(f"Got an unhandled exception in notification_pings: {e}.", exc_info=True)


async def send_notification_message(notification, user_channel, authed_preston, identifier="<no identifier>"):
    """For a notification from ESI take action and inform a user if required"""
    logger.debug("Sending notification message")

    # Fail if the notification is an error or None
    if notification is None:
        logger.warning(f"Got a None type notification with {identifier}.")
        return

    if type(notification) is str:
        logger.warning(f"Got a str notification: {notification} with {identifier}.")
        return

    notification_id = notification.get("notification_id")

    if not is_structure_notification(notification):
        logger.debug(f"Skipping Notification {notification_id} as it is not a structure notification.")
        return

    notif, created = Notification.get_or_create(notification_id=notification_id)

    if not notif.sent:
        try:
            if len(message := structure_notification_message(notification, authed_preston)) > 0:
                await user_channel.send(message)
                logger.debug(f"Sent notification to {identifier}")

        except Exception as e:
            logger.info(f"Could not send notification to {identifier}: {e}")
        else:
            notif.sent = True
            notif.save()

    else:
        logger.debug(f"Skipping notification with id: {notification_id} as it was previously sent.")


@tasks.loop(seconds=STATUS_CACHE_TIME // STATUS_PHASES + 1)
async def status_pings(action_lock, preston, bot):
    """Periodically fetch structure state apu from ESI"""
    try:
        global status_phase
        status_phase = (status_phase + 1) % STATUS_PHASES
        logger.debug(f"Running status_pings in phase {status_phase}.")

        async for character in schedule_characters(action_lock, status_phase, STATUS_PHASES):

            if (user_channel := await get_channel(character.user, bot)) is None:
                logger.info(f"{character} has no valid channel and can not be notified!")
                return

            authed_preston = with_refresh(preston, character.token)

            structures = authed_preston.get_op(
                "get_corporations_corporation_id_structures",
                corporation_id=character.corporation_id,
            )

            for structure in structures:
                if structure is None:
                    continue

                if type(structure) is str:
                    # We have some kind of error, but since the library is a bit wired we get one str at a time
                    if structure == "Character does not have required role(s)":
                        await send_structure_permission_warning(character, user_channel, authed_preston)
                    continue

                await send_structure_message(structure, user_channel, identifier=str(character))

    except Exception as e:
        logger.error(f"Got an unhandled exception in status_pings: {e}.", exc_info=True)


async def send_structure_message(structure, user_channel, identifier="<no identifier>"):
    """For a structure state if there are any changes, take action and inform a user"""

    structure_db, created = Structure.get_or_create(
        structure_id=structure.get('structure_id'),
        defaults={
            "last_state": structure.get('state'),
            "last_fuel_warning": fuel_warning(structure),
        },
    )

    if created:
        try:
            await user_channel.send(
                f"Structure {structure.get('name')} newly found in state:\n"
                f"{structure_info(structure)}"
            )
            logger.debug(f"Sent initial state to user {identifier}")
        except Exception as e:
            logger.info(f"Could not send initial state to {identifier}: {e}")

    else:
        # Send message based on state
        if structure_db.last_state != structure.get("state"):
            try:
                await user_channel.send(
                    f"Structure {structure.get('name')} changed state:\n"
                    f"{structure_info(structure)}"
                )
                logger.debug(f"Sent state change to user {identifier}")
            except Exception as e:
                logger.info(f"Could not send state change to user {identifier}: {e}")
            else:
                structure_db.last_state = structure.get("state")
                structure_db.save()

        current_fuel_warning = fuel_warning(structure)

        # Send message based on fuel:
        if structure_db.last_fuel_warning != current_fuel_warning:
            try:
                if current_fuel_warning > structure_db.last_fuel_warning:
                    await user_channel.send(
                        f"Structure {structure.get('name')} has been refueled:\n"
                        f"{structure_info(structure)}"
                    )
                    logger.debug(f"Sent refuel info to {identifier}.")

                else:
                    await user_channel.send(
                        f"{structure_db.last_fuel_warning}-day warning, structure {structure.get('name')} is running low on fuel:\n"
                        f"{structure_info(structure)}"
                    )
                    logger.debug(f"Sent fuel warning to {identifier}")

            except Exception as e:
                logger.info(f"Could not send fuel warning to {identifier}: {e}")
            else:
                structure_db.last_fuel_warning = current_fuel_warning
                structure_db.save()