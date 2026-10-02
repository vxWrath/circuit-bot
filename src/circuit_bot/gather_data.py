import aiohttp
import msgspec
import datetime
import asyncio
import re


def get_environment_variables() -> dict[str, str]:
    with open(".env", "r") as f:
        lines = f.readlines()

    env_vars: dict[str, str] = {}
    for line in lines:
        line = line.strip()

        if line.startswith("#") or line == "":
            continue

        key, value = line.split("=")
        env_vars[key.strip()] = value.strip()

    return env_vars

def get_env(key: str) -> str:
    env_vars = get_environment_variables()
    return env_vars[key]

USER_TOKEN = get_env("USER_TOKEN")
HEADERS    = {"Authorization": f"{USER_TOKEN}"}

URL        = "https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}&after={after}"
CHANNEL_ID = 0
LIMIT      = 100

BEFORE_DT = datetime.datetime(2026, 9, 21, tzinfo=datetime.timezone.utc)
AFTER_DT  = datetime.datetime(2026, 7, 17, tzinfo=datetime.timezone.utc)

class Message(msgspec.Struct):
    id: str
    timestamp: str
    embed_description: str

async def fetch_messages() -> list[Message]:
    print("Fetching messages...")
    
    # Calculate initial snowflake timestamp manually to avoid dependency quirks
    # Discord epoch (2015-01-01T00:00:00Z) in milliseconds = 1420070400000
    discord_epoch = 1420070400000
    after_id = int((AFTER_DT.timestamp() * 1000) - discord_epoch) << 22

    messages: list[Message] = []

    async with aiohttp.ClientSession() as session:
        while True:
            url = URL.format(channel_id=CHANNEL_ID, limit=LIMIT, after=after_id)
            async with session.get(url, headers=HEADERS) as response:
                if response.status == 429:
                    data = await response.json()
                    retry_after = data.get("retry_after", 1)
                    print(f"Rate limited, retrying in {retry_after} seconds")
                    await asyncio.sleep(retry_after)
                    continue

                if response.status != 200:
                    print(f"Unexpected status code: {response.status}")
                    print(await response.text())
                    break

                data = await response.json()

            if not data:
                break

            after_id = int(data[0]["id"])

            new_games = 0
            stop_fetching = False

            # Iterate in reverse (from data[-1] to data[0]) to process 
            # messages from oldest to newest chronologically
            for message in reversed(data):
                timestamp = datetime.datetime.fromisoformat(message["timestamp"])

                # Break loop when hitting messages newer than BEFORE_DT
                if timestamp > BEFORE_DT:
                    print(f"Reached cutoff date: {timestamp} > {BEFORE_DT}")
                    stop_fetching = True
                    break

                if not message.get("embeds"):
                    continue

                embed = message["embeds"][0]
                if not embed.get("description"):
                    continue

                msg = {
                    "id": message["id"],
                    "timestamp": message["timestamp"],
                    "embed_description": embed["description"],
                }
                messages.append(msgspec.convert(msg, Message))
                new_games += 1

            print(f"Fetched {len(data)} messages. New games: {new_games} ({len(messages)} total)")

            if stop_fetching or len(data) < LIMIT:
                break

    return messages

def store_messages(messages: list[Message]):
    import json
    with open("messages.json", "w") as f:
        json.dump(msgspec.to_builtins(messages), f, indent=4)

class Game(msgspec.Struct):
    id: str | None
    team_one_id: str
    team_one_name: str
    team_one_score: str
    team_two_id: str
    team_two_name: str
    team_two_score: str
    timestamp: str

PATTERN = re.compile(
    r"(?:<:(?P<team_one_name>\w+):\d+>|:(?P<team_one_name_alt>\w+):)?\s*"
    r"<@&(?P<team_one_id>\d+)>\s*"
    r"(?:<:(?P<team_one_name_after>\w+):\d+>|:(?P<team_one_name_alt_after>\w+):)?\s*"
    
    r"\*{0,2}(?P<team_one_score>\d+)\*{0,2}\s*-\s*\*{0,2}(?P<team_two_score>\d+)\*{0,2}\s*"
    
    r"(?:<:(?P<team_two_name>\w+):\d+>|:(?P<team_two_name_alt>\w+):)?\s*"
    r"<@&(?P<team_two_id>\d+)>\s*"
    r"(?:<:(?P<team_two_name_after>\w+):\d+>|:(?P<team_two_name_alt_after>\w+):)?",
    
    re.DOTALL
)

def parse_message(text: str) -> dict[str, str | None] | None:
    match = PATTERN.search(text)

    if not match:
        print(f"Could not parse message: {text}")
        return None

    data = match.groupdict()

    # helper to pick first non-null value
    def pick(*vals: str) -> str | None:
        return next((v for v in vals if v), None)

    result = {
        "team_one_name": pick(
            data["team_one_name"],
            data["team_one_name_alt"],
            data["team_one_name_after"],
            data["team_one_name_alt_after"],
        ),
        "team_one_id": data["team_one_id"],
        "team_one_score": data["team_one_score"],
        "team_two_name": pick(
            data["team_two_name"],
            data["team_two_name_alt"],
            data["team_two_name_after"],
            data["team_two_name_alt_after"],
        ),
        "team_two_id": data["team_two_id"],
        "team_two_score": data["team_two_score"],
    }

    return result

def convert_to_games():
    import json
    with open("messages.json", "r") as f:
        messages = json.load(f)

    games: list[Game] = []
    for message in messages:
        result = parse_message(message["embed_description"])

        if not result:
            continue

        result["timestamp"] = message["timestamp"]
        result["id"] = None

        game = msgspec.convert(result, Game)
        games.append(game)

    games.sort(key=lambda g: datetime.datetime.fromisoformat(g.timestamp))

    with open("games/<>.json", "w") as f:
        json.dump(msgspec.to_builtins(games), f, indent=4)

def main():
    messages = asyncio.run(fetch_messages())
    store_messages(messages)

    convert_to_games()

main()