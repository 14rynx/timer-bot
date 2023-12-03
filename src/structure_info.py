# Mapping of EVE states to human-readable states
from datetime import datetime, timedelta, timezone

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

fuel_warnings = [30, 7, 3, 2, 1]


def structure_info(structure) -> str:
    state = structure.get('state')
    structure_name = structure.get('name')

    formatted_state = state_mapping.get(state, "Unknown")

    structure_message = f"### {structure_name} \n"
    structure_message += f"**State:** {formatted_state}\n"

    if state in ["hull_reinforce", "armor_reinforce", "anchoring"]:
        state_expires = structure.get('state_timer_end').v
        state_expires_rd = state_expires.strftime('%d.%m.%y %H:%M ET')
        state_expires_ts = int(state_expires.timestamp())
        structure_message += f"**Timer:** <t:{state_expires_ts}> (<t:{state_expires_ts}:R>) ({state_expires_rd})\n"

    fuel_expires = structure.get('fuel_expires').v
    fuel_expires_rd = fuel_expires.strftime('%d.%m.%y %H:%M ET')
    fuel_expires_ts = int(fuel_expires.timestamp())
    structure_message += f"**Fuel:** <t:{fuel_expires_ts}> (<t:{fuel_expires_ts}:R>) ({fuel_expires_rd})\n"

    return structure_message


def fuel_warning(structure):
    fuel_expires = structure.get('fuel_expires').v
    time_left = fuel_expires - datetime.now(tz=timezone.utc)

    for fuel_warning_days in fuel_warnings:
        if time_left > timedelta(days=fuel_warning_days):
            return fuel_warning_days

    if time_left.days < 0:
        return 0
