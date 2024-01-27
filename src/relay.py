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
    user_channels = shelve.open('../data/user_channels', writeback=True)
    old_notifications = shelve.open('../data/old_notifications', writeback=True)
    try:
        for user, characters in user_characters.items():

            # Retrieve the channel associated with the user
            user_channel = bot.get_channel(user_channels.get(user))

            for character_id, tokens in characters.items():

                # Fetch notifications from character
                esi_security.update_token(tokens)
                op = esi_app.op['get_characters_character_id_notifications'](character_id=character_id)
                notification_response = esi_client.request(op)

                for notification in reversed(notification_response.data):
                    # Fail if the notification is an error or None
                    if notification is None or type(notification) is str:
                        continue

                    if not is_structure_notification(notification):
                        continue

                    # Check if this notification was sent out previously and skip it
                    if str(notification_id := notification.get("notification_id")) in old_notifications:
                        continue

                    try:
                        if len(message := build_notification_message(notification, esi_app, esi_client)) > 0:
                            await user_channel.send(message)
                    except Exception as e:
                        logger.error(
                            f"Could not send notification to character_id {character_id} / user_id {user}: {e}")
                    else:
                        # Set that this notification was handled
                        old_notifications[str(notification_id)] = "handled"

    except Exception as e:
        logger.error(f"Got an unhandled exception: {e}", exc_info=True)
    finally:
        user_characters.close()
        user_channels.close()
        old_notifications.close()


@tasks.loop(seconds=3600)
async def status_pings(esi_app, esi_client, esi_security, bot):
    """Periodically fetch structure state apu from ESI"""

    if downtime_is_now():
        return

    logger.info("running status_pings")

    structure_states = shelve.open('../data/structure_states', writeback=True)
    structure_fuel = shelve.open('../data/structure_fuel', writeback=True)
    user_characters = shelve.open('../data/user_characters', writeback=True)
    user_channels = shelve.open('../data/user_channels', writeback=True)
    try:
        for user, characters in user_characters.items():
            # Retrieve the channel associated with the user
            channel_id = user_channels.get(user)
            user_channel = bot.get_channel(channel_id)

            if not user_channel:
                continue

            for character_id, tokens in characters.items():
                # Refresh ESI Token
                esi_security.update_token(tokens)
                user_characters[user][character_id] = esi_security.refresh()

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

                    structure_state = structure.get('state')
                    structure_name = structure.get('name')

                    if (structure_key := str(structure.get('structure_id'))) in structure_states:
                        if not structure_states[structure_key] == structure_state:
                            try:
                                await user_channel.send(
                                    f"Structure {structure_name} changed state:\n"
                                    f"{structure_info(structure)}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Could not send structure state change to character_id {character_id} / user_id {user}: {e}")
                            else:
                                # The message has been sent without any exception, so we can update our db
                                structure_states[structure_key] = structure_state
                    else:
                        try:
                            # Update structure state and let user know
                            await user_channel.send(
                                f"Structure {structure_name} newly found in state:\n"
                                f"{structure_info(structure)}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Could not send structure state change to character_id {character_id} / user_id {user}: {e}")
                        else:
                            structure_states[structure_key] = structure_state

                    if structure_key in structure_fuel:
                        if not structure_fuel[structure_key] == fuel_warning(structure):
                            try:
                                await user_channel.send(
                                    f"{fuel_warning(structure)}-day warning, structure {structure_name} is running low on fuel:\n"
                                    f"{structure_info(structure)}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Could not send structure fuel warning to character_id {character_id} / user_id {user}: {e}")
                            else:
                                # The message has been sent without any exception, so we can update our db
                                structure_fuel[structure_key] = fuel_warning(structure)

                    else:
                        # Add structure to fuel db quietly
                        structure_fuel[structure_key] = fuel_warning(structure)

    except Exception as e:
        logger.error(f"Got an unhandled exception: {e}", exc_info=True)
    finally:
        structure_states.close()
        structure_fuel.close()
        user_characters.close()
        user_channels.close()
