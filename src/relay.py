import logging
import shelve
from datetime import datetime

from discord.ext import tasks

from structure_info import structure_info, fuel_warning

# Configure the logger
logger = logging.getLogger('discord.relay')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)


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


async def send_notification_message(notification, channel, character_id, user_id, esi_app, esi_client):
    # Fail if the notification is an error or None
    if notification is None or type(notification) is str:
        return

    if not is_structure_notification(notification):
        return

    with shelve.open('../data/old_notifications', writeback=True) as old_notifications:
        # Check if this notification was sent out previously and skip it
        if str(notification_id := notification.get("notification_id")) not in old_notifications:
            return

        try:
            if len(message := build_notification_message(notification, esi_app, esi_client)) > 0:
                await channel.send(message)
        except Exception as e:
            logger.error(
                f"Could not send notification to character_id {character_id} / user_id {user_id}: {e}")
        else:
            # Set that this notification was handled
            old_notifications[str(notification_id)] = "handled"


async def send_state_message(structure, channel, character_id=0, user_id=""):
    """given a structure object and a channel, send a message if the structure state changed"""
    structure_state = structure.get('state')
    with shelve.open('../data/structure_states', writeback=True) as structure_states:
        if (structure_key := str(structure.get('structure_id'))) in structure_states:
            if not structure_states[structure_key] == structure_state:
                try:
                    await channel.send(
                        f"Structure {structure.get('name')} changed state:\n"
                        f"{structure_info(structure)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Could not send structure state change to character_id {character_id} / user_id {user_id}: {e}")
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
            except Exception as e:
                logger.error(
                    f"Could not send structure state change to character_id {character_id} / user_id {user_id}: {e}")
            else:
                structure_states[structure_key] = structure_state


async def send_fuel_message(structure, channel, character_id=0, user_id=""):
    """given a structure object and a channel, send a message if fuel went low"""
    with shelve.open('../data/structure_fuel', writeback=True) as structure_fuel:
        if (structure_key := str(structure.get('structure_id'))) in structure_fuel:
            if not structure_fuel[structure_key] == fuel_warning(structure):
                try:
                    await channel.send(
                        f"{fuel_warning(structure)}-day warning, structure {structure.get('name')} is running low on fuel:\n"
                        f"{structure_info(structure)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Could not send structure fuel warning to character_id {character_id} / user_id {user_id}: {e}")
                else:
                    # The message has been sent without any exception, so we can update our db
                    structure_fuel[structure_key] = fuel_warning(structure)

        else:
            # Add structure to fuel db quietly
            structure_fuel[structure_key] = fuel_warning(structure)


def downtime_is_now():
    current_time = datetime.utcnow()
    server_down_start = current_time.replace(hour=11, minute=0, second=0)
    server_down_end = current_time.replace(hour=11, minute=12, second=0)
    return server_down_start <= current_time < server_down_end


@tasks.loop(seconds=600)
async def notification_pings(esi_app, esi_client, esi_security, bot):
    """Periodically fetch notifications from ESI"""

    if downtime_is_now():
        return

    logger.info("running notification_pings")

    user_characters = shelve.open('../data/user_characters', writeback=True)
    try:
        for user_id, characters in user_characters.items():

            # Retrieve the channel associated with the user
            with shelve.open('../data/user_channels') as user_channels:
                user_channel = bot.get_channel(user_channels.get(user_id))

            for character_id, tokens in characters.items():

                # Fetch notifications from character
                esi_security.update_token(tokens)
                op = esi_app.op['get_characters_character_id_notifications'](character_id=character_id)
                notification_response = esi_client.request(op)

                for notification in reversed(notification_response.data):
                    await send_notification_message(notification, user_channel, character_id, user_id, esi_app,
                                                    esi_client)

    except Exception as e:
        logger.error(f"Got an unhandled exception: {e}", exc_info=True)
    finally:
        user_characters.close()


@tasks.loop(seconds=3600)
async def status_pings(esi_app, esi_client, esi_security, bot):
    """Periodically fetch structure state apu from ESI"""

    if downtime_is_now():
        return

    logger.info("running status_pings")

    user_characters = shelve.open('../data/user_characters', writeback=True)
    try:
        for user_id, characters in user_characters.items():

            # Retrieve the channel associated with the user
            with shelve.open('../data/user_channels') as user_channels:
                user_channel = bot.get_channel(user_channels.get(user_id))

            for character_id, tokens in characters.items():
                # Refresh ESI Token
                esi_security.update_token(tokens)
                user_characters[user_id][character_id] = esi_security.refresh()

                # Get corporation ID from character
                op = esi_app.op['get_characters_character_id'](character_id=character_id)
                corporation_id = esi_client.request(op).data.get("corporation_id")

                # Fetch structure data from character
                op = esi_app.op['get_corporations_corporation_id_structures'](corporation_id=corporation_id)
                results = esi_client.request(op)

                # Extracting and formatting data
                for structure in results.data:
                    # Fail if a structure is None
                    if structure is None:
                        continue

                    # Fail if we got back an error
                    if type(structure) is str:

                        # Every once in a while (approximately 2 days, shifting through timezones) warn users that this
                        # character will not result in pings
                        if status_pings.current_loop % 49 == 1:
                            character_name = esi_security.verify()['name']
                            await user_channel.send(
                                "### Warning\n "
                                "The following character does not have permissions to see structure info:\n"
                                f"{character_name}\n If you to not intend to use this character, "
                                f"remove him with `!revoke {character_name}`\n"
                                "otherwise, fix your corp permissions and check them with `!info`"
                            )
                            logger.info(f"Sent warning message to {character_name}")
                        continue

                    await send_state_message(structure, user_channel, character_id, user_id)
                    await send_fuel_message(structure, user_channel, character_id, user_id)

    except Exception as e:
        logger.error(f"Got an unhandled exception: {e}", exc_info=True)
    finally:
        user_characters.close()
