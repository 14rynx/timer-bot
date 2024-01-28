import logging
import shelve
import sys

from aiohttp import web
from discord.ext import tasks

# Fix for Mutable Mapping collection being moved
if sys.version_info.major == 3 and sys.version_info.minor >= 10:
    import collections

    setattr(collections, "MutableMapping", collections.abc.MutableMapping)
    setattr(collections, "Mapping", collections.abc.Mapping)

from esipy.exceptions import APIException

# Configure the logger
logger = logging.getLogger('callback')
logger.setLevel(logging.INFO)


@tasks.loop()
async def callback_server(esi_security):
    routes = web.RouteTableDef()

    @routes.get('/')
    async def hello(request):
        return web.Response(text="Hangar Script Callback Server<")

    @routes.get('/callback/')
    async def callback(request):
        # get the code from the login process
        code = request.query.get('code')
        state = request.query.get('state')

        try:
            with shelve.open('../data/challenges', writeback=True) as challenges:
                author_id = str(challenges[state])
        except KeyError:
            logger.warning(f"failed to verify challenge")
            return web.Response(text="Authentication failed: State Missmatch", status=403)

        try:
            tokens = esi_security.auth(code)

            character_data = esi_security.verify()
            character_id = character_data["sub"].split(':')[-1]
            character_name = character_data["name"]
        except APIException:
            logger.warning(f"failed to verify token")
            return web.Response(text="Authentication failed: Token Invalid", status=403)

        # Store tokens under author
        with shelve.open('../data/tokens', writeback=True) as author_character_tokens:
            if author_id not in author_character_tokens:
                author_character_tokens[author_id] = {character_id: tokens}
            else:
                author_character_tokens[author_id][character_id] = tokens

        logger.info(f"added {character_id}")
        return web.Response(text=f"Sucessfully authentiated {character_name}!")

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=80)
    await site.start()
