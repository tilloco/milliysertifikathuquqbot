# Law Quiz Telegram Bot

Sells access to quiz packs. Buyer picks a quiz, sends a payment screenshot,
you confirm manually with one command, they unlock the quiz and take it
right inside Telegram.

## 1. Get a bot token

1. Open Telegram, message **@BotFather**.
2. Send `/newbot`, give it a name and a username (must end in "bot").
3. BotFather gives you a token like `123456:ABC-DEF1234...`. Copy it.

## 2. Get your own numeric Telegram ID

Message **@userinfobot** on Telegram — it replies with your ID. This is
your `ADMIN_ID`, the account that gets payment screenshots and can run
`/confirm`.

## 3. Install and configure

```bash
cd lawquizbot
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Set your token and admin ID as environment variables (or edit `config.py`
directly):

```bash
export BOT_TOKEN="123456:ABC-DEF1234..."
export ADMIN_ID="123456789"
```

On Windows (PowerShell):
```powershell
$env:BOT_TOKEN="123456:ABC-DEF1234..."
$env:ADMIN_ID="123456789"
```

## 4. Add your real quiz questions

Open `seed_data.py`, replace the sample questions with your real ones
(same shape: question text, 4 options, index of the correct one), then:

```bash
python seed_data.py
```

Run it again for each quiz pack (change the `title`/`price_uzs` each time).
You can also add quizzes later from inside Telegram with
`/addquiz Title | Description | Price` sent by the admin account, though
you'll still need `seed_data.py`-style code to attach questions to it.

## 5. Run the bot

```bash
python bot.py
```

Leave this running. Message your bot on Telegram and try `/start`.

## 6. How a sale actually flows

1. Buyer sends `/start`, taps a quiz.
2. Bot shows the price and your payment details (edit these in `config.py`
   under `PAYMENT_INSTRUCTIONS` — put your real Click/Payme/card number).
3. Buyer pays outside Telegram, sends a screenshot back to the bot.
4. The bot forwards that screenshot to **you** (`ADMIN_ID`) with the
   buyer's ID and a ready-made command.
5. You check the payment actually landed, then send:
   `/confirm <user_id> <quiz_id>`
6. Buyer taps the quiz again — it's unlocked, questions start immediately
   with tappable A/B/C/D buttons, and they get a score at the end.

## 7. Keeping it running after you close your laptop

For real deployment (24/7, not just while your PC is on), push this
folder to GitHub and deploy it as a "worker" on Railway or Render —
same idea as your 17law deployment, but as a background worker instead
of a web service, since a bot doesn't need to listen on a port. Ask me
when you're ready for that step and I'll walk you through it.

## Notes on scaling this later

- Right now payment confirmation is manual (you eyeball the screenshot).
  Fine for launch. Once you have volume, Click/Payme has a merchant API
  you can wire into `/confirm` to make it automatic.
- Quiz content lives in this bot's own SQLite file. If you later want it
  to share content with your 17law web app, the cleanest move is to point
  this bot's `database.py` functions at your Rust backend's API instead
  of local SQLite — the bot logic in `bot.py` barely has to change.
