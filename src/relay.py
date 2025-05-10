import collections
import logging

from requests.exceptions import HTTPError, ConnectionError
from discord.ext import tasks

from models import Character, Structure, Notification
from structure import structure_notification_message, structure_info, next_fuel_warning, is_structure_notification
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
                if exp.response.status_code == 400:
                    await send_background_warning(
                        user_channel,
                        await esi_permission_warning(character, preston)
                    )
                elif exp.response.status_code == 401:
                    await send_background_warning(
                        user_channel,
                        await esi_permission_warning(character, preston)
                    )
                else:
                    logger.error(f"{character} got {exp.response.status_code} response {exp.response.text}, skipping...")
                continue
            except ConnectionError as exp:
                # Network issue, we are fine with a warning
                logger.warning(f"{character} got {exp.response.status_code} response {exp.response.text}, skipping...")
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

    if not is_structure_notification(notification):
        return

    notif, created = Notification.get_or_create(notification_id=notification_id)

    if not notif.sent:
        try:
            if len(message := structure_notification_message(notification, authed_preston)) > 0:
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
                if exp.response.status_code == 400:
                    await send_background_warning(
                        user_channel,
                        await esi_permission_warning(character, preston)
                    )
                elif exp.response.status_code == 403:
                    await send_background_warning(
                        user_channel,
                        await esi_permission_warning(character, preston)
                    )
                else:
                    logger.error(f"{character} got {exp.response.status_code} response {exp.response.text}, skipping...")
                continue
            except ConnectionError as exp:
                # Network issue, we are fine with a warning
                logger.warning(f"{character} got {exp.response.status_code} response {exp.response.text}, skipping...")
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
                        # See if character changed corporation and update
                        character_new_corporation = preston.get_op(
                            "get_characters_character_id",
                            character_id=character.character_id
                        ).get("corporation_id")

                        # Send warning if corp was correct, else update corp
                        if character.corporation_id == character_new_corporation:
                            await send_background_warning(
                                user_channel,
                                await structure_corp_warning(character, authed_preston),
                            )
                        else:
                            character.corporation_id = character_new_corporation
                            character.save()
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
            "last_fuel_warning": next_fuel_warning(structure),
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
            logger.warning(f"Could not send initial state to {identifier}: {e}")

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
                logger.warning(f"Could not send state change to user {identifier}: {e}")
            else:
                structure_db.last_state = structure.get("state")
                structure_db.save()

        current_fuel_warning = next_fuel_warning(structure)

        if structure_db.last_fuel_warning is None:  # Maybe remove this clause?
            structure_db.last_fuel_warning = current_fuel_warning
            structure_db.save()
            return

        elif current_fuel_warning > structure_db.last_fuel_warning:
            if structure_db.last_fuel_warning == -1:
                message = f"Structure {structure.get('name')} got initially fueled with:\n{structure_info(structure)}"
                logger_info = f"initial fuel info to {identifier}."
            else:
                message = f"Structure {structure.get('name')} has been refueled:\n{structure_info(structure)}"
                logger_info = f"refuel info to {identifier}."
        elif current_fuel_warning < structure_db.last_fuel_warning:
            state = structure.get('state')
            if current_fuel_warning == -1:
                if state in ["anchoring", "anchor_vulnerable"]:
                    return
                else:
                    message = f"Final warning, structure {structure.get('name')} ran out of fuel:\n{structure_info(structure)}"
                    logger_info = f"fuel empty to {identifier}"
            else:
                message = (f"{structure_db.last_fuel_warning}-day warning, structure {structure.get('name')} is "
                           f"running low on fuel:\n{structure_info(structure)}")
                logger_info = f"fuel warning to {identifier}"
        else:
            return

        # Send fuel message and update DB if successful
        try:
            await user_channel.send(message)
            logger.debug(f"Sent {logger_info}")
        except Exception as e:
            logger.warning(f"Could not send {logger_info}: {e}")
        else:
            structure_db.last_fuel_warning = current_fuel_warning
            structure_db.save()
