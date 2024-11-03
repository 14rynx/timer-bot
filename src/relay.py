import logging
import shelve
import sys

from discord.ext import tasks

# Fix for Mutable Mapping collection being moved
if sys.version_info.major == 3 and sys.version_info.minor >= 10:
    import collections

    setattr(collections, "MutableMapping", collections.abc.MutableMapping)
    setattr(collections, "Mapping", collections.abc.Mapping)

from esipy.exceptions import APIException
from structure_info import structure_info, fuel_warning

# Constants
NOTIFICATION_CACHE_TIME = 600
NOTIFICATION_PHASES = 12

STATUS_CACHE_TIME = 3600
STATUS_PHASES = 12

# Configure the logger
logger = logging.getLogger('discord.relay')
logger.setLevel(logging.INFO)

# Configure iteration variables
notification_phase = -1
status_phase = -1


def build_notification_message(notification, esi_app, esi_client):
    structure_name = get_structure_name(notification, esi_app, esi_client)
    match notification.get('type'):
        case "StructureLostArmor":
            return f"@everyone Structure {structure_name} has lost it's armor!\n"
        case "StructureLostShields":
            return f"@everyone Structure {structure_name} has lost it's shields!\n"
        case "StructureUnanchoring":
            return f"@everyone Structure {structure_name} is now unanchoring!\n"
        case "StructureUnderAttack":
            return f"@everyone Structure {structure_name} is under attack!\n"
        case "StructureWentHighPower":
            return f"@everyone Structure {structure_name} is now high power!\n"
        case "StructureWentLowPower":
            return f"@everyone Structure {structure_name} is now low power!\n"
        case "StructureOnline":
            return f"@everyone Structure {structure_name} went online!\n"
        case _:
            return ""


def get_structure_id(notification):
    """returns a structure id from the notification,
    returns none if no structure_id can be found"""
    structure_id = None
    for line in notification.get("text").split("\n"):
        if "structureID:" in line:
            structure_id = int(line.split(" ")[2])
    return structure_id


def get_structure_name(notification, esi_app, esi_client):
    if structure_id := get_structure_id(notification):
        op = esi_app.op['get_universe_structures_structure_id'](structure_id=structure_id)
        structure_name = esi_client.request(op).data.get("name", "Unknown")
    else:
        structure_name = "Unknown"
    return structure_name


def is_structure_notification(notification):
    # All structure notifications start with Structure... so we can use that
    return "Structure" in notification.get('type')


async def send_notification_message(notification, channel, character_key, user_key, esi_app, esi_client):
    logger.debug("Sending notification message")

    # Fail if the notification is an error or None
    if notification is None:
        logger.error(f"Got a None type notification with User {user_key} & Character {character_key}.")
        return

    if type(notification) is str:
        logger.error(f"Got a str notification: {notification} with User {user_key} & Character {character_key}.")
        return

    notification_id = str(notification.get("notification_id"))

    if not is_structure_notification(notification):
        logger.debug(f"Skipping Notification {notification_id} as it is not a structure notification.")
        return

    if channel is None:
        logger.warning(f"User {user_key} character {character_key} has no valid channel and can not be notified!")
        return

    with shelve.open('../data/old_notifications') as old_notifications:

        if notification_id in old_notifications:
            logger.debug(f"Skipping notification with id: {notification_id} as it was previously sent.")
            return

        try:
            if len(message := build_notification_message(notification, esi_app, esi_client)) > 0:
                await channel.send(message)
                logger.info(f"Sent notification to user {user_key} character {character_key}")
        except Exception as e:
            logger.error(f"Could not send notification to user {user_key} character {character_key}: {e}")
        else:
            old_notifications[notification_id] = "handled"


async def send_state_message(structure, channel, character_key="", user_key=""):
    """given a structure object and a channel, send a message if the structure state changed"""
    structure_state = structure.get('state')
    with shelve.open('../data/structure_states', writeback=True) as structure_states:
        if (structure_key := str(structure.get('structure_id'))) in structure_states:
            if not structure_states[structure_key] == structure_state:
                if channel is None:
                    logger.warning(f"User {user_key} character {character_key} has no valid channel and can not be notified!")
                    return

                try:
                    await channel.send(
                        f"Structure {structure.get('name')} changed state:\n"
                        f"{structure_info(structure)}"
                    )
                    logger.info(f"Sent state change to user {user_key} character {character_key}")
                except Exception as e:
                    logger.error(f"Could not send state change to user {user_key} character {character_key}: {e}")
                else:
                    # The message has been sent without any exception, so we can update our db
                    structure_states[structure_key] = structure_state
        else:
            try:
                # Update structure state and let user know
                await channel.send(
                    f"Structure {structure.get('name')} newly found in state:\n"
                    f"{structure_info(structure)}"
                )
                logger.info(f"Sent initial state to user {user_key} character {character_key}")
            except Exception as e:
                logger.error(f"Could not send initial state to user {user_key} character {character_key}: {e}")
            else:
                structure_states[structure_key] = structure_state


async def send_fuel_message(structure, channel, character_key="", user_key=""):
    """given a structure object and a channel, send a message if fuel went low"""
    with shelve.open('../data/structure_fuel', writeback=True) as structure_last_fuel_warning:
        current_fuel_warning = fuel_warning(structure)
        if current_fuel_warning is None:
            # Structure anchoring, we skip fuel for now
            return

        if (structure_key := str(structure.get('structure_id'))) in structure_last_fuel_warning:
            if not structure_last_fuel_warning[structure_key] == current_fuel_warning:
                if channel is None:
                    logger.warning(f"User {user_key} character {character_key} has no valid channel and can not be notified!")
                    return

                try:
                    if current_fuel_warning > structure_last_fuel_warning[structure_key]:
                        await channel.send(
                            f"Structure {structure.get('name')} has been refueled:\n"
                            f"{structure_info(structure)}"
                        )
                        logger.info(f"Sent refuel info to user {user_key} character {character_key}")
                    else:
                        await channel.send(
                            f"{structure_last_fuel_warning[structure_key]}-day warning, structure {structure.get('name')} is running low on fuel:\n"
                            f"{structure_info(structure)}"
                        )
                        logger.info(f"Sent fuel warning to user {user_key} character {character_key}")
                except Exception as e:
                    logger.error(f"Could not send fuel warning to user {user_key} character {character_key}: {e}")
                else:
                    # The message has been sent without any exception, so we can update our db
                    structure_last_fuel_warning[structure_key] = current_fuel_warning

        else:
            # Add structure to fuel db quietly
            structure_last_fuel_warning[structure_key] = current_fuel_warning


async def send_permission_warning(character_name, channel, character_key, user_key):
    try:
        await channel.send(
            "### Warning\n "
            "The following character does not have permissions to see structure info:\n"
            f"{character_name}\n If you to not intend to use this character, "
            f"remove him with `!revoke {character_name}`\n"
            "otherwise, fix your corp permissions and check them with `!info`"
        )
        logger.info(f"Sent permission warning to user {user_key} character {character_key}")
    except Exception as e:
        logger.error(f"Could not send permission warning to user {user_key} character {character_key}: {e}")


async def send_token_warning(character_name, channel, character_key, user_key):
    try:
        await channel.send(
            "### Warning\n "
            f"The character {character_name}'s scopes have run out and you will no longer get notifications"
            f"Please remove him with `!revoke {character_name}` and then add him anew with !auth\n"
        )
        logger.info(f"Sent scope warning message to {character_name}")
    except Exception as e:
        logger.error(f"Could not send scope warning to user {user_key} character {character_key}: {e}")


async def schedule_characters(action_lock, phase, phases, esi_app, esi_client):
    """returns a subset of characters such that if all characters could get the same notification,
    it is fetched as early as possible.

    Requires the notification loop to be run more often than just for cache time by NOTIFICATION_PHASES"""

    async with action_lock:
        with shelve.open('../data/user_characters') as user_characters:
            corporation_tokens = {}

            # Sort all registered characters by corporation
            for user_key, characters in user_characters.items():
                for character_key, tokens in user_characters[user_key].items():

                    # Get corporation ID from character
                    op = esi_app.op['get_characters_character_id'](character_id=int(character_key))
                    corporation_id = esi_client.request(op).data.get("corporation_id")

                    if corporation_id in corporation_tokens:
                        corporation_tokens[corporation_id].append([user_key, character_key, tokens])
                    else:
                        corporation_tokens[corporation_id] = [[user_key, character_key, tokens]]

            # Now go through each corporation and run depending on the phase
            for corporation_id, token_list in corporation_tokens.items():
                characters_in_corporation = len(token_list)

                for i, (user_key, character_key, tokens) in enumerate(token_list):
                    if phase == int(i / characters_in_corporation * phases):
                        logger.debug(f"Scheduling Corporation: {corporation_id} User: {user_key} Character: {character_key}.")
                        yield user_key, character_key, tokens


@tasks.loop(seconds=NOTIFICATION_CACHE_TIME // NOTIFICATION_PHASES + 1)
async def notification_pings(action_lock, esi_app, esi_client, esi_security, bot):
    """Periodically fetch notifications from ESI"""
    try:
        # Increment phase
        global notification_phase
        notification_phase = (notification_phase + 1) % NOTIFICATION_PHASES

        logger.debug(f"Running notification_pings in phase {notification_phase}.")

        async for user_key, character_key, tokens in schedule_characters(
                action_lock,
                notification_phase, NOTIFICATION_PHASES,
                esi_app, esi_client
        ):

            # Retrieve the channel associated with the user
            with shelve.open('../data/user_channels') as user_channels:
                user_channel = bot.get_channel(user_channels.get(user_key))

            # Fetch notifications from character
            try:
                tokens["expires_in"] = -1
                esi_security.update_token(tokens)
            except APIException:
                logger.warning(f"Got an API Exception with user: {user_key} character: {character_key}.")
                continue

            op = esi_app.op['get_characters_character_id_notifications'](character_id=int(character_key))
            notification_response = esi_client.request(op)

            # Send Messages for notifications
            for notification in reversed(notification_response.data):
                await send_notification_message(notification, user_channel, character_key, user_key, esi_app,
                                                esi_client)

    except APIException:
        logger.error("Got an API exception - notification_pings phase failed!")
    except Exception as e:
        logger.error(f"Got an unhandled exception in notification_pings: {e}.", exc_info=True)


@tasks.loop(seconds=STATUS_CACHE_TIME // STATUS_PHASES + 1)
async def status_pings(action_lock, esi_app, esi_client, esi_security, bot):
    """Periodically fetch structure state apu from ESI"""
    try:
        # Increment phase
        global status_phase
        status_phase = (status_phase + 1) % STATUS_PHASES

        logger.debug(f"Running status_pings in phase {status_phase}.")

        async for user_key, character_key, tokens in schedule_characters(
                action_lock,
                status_phase, STATUS_PHASES,
                esi_app, esi_client
        ):

            # Retrieve the channel associated with the user
            with shelve.open('../data/user_channels') as user_channels:
                user_channel = bot.get_channel(user_channels.get(user_key))

            # Fetch structure info from character
            try:
                logger.debug(f"Updating tokens {tokens}.")
                tokens["expires_in"] = -1
                esi_security.update_token(tokens)
            except APIException:
                logger.warning(f"Got an API Exception with user: {user_key} character: {character_key}.")
                continue

            # Get corporation ID from character
            op = esi_app.op['get_characters_character_id'](character_id=int(character_key))
            response_data = esi_client.request(op).data
            corporation_id = response_data.get("corporation_id")
            character_name = response_data.get("name")

            # Fetch structure data from character
            op = esi_app.op['get_corporations_corporation_id_structures'](corporation_id=corporation_id)
            results = esi_client.request(op)

            # Extracting and formatting data
            for result_entry in results.data:
                if result_entry is None:
                    continue

                if type(result_entry) is str:
                    # We have some kind of error, but since the library is a bit wired we get one str at a time
                    if result_entry == "Character does not have required role(s)":
                        await send_permission_warning(character_name, user_channel, character_key, user_key)
                    continue

                await send_state_message(result_entry, user_channel, character_key, user_key)
                await send_fuel_message(result_entry, user_channel, character_key, user_key)

    except APIException:
        logger.error("Got an API exception - status_pings phase failed!")
    except Exception as e:
        logger.error(f"Got an unhandled exception in status_pings: {e}.", exc_info=True)


@tasks.loop(hours=49)
async def refresh_tokens(action_lock, esi_app, esi_client, esi_security, bot):
    async with action_lock:
        """Periodically fetch structure state apu from ESI"""
        try:
            logger.debug("Refreshing tokens...")

            with shelve.open('../data/user_characters', writeback=True) as user_characters:
                for user_key, characters in user_characters.items():

                    # Retrieve the channel associated with the user
                    with shelve.open('../data/user_channels') as user_channels:
                        user_channel = bot.get_channel(user_channels.get(user_key))

                    for character_key, tokens in characters.items():
                        try:
                            esi_security.update_token(tokens)
                            user_characters[user_key][character_key] = esi_security.refresh()
                        except APIException:
                            # Tokens are already expired somehow -> let the user fix it
                            op = esi_app.op['get_characters_character_id'](character_id=int(character_key))
                            character_name = esi_client.request(op).data.get("name")
                            await send_token_warning(character_name, user_channel, character_key, user_key)

        except APIException:
            logger.error("Got an API exception - refresh failed!")
        except Exception as e:
            logger.error(f"Got an unhandled exception in refresh_tokens: {e}.", exc_info=True)
