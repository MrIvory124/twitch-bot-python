# twitch-bot-python

A small python coded twitch bot that contains:
- Live connection to specified twitch channel
- User message storing
- Opt in/out from message storing
- Microphone speech-to-text recognition
- Toggleable local llm message generation based on collected information (which is then sent into twitch chat as a user)

### How to use:
1. Clone repository
2. Create venv using requirements.txt
3. Download Ollama and pull the openhermes:v2.5 model (can be set up to work remotely)
4. Populate config.json with required information (Will flesh this out later)
5. While the bot is running, visit these links:

Log into the bot account and visit this:
> http://localhost:4343/oauth?scopes=user:read:chat%20user:write:chat%20moderator:read:chat_messages%20user:bot%20channel:moderate&force_verify=true

Log into the user that owns the account (your twitch channel) and visit:
> http://localhost:4343/oauth?scopes=channel:bot%20channel:moderate%20user:read:chat&force_verify=true

### TODO:
- Convert to allow for back and forth conversation with streamer mic
- Integration with obs
- Allow for choice between each ai gen message for having history or not
- Switch from JSON for saving sensitive information


