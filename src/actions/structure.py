import logging
from datetime import datetime, timedelta, timezone

from messaging import send_background_message
from models import Structure

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
fuel_warnings = [30, 15, 7, 3, 2, 1, 0]

# Configure the logger
logger = logging.getLogger('discord.timer.structure')


def to_datetime(time_string: str | None) -> datetime | None:
    if time_string is None:
        return None
    return datetime.strptime(time_string, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def structure_info_text(structure: dict) -> str:
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


async def send_structure_message(structure, bot, user, identifier="<no identifier>"):
    """For a structure state if there are any changes, take action and inform a user"""

    structure_db, created = Structure.get_or_create(
        structure_id=structure.get('structure_id'),
        defaults={
            "last_state": structure.get('state'),
            "last_fuel_warning": next_fuel_warning(structure),
        },
    )

    if created:
        message = f"Structure {structure.get('name')} newly found in state:\n{structure_info_text(structure)}"
        await send_background_message(bot, user, message, identifier)

    else:
        if structure_db.last_state != structure.get("state"):
            message = f"Structure {structure.get('name')} changed state:\n{structure_info_text(structure)}"
            if await send_background_message(bot, user, message, identifier):
                structure_db.last_state = structure.get("state")
                structure_db.save()

        current_fuel_warning = next_fuel_warning(structure)

        if structure_db.last_fuel_warning is None:  # Maybe remove this clause?
            structure_db.last_fuel_warning = current_fuel_warning
            structure_db.save()
            return

        elif current_fuel_warning > structure_db.last_fuel_warning:
            if structure_db.last_fuel_warning == -1:
                message = f"Structure {structure.get('name')} got initially fueled with:\n{structure_info_text(structure)}"
            else:
                message = f"Structure {structure.get('name')} has been refueled:\n{structure_info_text(structure)}"
            if await send_background_message(bot, user, message, identifier):
                structure_db.last_fuel_warning = current_fuel_warning
                structure_db.save()
                return

        elif current_fuel_warning < structure_db.last_fuel_warning:
            state = structure.get('state')
            if current_fuel_warning == -1:
                if state in ["anchoring", "anchor_vulnerable"]:
                    return
                else:
                    message = f"Final warning, structure {structure.get('name')} ran out of fuel:\n{structure_info_text(structure)}"
            else:
                message = f"{structure_db.last_fuel_warning}-day warning, structure {structure.get('name')} is running low on fuel:\n{structure_info_text(structure)}"
            if await send_background_message(bot, user, message, identifier):
                structure_db.last_fuel_warning = current_fuel_warning
                structure_db.save()
                return
