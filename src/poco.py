from datetime import datetime, timezone

from preston import Preston

ROMAN = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def int_to_roman(number):
    result = []
    for (arabic, roman) in ROMAN:
        (factor, number) = divmod(number, arabic)
        result.append(roman * factor)
        if number == 0:
            break
    return "".join(result)


def poco_name(poco_id: int, corporation_id: int, authed_preston: Preston):
    """ Resolve poco name"""
    try:
        asset_names = authed_preston.post_op(
            "get_corporations_corporation_id_assets_names",
            path_data={"corporation_id": corporation_id},
            post_data=[poco_id]
        )

        for asset_name in asset_names:
            if asset_name.get("item_id") == poco_id:
                return asset_name.get("name")

        return ""

    except Exception:
        return ""


def to_datetime(time_string: str | None) -> datetime | None:
    if time_string is None:
        return None
    return datetime.strptime(time_string, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def standing_info(label: str, value: float, allowed: bool):
    if allowed:
        return f"**{label}:** {value:.2f}%\n"
    else:
        return f"**{label}:** <no access>\n"


def poco_info(customs_office: dict, office_name: str) -> str:
    """Builds a human-readable message containing the state of a customs office, using Preston for name resolution."""

    message = f"### Customs Office {office_name}\n"

    # Reinforcement window
    start = customs_office.get('reinforce_exit_start')
    end = customs_office.get('reinforce_exit_end')
    if start is not None and end is not None:
        message += f"**Reinforcement Window:** {start:02d}:00 - {end:02d}:00 ET\n"

    # Access settings
    message += "#### Taxes"

    message += standing_info("Corporation", customs_office.get('corporation_tax_rate'), True)
    message += standing_info("Alliance", customs_office.get('alliance_tax_rate'),
                             customs_office.get('allow_alliance_access'))

    standing_access = customs_office.get('allow_access_with_standings')
    standing_required = customs_office.get('standing_level')

    excellent_allowed = standing_access and standing_required in ["excellent", "good", "neutral", "bad", "terrible"]
    message += standing_info("Excellent Standing", customs_office.get('excellent_standing_tax_rate'), excellent_allowed)

    good_allowed = standing_access and standing_required in ["good", "neutral", "bad", "terrible"]
    message += standing_info("Good Standing", customs_office.get('good_standing_tax_rate'), good_allowed)

    neutral_allowed = standing_access and standing_required in ["neutral", "bad", "terrible"]
    message += standing_info("Neutral Standing", customs_office.get('neutral_standing_tax_rate'), neutral_allowed)

    bad_allowed = standing_access and standing_required in ["bad", "terrible"]
    message += standing_info("Bad Standing", customs_office.get('bad_standing_tax_rate'), bad_allowed)

    terrible_allowed = standing_access and standing_required in ["terrible"]
    message += standing_info("Terrible Standing", customs_office.get('terrible_standing_tax_rate'), terrible_allowed)

    return message.strip()


def poco_notification_message(notification: dict, authed_preston: Preston) -> str:
    """Returns a human-readable message of a structure notification"""

    match notification.get('type'):
        case "OrbitalAttacked":
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
            return f"@everyone {get_poco_name(notification, authed_preston)} is under attack{attribution}!\n"
        case "OrbitalReinforced":
            return f"@everyone {get_poco_name(notification, authed_preston)} has ben reinforced!\n"
        case _:
            return ""


def get_poco_name(notification: dict, preston: Preston) -> str:
    """returns a structure id from the notification or none if no structure_id can be found"""
    planet_id = None
    for line in notification.get("text").split("\n"):
        if "planetID:" in line:
            planet_id = line.split(" ")[2]

    if planet_id is not None:
        return preston.get_op("get_universe_plantes_planet_id", planet_id=planet_id).get("name")
    return "Unknown Poco"


def get_attacker_character_id(notification: dict) -> int | None:
    """returns a character_id from the notification or None if no character_id can be found"""
    character_id = None
    for line in notification.get("text").split("\n"):
        if "aggressorID:" in line:
            character_id = int(line.split(" ")[1])
    return character_id


def is_poco_notification(notification: dict) -> bool:
    """returns true if a notification is about a structure"""
    # All structure notifications start with Structure... so we can use that
    return "Orbital" in notification.get('type')
