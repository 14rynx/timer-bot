# Timer-Bot

A discord bot that should notify you when your structures have been attacked, or are running low on fuel.
If you just want to know what is going on, without having to run a full Alliance Auth or SEAT just for this one feature.

## Inviting the bot

If you want to set up this bot quickly, you can use the following [invite link](https://discord.com/oauth2/authorize?client_id=1180817944813518879&permissions=3072&scope=bot).
For any things regarding maintenance I will try to notify people on [this discord sever](https://discord.com/invite/fT3eShrg5g).

## Setup

Since we need to connect to both ESI and discord, there is sadly still some things to do.
TLDR: Create an env file and fill it in with the CCP and Discord info, then run with docker compose.

1. Copy the .env file from the example
    ```shell
    cp .env.example .env
    ```

2. Head over to the [Discord Developers Website](https://discord.com/developers/) and create yourself an application.
    - Go to the "Bot" section and reset the token, then copy the new one. Put it in the .env file (`DISCORD_TOKEN=`).
    - Enable the "Message Content Intent" in the Bot section.
    - Invite your bot to your server in the "OAuth2" section. In the URL Generator, click on "Bot" and then
    further down "Send Messages" and "Read Mesasges/View Channels". Follow the generated URL and add the bot to your server.

3. Head over to the [Eve onlone Developers Page](https://developers.eveonline.com/) and create yourself an application.
    - Under "Application Type" select "Authentication & API Access"
    - Under "Permissions" add `esi-corporations.read_structures.v1
    - Under "Callback URL" set `https://yourdomain.com/callback/` (obviously replace your domain)

    Now view the application and copy the values `CCP_REDIRECT_URI`, `CCP_CLIENT_ID` and `CCP_SECRET_KEY` to your .env file.

4. Start the container
    If you run traefik as a reverse-proxy externally:
    ```shell
    docker-compose up -d --build
    ```

    Add the `LE_EMAIL=your_email@mailserver.com` to the .env file so that certbot can send you info about your certificates
    If you want to run this without any external things:
    ```shell
    docker-compose up -d --build -f docker-compose+traefik.yml
    ```
