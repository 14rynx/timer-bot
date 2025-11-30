# Timer-Bot

A discord bot that should notify you when your [Eve-Online](https://www.eveonline.com) structures have been attacked, or are running low on fuel.
If you just want to know what is going on, without having to run a full [Alliance Auth](https://apps.allianceauth.org/) or [SeAT](https://github.com/eveseat/seat) just for this one feature.

![Info](https://friendly-splash.space/Tools/timer-notifications-images/info-command.png)

## Public Instance

You can use the following [invite link](https://discord.com/oauth2/authorize?client_id=1180817944813518879&permissions=3072&scope=bot) to add the bot to your discord server.
Please also join my [discord sever](https://discord.com/invite/fT3eShrg5g) if you want any info on updates / maintenance. Please note the Data Protection and TOS rules at the bottom of the REDME.md.

To set up the bot, do the follwing:
- Make sure your character(s) you want to use have the `Station Manager` role. You can find it in game under "Corporation" -> "Administration" -> "Role Management" -> "Station Services".
- Use `/auth`to get an auhtorization link.  Grant to the bot access to your structure info and notifications.

Now you can use any of the other commands:
- `/characters` to see a list of authorized characters.
- `/info` to see all your structures and timers / fuel.
- `/callback` to set on which channel you want to recieve notifications.
- `/revoke` to delete the esi tokens and stop using the bot.

### Demonstration Video
Note: This video is slightly outdated. The bot now uses `/` to start commands and not `!`.
[![Demonstration Video](https://img.youtube.com/vi/s6n5UfaSpWg/0.jpg)](https://www.youtube.com/watch?v=s6n5UfaSpWg)

## Self-Hosting

Since we need to connect to both ESI and discord, there is sadly still some things to do.
TLDR: Create an env file and fill it in with the CCP and Discord info, then run with docker compose.

1. Clone this repository
    ```shell
    git clone https://github.com/14rynx/timer-bot.git
    ```
   
2. Copy the .env file from the example
    ```shell
    cp .env.example .env
    ```

3. (Optional) To use PostgreSQL instead of SQLite, add the following to your .env file:
    ```bash
    DB_HOST=postgres
    DB_NAME=timer_bot
    DB_USER=postgres
    DB_PASSWORD=your_secure_password_here
    DB_PORT=5432
    ```
    
    If `DB_HOST` is not set, the bot will use SQLite (`data/bot.sqlite`) by default.

4. Head over to the [Discord Developers Website](https://discord.com/developers/) and create yourself an application.
    - Go to the "Bot" section and reset the token, then copy the new one. Put it in the .env file (`DISCORD_TOKEN=`).
    - Enable the "Message Content Intent" in the Bot section.
    - Invite your bot to your server in the "OAuth2" section. In the URL Generator, click on "Bot" and then
    further down "Send Messages" and "Read Mesasges/View Channels". Follow the generated URL and add the bot to your server.

5. Head over to the [Eve onlone Developers Page](https://developers.eveonline.com/) and create yourself an application.
    - Under "Application Type" select "Authentication & API Access"
    - Under "Permissions" add `esi-universe.read_structures.v1, esi-corporations.read_structures.v1, esi-characters.read_notifications.v1`
    - Under "Callback URL" set `https://yourdomain.com/callback/` (obviously replace your domain)

    Now view the application and copy the values `CCP_REDIRECT_URI`, `CCP_CLIENT_ID` and `CCP_SECRET_KEY` to your .env file.

6. Start the container.
    + If you run traefik as a reverse-proxy externally:
      ```shell
      docker-compose up -d --build
      ```
    
    + If you want to run this without any external reverse proxy:
      - Add the `LE_EMAIL=your_email@mailserver.com` to the .env file so that letsencrypt certbot can send you info about your https certificates
      - Run the docker compose with both timer-bot and traefik with the following command
      ```shell
      docker-compose up -d --build -f docker-compose+traefik.yml
      ```

7. You can now invite the bot to your server and continue like the public instance.
   You should be able to get an invite link from the discord admin panel, or you can fill in the blank in this one
   ```
   https://discord.com/oauth2/authorize?client_id=<YOUR_CLIENT_ID_GOES_HERE>&permissions=3072&scope=bot
   ```

# Public Instance Terms of Service and Data Protection Rules
## Terms of Service
By using the public instance Timer-Bot via the invite link you are agreeing to the following terms of service between you and Larynx Austrene:
- Timer-bot is provided as a best-effort service free of charge. Altough timer-bot is internally redundant and will automatically fix missed timers, we do not take any responsibility for missed timers due to force majeure. If you want to achieve waterproof guarantees host your own instance.
In timer bot, the following mechanisms are used to notify users of data transmission problems: 
  - In case of a connectivity problem with EvE ESI, you will be notified via discord.
  - If there is an issue with the set up discord channel, timer-bot will attempt using private messages and warn you. 
  - If that is not possible you will show up in the public endpoint [timer.synthesis-w.space/unreachable](https://timer.synthesis-w.space/unreachable). 
  - If you are unreachable by both eve and discord your account will be deleted after 100 unsucessful attempts.
  - Any manual attemps at fixing your data reception are not required by Larynx Austrene and are solely your responsibility.
- The public instance of timer-bot is primarily intended for small, independent entities in eve-online which run no or little private IT-infrastructure. You are allowed to use the service even in large corporations/alliances, but in cases where a single entity takes up significant resources, we reserve the right to exclude you from the service.
- The service may be discontinued at any time by Larynx Austrene, users will be notified by the built-in call-to-action command.
- And disputes will be handled at Court for Trial by Fire - Planet V - J204815 - D-R00016 - Anoikis.
## Data Protection Rules
- The timer-bot public instance is hosted in germany and stores the following information about your eve online characters and discord user. To protect your identity, you are required to not use any real-world names when signing up to this service.
  - Your API Keys, corporation and character names as well as discord channel and user ids as well as current structure state during the extent of you using the service
  - Logs containing your user and character id until the restart of the service, which happens only at new deployment or uppon request.
  - Structure and Poco related notification ids for up to 3 days to uniquely identify them.
  - Other notifications are filtered in-flight and not stored to disk
- If you wish to remove your data and API tokens, you can use the /revoke command, data is deleted immediately and irrevocably with the exception of log data which will remain until the next bot restart.
- For data related concerns, you can contact Larynx Austrene in game, via larynx.austrene@gmail.com or discord user larynx.com
