import logging
import shelve

from flask import Flask
from flask import request
from waitress import serve

# Configure the logger
logger = logging.getLogger('callback')
logger.setLevel(logging.INFO)


def callback_server(esi_app, esi_client, esi_security, challenges):
    flask_app = Flask("Timer Callback Server")

    @flask_app.route("/")
    def hello_world():
        return "<p>Timer Script Callback Server</p>"

    @flask_app.route('/callback/')
    def callback():
        # get the code from the login process
        code = request.args.get('code')
        secret_state = request.args.get('state')

        try:
            user_key = str(challenges[secret_state].author.id)
        except KeyError:
            logger.info(f"got wrong secret in callback request: {secret_state}")
            return 'Authentication failed: State Missmatch', 403

        tokens = esi_security.auth(code)

        character_data = esi_security.verify()
        character_id = character_data["sub"].split(':')[-1]
        character_name = character_data["name"]

        # Store tokens under author
        with shelve.open('../data/user_characters', writeback=True) as user_characters:
            if user_key not in user_characters:
                user_characters[user_key] = {character_id: tokens}
            else:
                user_characters[user_key][character_id] = tokens

        # Mark all old notifications as read
        op = esi_app.op['get_characters_character_id_notifications'](character_id=character_id)
        response = esi_client.request(op)

        with shelve.open('../data/old_notifications', writeback=True) as old_notifications:
            for notification in response.data:
                notification_type = notification.get('type')
                notification_id = notification.get("notification_id")

                if "Structure" in notification_type:
                    old_notifications[str(notification_id)] = "skipped"

        logger.info(f"added {character_id}")
        return f"<p>Sucessfully authentiated {character_name}!</p>"

    serve(flask_app, port=80)
