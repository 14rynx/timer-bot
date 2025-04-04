from datetime import datetime, timedelta, timezone

from preston import Preston

# Mapping of EVE states to human-readable states
state_mapping = {
    "anchor_vulnerable": "Anchoring timer ticking",
    "anchoring": "Waiting for anchoring timer",
    "armor_reinforce": "Reinforced for armor timer",
    "armor_vulnerable": "Armor timer ticking",
    "deploy_vulnerable": "Deployment timer ticking",
    "fitting_invulnerable": "Fitting Invulnerable",
    "hull_reinforce": "Reinforced for hull timer",
    "hull_vulnerable": "Hull timer ticking",
    "online_deprecated": "Online Deprecated",
    "onlining_vulnerable": "Waiting for quantum core",
    "shield_vulnerable": "Full Power",
    "unanchored": "Unanchored",
    "unknown": "Unknown"
}

# Days when a fuel warning is sent
fuel_warnings = [30, 7, 3, 2, 1, 0]


def to_datetime(time_string: str | None) -> datetime | None:
    if time_string is None:
        return None
    return datetime.strptime(time_string, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def structure_info(structure: dict) -> str:
    """Builds a human-readable message containing the state of a structure"""
    state = structure.get('state')
    structure_name = structure.get('name')

    formatted_state = state_mapping.get(state, "Unknown")

    structure_message = f"### {structure_name} \n"
    structure_message += f"**State:** {formatted_state}\n"

    if state in ["hull_reinforce", "armor_reinforce", "anchoring"]:
        state_expires = to_datetime(structure.get('state_timer_end'))
        if state_expires:
            structure_message += f"**Timer:** <t:{int(state_expires.timestamp())}> (<t:{int(state_expires.timestamp())}:R>) ({state_expires} ET)\n"
        else:
            structure_message += f"**Timer:** Unknown, please check manually!\n"

    fuel_expires = to_datetime(structure.get('fuel_expires'))
    if fuel_expires is not None:
        structure_message += f"**Fuel:** <t:{int(fuel_expires.timestamp())}> (<t:{int(fuel_expires.timestamp())}:R>) ({fuel_expires} ET)\n"
    else:
        # fuel_expires is None e.g. structure is anchoring
        if state in ["anchoring", "anchor_vulnerable"]:
            structure_message += f"**Fuel:** Not fueled yet (anchoring)\n"
        else:
            structure_message += f"**Fuel:** Out of fuel!\n"

    return structure_message


def next_fuel_warning(structure: dict) -> int:
    """Returns the next fuel warning level a structure is currently on"""
    fuel_expires = to_datetime(structure.get('fuel_expires'))
    if fuel_expires is not None:
        time_left = fuel_expires - datetime.now(tz=timezone.utc)

        for fuel_warning_days in fuel_warnings:
            if time_left > timedelta(days=fuel_warning_days):
                return fuel_warning_days

    # fuel_expires is None e.g. structure is anchoring or out of fuel
    return -1


def structure_notification_message(notification: dict, authed_preston: Preston) -> str:
    """Returns a human-readable message of a structure notification"""
    structure_name = authed_preston.get_op(
        "get_universe_structures_structure_id",
        structure_id=str(get_structure_id(notification)),
    ).get("name", "Unknown")

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


def is_structure_notification(notification: dict) -> bool:
    """returns true if a notification is about a structure"""
    # All structure notifications start with Structure... so we can use that
    return "Structure" in notification.get('type')
