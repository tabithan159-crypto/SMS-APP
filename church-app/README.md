# Church Members & Messaging App

Record members (name, phone, fellowship), import/export them as CSV,
and send SMS to everyone or to one fellowship.

## 1. Install (once)
```
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac / Linux / Termux
pip install -r requirements.txt
```

## 2. Create your admin login (once)
```
flask --app app create-admin
```

## 3. Run
```
flask --app app run
```
Open http://127.0.0.1:5000 and log in.

By default the app is in TEST MODE: messages are printed in the terminal and
nothing is sent. This is safe for trying it out.

## 4. Turn on real SMS (Africa's Talking)
Set these environment variables before running:
```
SMS_PROVIDER=africastalking
AT_USERNAME=your_username        # "sandbox" for testing
AT_API_KEY=your_api_key
AT_SENDER_ID=YOURCHURCH          # optional, must be approved
```
Windows (PowerShell):  `$env:SMS_PROVIDER="africastalking"`
Mac/Linux:             `export SMS_PROVIDER=africastalking`

Using another provider (e.g. Celcom Africa)? Open `app.py`, find `send_sms()`,
and add a new branch using that provider's API documentation.

## 5. Importing your existing members
Put them in a spreadsheet with columns `Name, Phone, Fellowship`, save as CSV,
then use Import. See `members_sample.csv`.

## Before going live (important)
- Member phone numbers are personal data. Keep the server private.
- Set a fixed `SECRET_KEY` environment variable (any long random text).
- Serve over HTTPS and set `HTTPS=1` so cookies are secure.
- Run with a production server, not the dev server:
  `gunicorn app:app`
- Back up `instance/church.db` regularly (or use Export CSV).
- Use strong admin passwords. Share logins only with trusted leaders.
