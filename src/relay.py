import shelve

from discord.ext import tasks

from structure_info import structure_info, fuel_warning


@tasks.loop(seconds=600)
async def external_pings(esi_app, esi_client, esi_security, bot):
    """Periodically fetches ESI and sends a message if anything interesting happened."""

    structure_states = shelve.open('../data/structure_states', writeback=True)
    structure_fuel = shelve.open('../data/structure_fuel', writeback=True)
    user_characters = shelve.open('../data/user_characters', writeback=True)
    user_channels = shelve.open('../data/user_channels', writeback=True)

    for user, characters in user_characters.items():
        # Retrieve the channel associated with the user
        channel_id = user_channels.get(user)
        user_channel = bot.get_channel(channel_id)

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
                # Fail if the character does not have permissions. TODO: Fail loud the first time this happens
                if type(structure) is str:
                    continue

                state = structure.get('state')
                structure_name = structure.get('name')
                structure_key = str(structure.get('structure_id'))

                if structure_key in structure_states:
                    if not structure_states[structure_key] == state:
                        try:
                            await user_channel.send(
                                f"Structure {structure_name} changed state:\n"
                                f"{structure_info(structure)}"
                            )
                        except Exception as e:
                            print(e)
                        else:
                            # The message has been sent without any exception, so we can update our db
                            structure_fuel[structure_key] = fuel_warning(structure)
                else:
                    # Update structure state and let user know
                    await user_channel.send(
                        f"Structure {structure_name} newly found in state:\n"
                        f"{structure_info(structure)}"
                    )
                    structure_states[structure_key] = state

                if structure_key in structure_fuel:
                    if not structure_fuel[structure_key] == fuel_warning(structure):
                        try:
                            await user_channel.send(
                                f"{fuel_warning(structure)}-day warning, structure {structure_name} is running low on fuel:\n"
                                f"{structure_info(structure)}"
                            )
                        except Exception as e:
                            print(e)
                        else:
                            # The message has been sent without any exception, so we can update our db
                            structure_fuel[structure_key] = fuel_warning(structure)

                else:
                    # Add structure to fuel db quietly
                    structure_fuel[structure_key] = fuel_warning(structure)
            
            # Fetch notifications from character
            op = esi_app.op['get_characters_character_id_notifications'](character_id=character_id)
            response = esi_client.request(op)

            for notification in reversed(response.data):
                notification_type = notification.get('type')

                if "Structure" in notification_type:
                    structure_id = None
                    for line in notification.get("text").split("\n"):
                        if "structureID:" in line:
                            structure_id = int(line.split(" ")[2])

                    if structure_id:
                        op = esi_app.op['get_universe_structures_structure_id'](structure_id=structure_id)
                        structure_name = esi_client.request(op).data.get("name", "Unknown")
                    else:
                        structure_name = "Unknown"

                    if notification_type == 'StructureLostArmor':
                        await user_channel.send(f"Structure {structure_name} has lost it's armor!\n")

                    elif notification_type == "StructureLostShields":
                        await user_channel.send(f"Structure {structure_name} has lost it's shields!\n")

                    elif notification_type == "StructureUnanchoring":
                        await user_channel.send(f"Structure {structure_name} is now unanchoring!\n")

                    elif notification_type == "StructureUnderAttack":
                        await user_channel.send(f"Structure {structure_name} is under attack!\n")

                    elif notification_type == "StructureWentHighPower":
                        await user_channel.send(f"Structure {structure_name} is now high power!\n")

                    elif notification_type == "StructureWentLowPower":
                        await user_channel.send(f"Structure {structure_name} is now low power!\n")

                    elif notification_type == "StructureOnline":
                        await user_channel.send(f"Structure {structure_name} went online!\n")

    structure_states.close()
    structure_fuel.close()
    user_characters.close()
    user_channels.close()
