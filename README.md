# Interview Mate

Interview Mate is a local interview practice website with account sign-up, sign-in, a personal dashboard, and an AI HR interview coach.

## Run on Windows

1. Install Python 3.10 or newer if it is not already installed.
2. Open PowerShell in this folder and install the small set of app dependencies:

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

3. Start the website:

   ```powershell
   py -3 app.py
   ```

4. Open `http://127.0.0.1:5000/` in your browser. Keep the terminal window open while you use the site.

You can also double-click `START_DEMO.bat`; it installs the dependencies and starts the app.

To see the dashboard layout without creating an account first, open `http://127.0.0.1:5000/dashboard-preview.html`. This visual preview uses sample values. For the working site, open the main address, create an account, and sign in; that dashboard loads the signed-in user's saved data.

## Accounts and saved data

Create an account from **Get Started**, then sign in with that email and password. Passwords are stored as password hashes. The app creates `interview_mate.sqlite3` automatically the first time it runs. Account profiles, assessment scores, study plans, and daily goals are stored in that local database and linked to each account. All practice modules are available to signed-in accounts in the classroom demo.

The database stays on this computer. Copy `interview_mate.sqlite3` somewhere safe if you want to keep a backup. Removing that file removes the locally saved accounts and progress.

## AI HR interview

The HR module is available to every signed-in account and gives feedback after each answer plus a short summary at the end. The app uses the Gemini API from the Flask server when a `GEMINI_API_KEY` is configured. The key is never sent to the browser. If the API key is missing or Gemini is unavailable, the module switches to a built-in offline practice coach so the demo can continue.

For a new copy of the project, copy `.env.example` to `.env` and add your Gemini key there. The supplied project bundle's private `_env` settings file is also read automatically if present. Keep either settings file private.

## Notes

- Open the site through the Flask address above; do not open `index.html` directly.
- The site pages scroll vertically. On narrow screens, the dashboard and practice navigation can scroll sideways to reach every item.
- Sessions expire after 24 hours. The local signing key is created automatically and saved privately beside the app.
- Online payments are off by default for the classroom demo.
