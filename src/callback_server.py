import asyncio

from flask import Flask
from flask import request
from waitress import serve


def callback_server(esi_security, challenges, user_characters):
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
            return 'Authentication failed: State Missmatch', 403

        tokens = esi_security.auth(code)

        character_data = esi_security.verify()
        character_id = character_data["sub"].split(':')[-1]
        character_name = character_data["name"]

        # Store tokens under author
        if user_key not in user_characters:
            user_characters[user_key] = {character_id: tokens}
        else:
            user_characters[user_key][character_id] = tokens
        # asyncio.run(challenges[secret_state].send(f"Authenticated {character_name}"))

        return f"<p>Sucessfully authentiated {character_name}!</p>"

    serve(flask_app, port=80)
