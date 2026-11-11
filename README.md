# Ghost Lab

Live F1 26 / F1 25 Time Trial telemetry on your phone: a 3D track map with camera views, your car, your PB ghost and the rival ghost, sectors, speed/throttle/brake traces, and every lap saved.

```
everyone's console ──UDP──▶ your free Oracle Cloud server ──HTTPS──▶ the app, on any phone
```

- Several drivers at once, each with their own UDP port and access code.
- An admin link that sees every driver and adds or removes people from the website.
- Laps export and import as `.ghostlab` files.
- Free: GitHub for the code, Oracle Always Free for the server, DuckDNS for the name.

## What's in here

| Path | What it is |
|---|---|
| `bridge.py` | The server program: receives the game's UDP telemetry and streams it to phones. Python standard library only. |
| `server/install.sh` | Sets the server up (HTTPS, DuckDNS, drivers, daily updates). Run for you by the setup script. |
| `server/ghostlab.py` | `sudo ghostlab …` commands on the server, if you ever log in. |
| `web/` | The phone app, plus `setup.html`, the setup helper. |
| `.github/workflows/pages.yml` | Publishes `web/` to GitHub Pages whenever you push. |

## Putting it on GitHub

1. On github.com, click **+ → New repository**.
   - Name: `ghost-lab`
   - **Public** (the server downloads the code from here, and free Pages needs a public repo)
   - Don't add a README. Click **Create repository**.
2. On the empty repo page, click **uploading an existing file**. Unzip `ghost-lab.zip` on your computer, open the `ghost-lab` folder, select **everything inside it** and drag it onto the page. Click **Commit changes**.
3. Check the workflow came across: you should see a `.github` folder in the repo. If you don't (Macs hide folders starting with a dot), click **Add file → Create new file**, type the name `.github/workflows/pages.yml`, paste in the contents of that file from the zip, and click **Commit changes**.
4. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
5. **Actions** tab: open **Deploy to GitHub Pages**. If nothing has run, click **Run workflow**. When it goes green (about a minute), your site is at `https://YOUR-USERNAME.github.io/ghost-lab/`.

Then follow **[CLOUD.md](CLOUD.md)** to create the server.

GitHub Pages is optional. Your server also serves the app at `https://your-name.duckdns.org/`, so if Pages ever won't deploy, everything still works from there.

## Keeping secrets out of the repo

The repo holds only code. Access codes, your DuckDNS token and laps live on the server (and in your browser for the setup helper). `.gitignore` keeps `drivers.json`, `*.env` and `laps/` out in case you ever run it locally.
