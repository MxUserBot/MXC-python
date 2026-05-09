# Building a Bot with MXC

This guide walks through creating a Matrix bot using the `mxc` library.

## 1. Setup

```bash
mkdir my-matrix-bot && cd my-matrix-bot
uv init
uv add mxc
```

If `mxc` is not on PyPI yet, install from GitHub:

```bash
uv add "mxc @ git+https://github.com/your-org/mxc.git"
```

## 2. Minimal Bot

Create `main.py`:

```python
import asyncio
import logging

from loguru import logger
from mautrix import Client
from mautrix.api import HTTPAPI
from mautrix.types import EventType, MessageEvent

from mxc import utils
from mxc.types import InterceptHandler


class Bot:
    def __init__(self, access_token: str, base_url: str):
        self.client = Client(api=HTTPAPI(base_url=base_url))
        self.client.api.token = access_token

    async def on_message(self, event: MessageEvent) -> None:
        await utils.answer(self, "Hello from MXC bot!", event=event)

    async def start(self) -> None:
        self.client.add_event_handler(EventType.ROOM_MESSAGE, self.on_message)
        await self.client.start()


async def main():
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    bot = Bot(
        access_token="YOUR_ACCESS_TOKEN",
        base_url="https://matrix.example.com",
    )
    await bot.start()
    await asyncio.Event().wait()


asyncio.run(main())
```

## 3. Structure for Larger Bots

For bots with multiple commands, use the `BaseCallBack` pattern:

```
my_bot/
├── main.py
├── bot.py          # Bot client setup
├── callbacks.py    # Event handlers
└── modules/
    ├── __init__.py
    ├── ping.py
    └── admin.py
```

### `bot.py`

```python
from mautrix import Client
from mautrix.api import HTTPAPI


class BotClient:
    def __init__(self, access_token: str, base_url: str, device_id: str = None):
        self.client = Client(api=HTTPAPI(base_url=base_url))
        self.client.api.token = access_token
        self.client.mxid = ...     # e.g. "@user:server"
        self.client.device_id = device_id or "MXC0001"
        self.interface = self       # or a wrapper with .client property
        self.active_modules = {}
```

### `callbacks.py`

```python
from mautrix.types import EventType, MessageEvent

from mxc.callback import BaseCallBack
from mxc import utils
from mxc.fsm import FSM


class BotCallBack(BaseCallBack):
    def __init__(self, bot):
        super().__init__(bot)
        self.fsm = FSM()

    async def message_cb(self, event: MessageEvent) -> None:
        """Handle incoming messages."""
        text = event.content.body.strip()

        if text == ".ping":
            await utils.answer(self.bot.interface, "Pong!", event=event)
        elif text.startswith(".echo "):
            await utils.answer(self.bot.interface, text[6:], event=event)
        else:
            # Check FSM state
            state = self.fsm.get_state(event)
            if state:
                await self._handle_state(event, state)
```

### `main.py`

```python
import asyncio
import logging

from mxc.types import InterceptHandler
from bot import BotClient
from callbacks import BotCallBack


async def main():
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    bot = BotClient(
        access_token="YOUR_TOKEN",
        base_url="https://matrix.example.com",
    )
    cb = BotCallBack(bot)
    bot.client.add_event_handler(EventType.ROOM_MESSAGE, cb.message_cb)

    await bot.client.start()
    await asyncio.Event().wait()


asyncio.run(main())
```

## 4. Sending Media

```python
from mxc.types import Image, Audio, Video, Document, Sticker
from mxc import utils

# Image from URL
await utils.answer(
    mx, room_id=room_id,
    media=Image(url="https://example.com/image.png", caption="<b>Look!</b>", w=800, h=600),
)

# Image from bytes
with open("photo.png", "rb") as f:
    await utils.answer(
        mx, room_id=room_id,
        media=Image(url=f.read(), filename="photo.png"),
    )

# Audio
await utils.answer(
    mx, room_id=room_id,
    media=Audio(url="mxc://server/audio_id", duration=120000),
)

# Document (any file)
await utils.answer(
    mx, room_id=room_id,
    media=Document(url="mxc://server/file_id", filename="report.pdf"),
)
```

## 5. Pagination with Emoji Reactions

```python
from mxc.utils.emoji import EmojiKeyBoard, EmojiButton
from mxc import utils

pages = ["Page 1 text", "Page 2 text", "Page 3 text"]

async def on_page(ctx):
    page = ctx.data["page"]
    if ctx.payload == "next":
        page = min(len(pages) - 1, page + 1)
    elif ctx.payload == "prev":
        page = max(0, page - 1)
    ctx.data["page"] = page
    await ctx.edit(pages[page])

markup = EmojiKeyBoard(
    rows=[[
        EmojiButton(emoji="⬅️", data="prev"),
        EmojiButton(emoji="➡️", data="next"),
    ]],
    callback=on_page,
    data={"page": 0},
    allowed_senders=event.sender,
    timeout=120,
)

await utils.answer(mx, pages[0], event=event, reply_markup=markup)
```

## 6. Stateful Conversations (FSM)

```python
from mxc.fsm import FSM, StatesGroup, State, FSMContext


class PollStates(StatesGroup):
    waiting_question = State()
    waiting_options = State()


fsm = FSM()


async def create_poll(mx, event):
    ctx = FSMContext(fsm, event)
    await ctx.set_state(PollStates.waiting_question)
    await utils.answer(mx, "Send me the poll question:", event=event)


async def handle_state(mx, event, state):
    ctx = FSMContext(fsm, event)

    if state == PollStates.waiting_question.state:
        await ctx.update_data(question=event.content.body)
        await ctx.set_state(PollStates.waiting_options)
        await utils.answer(mx, "Now send options (one per line):", event=event)

    elif state == PollStates.waiting_options.state:
        data = await ctx.get_data()
        options = event.content.body.split("\n")
        await utils.answer(
            mx,
            f"Poll created!\nQuestion: {data['question']}\nOptions: {options}",
            event=event,
        )
        await ctx.clear()
```

## 7. Error Handling

```python
from mxc.exceptions import UsageError
from mxc import utils


async def my_command(mx, event):
    try:
        args = event.content.body.split()
        if len(args) < 2:
            raise UsageError("Not enough arguments!")
        # ...
    except UsageError as e:
        await utils.answer(mx, f"❌ {e}", event=event)
```

## 8. Encrypted Rooms

For bots that need to handle encrypted rooms:

```python
from mautrix.crypto import OlmMachine
from mxc.crypto import BotSASVerification, MemoryCryptoStore


# Set up crypto
store = MemoryCryptoStore()
client.state_store = store
client.crypto = OlmMachine(client, store, store)
await client.crypto.load()

# Handle SAS verification
verifier = BotSASVerification(client)

# Allow key requests (to share keys with other sessions)
client.crypto.allow_key_requests = True
```

## 9. Full Example

See the [examples/](examples/) directory for a complete working bot.

## 10. Running

```bash
uv run python main.py
```

Or with systemd/tmux for production.

## Important Notes

- `mxc` is designed for `mautrix-python >= 0.21`
- The `BaseCallBack` class expects a `bot` object with `.interface` (has `.client` property) and `.active_modules` (dict)
- For simple bots, just use raw event handlers and call `mxc.utils` functions directly
- `utils.answer()` auto-detects room encryption and encrypts media attachments
