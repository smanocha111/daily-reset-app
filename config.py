from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TIPS_JSON_PATH = PROJECT_ROOT / "data" / "tips.json"
STATE_FILE_PATH = PROJECT_ROOT / ".last_checked.json"

CHANNELS = [
    {
        "id": "UCGq-a57w-aPwyi3pIPAg6Jg",
        "name": "Diary of a CEO",
    },
    {
        "id": "UC2D2CMWXMOVWx7giW1n3LIg",
        "name": "Huberman Lab",
    },
]

DEFAULT_LOOKBACK_HOURS = 24
MAX_TRANSCRIPT_CHARS = 80_000
TIPS_PER_VIDEO = 3
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_MAX_TOKENS = 2048
OPENAI_TEMPERATURE = 0.3
