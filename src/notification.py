import logging

from preston import Preston

from models import Notification

# Configure the logger
logger = logging.getLogger('discord.timer.notification')


def get_structure_id(notification: dict) -> int | None:
    """returns a structure id from the notification or none if no structure_id can be found"""
    structure_id = None
    for line in notification.get("text").split("\n"):
        if "structureID:" in line:
            structure_id = int(line.split(" ")[2])
    return structure_id


def get_attacker_character_id(notification: dict) -> int | None:
    """returns a character_id from the notification or None if no character_id can be found"""
    character_id = None
    for line in notification.get("text").split("\n"):
        if "charID:" in line:
            character_id = int(line.split(" ")[1])
    return character_id


def structure_notification_text(notification: dict, authed_preston: Preston) -> str:
    """Returns a human-readable message of a structure notification"""
    try:
        structure_name = authed_preston.get_op(
            "get_universe_structures_structure_id",
            structure_id=str(get_structure_id(notification)),
        ).get("name")
    except Exception:
        structure_name = f"Structure {get_structure_id(notification)}"

    match notification.get('type'):
        case "StructureLostArmor":
            return f"@everyone Structure {structure_name} has lost it's armor!\n"
        case "StructureLostShields":
            return f"@everyone Structure {structure_name} has lost it's shields!\n"
        case "StructureUnanchoring":
            return f"@everyone Structure {structure_name} is now unanchoring!\n"
        case "StructureUnderAttack":
            # Parse attacker info
            character_id = get_attacker_character_id(notification)
            if character_id is not None:
                character_name = authed_preston.get_op(
                    'get_characters_character_id',
                    character_id=str(character_id)
                ).get("name", "Unknown")
                attribution = f" by [{character_name}](https://zkillboard.com/character/{character_id}/)"
            else:
                attribution = ""
            return f"@everyone Structure {structure_name} is under attack{attribution}!\n"
        case "StructureWentHighPower":
            return f"@everyone Structure {structure_name} is now high power!\n"
        case "StructureWentLowPower":
            return f"@everyone Structure {structure_name} is now low power!\n"
        case "StructureOnline":
            return f"@everyone Structure {structure_name} went online!\n"
        case _:
            return ""


def is_structure_notification(notification: dict) -> bool:
    """returns true if a notification is about a structure"""
    # All structure notifications start with Structure... so we can use that
    return "Structure" in notification.get('type')


async def send_notification_message(notification, user_channel, authed_preston, identifier="<no identifier>"):
    """For a notification from ESI take action and inform a user if required"""
    notification_id = notification.get("notification_id")

    if not is_structure_notification(notification):
        return

    notif, created = Notification.get_or_create(notification_id=notification_id)

    if not notif.sent:
        try:
            if len(message := structure_notification_text(notification, authed_preston)) > 0:
                await user_channel.send(message)
                logger.debug(f"Sent notification to {identifier}")

        except Exception as e:
            logger.warning(f"Could not send notification to {identifier}: {e}")
        else:
            notif.sent = True
            notif.save()
