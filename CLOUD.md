# Ghost Lab: set up your free server

You need about 30 minutes, most of it waiting. Do it once.

## 1. DuckDNS (free name for your server)

1. Go to **duckdns.org** and sign in.
2. Type a name (e.g. `apex-ghost-4821`) and click **add domain**.
3. Keep the page open. You need the **token** at the top.

## 2. Make your script

1. Open `https://YOUR-USERNAME.github.io/ghost-lab/setup.html`
2. Fill in:
   - **Subdomain:** your DuckDNS name
   - **Token:** your DuckDNS token
   - **Drivers:** one name per line, including you
3. Tap **Create my server script**.
4. Tap **Copy admin link** and save it somewhere private. This is your key to everything.
5. Tap **Copy all** (or **Download**) to keep everyone's links and codes.
6. Tap **Copy script**. You paste this in step 3.

## 3. Create the Oracle server

Oracle Cloud → **☰ → Compute → Instances → Create instance**

**Page 1: Basic information**
1. **Name:** `ghost-lab`
2. **Shape (do this first):** Change shape → **VM.Standard.E2.1.Micro** (Always Free)
3. **Image:** Change image → **Ubuntu → Canonical Ubuntu 24.04**
4. **Advanced options → Management → Initialization script:** choose **Paste cloud-init script** and paste your script.

**Page 2: Security:** leave everything **off**.

**Page 3: Networking**
1. First time: **Create new virtual cloud network** and **Create new public subnet**. Making it again later: **Select existing virtual cloud network** and pick the existing **public** subnet, so the firewall rules from step 4 still apply.
2. Tick **Automatically assign public IPv4 address** (important)
3. **SSH keys:** No SSH keys

**Page 4: Storage:** leave as it is.

**Page 5: Review:** click **Create**.

When it says **Running**, write down the **Public IP address**.

## 4. Open the ports

On the instance page: **Subnet** link → **Security** → **Default Security List** → **Add Ingress Rules**.
Add three rules, each with Source CIDR `0.0.0.0/0`:

| Protocol | Port | For |
|---|---|---|
| UDP | `20777-20799` | game telemetry |
| TCP | `80` | HTTPS certificate |
| TCP | `443` | the app |

## 5. Wait and watch

On your phone, open `http://YOUR-PUBLIC-IP` (plain **http**).
A progress page shows **Step 1 of 4** to **Step 4 of 4**. It takes 10–20 minutes.

When it's done, open your **admin link**. You should see **Connected as admin**.

## 6. Set up each console

F1 26 → **Settings → Telemetry Settings → UDP Telemetry Settings**

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| Broadcast Mode | Off |
| UDP IP Address | your server's **Public IP** |
| UDP Port | that person's port (20777, 20778, …) |
| Send Rate | 60 Hz |
| UDP Format | 2026 |

Send each driver their own link and port. Start a Time Trial and the car appears.

## If something goes wrong

| Problem | Fix |
|---|---|
| Progress page won't load | Check the **public IPv4** was ticked and the TCP 80 rule exists. |
| Progress page shows **Problem:** | Read the message. Usually: terminate the instance and create it again with the same script. |
| Admin link won't load | Check the TCP 443 rule. On duckdns.org, **current ip** must match the server's Public IP. |
| "Shape not valid for image" | Pick the **shape first**, then the image. |
| "Out of capacity" | Try again later, or use shape **VM.Standard.A1.Flex** (1 OCPU, 6 GB) with the **aarch64** Ubuntu image. |
| Stuck on "Listening for the game…" | Console UDP IP must be the **Public IP**, with the right port and Broadcast **Off**. Telemetry only flows on track. |

**Starting again:** Instance → **More actions → Terminate** (tick *delete boot volume*), then repeat step 3 with the **same script**, choosing the **existing** network and public subnet. If you made a new network instead, add the step 4 rules again.

**Keep it free:** only use things marked *Always Free*, don't upgrade the account, and sign in to Oracle every couple of months.
