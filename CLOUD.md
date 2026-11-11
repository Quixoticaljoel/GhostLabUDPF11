# Ghost Lab online: GitHub Pages + a free Oracle server

The simplest way to run Ghost Lab for a group, **with no PC and nothing at home**:

```
everyone's console ──UDP──▶ your free Oracle server ──HTTPS──▶ your GitHub Pages site, on any phone
```

- **Your home IP is never shared.** Consoles send to the Oracle server, not your house.
- **Everyone gets their own UDP port and access code**, and several people can drive at the same time.
- **You get an admin link** that sees every driver (live and saved laps) and adds or removes drivers from the website.
- **No logging in to the server, ever.** You paste one script when creating it, and it sets itself up and keeps itself up to date.
- **Free**, using Oracle's Always Free tier.

It takes about 30–40 minutes, most of it waiting. You can do it all on a phone, though a laptop makes the copying and pasting easier.

**Before you start:** your Ghost Lab repo should be on GitHub and **public** (README → "Putting it on GitHub"). GitHub Pages is nice to have but not needed: the server serves the app itself.

---

## Step 1: Get a free DuckDNS name

This gives your server a name, which the secure (HTTPS) connection needs.

1. Go to **duckdns.org** and sign in (Google, GitHub, Reddit and others work).
2. Under **domains**, type a name (e.g. `apex-ghost-4821`) and click **add domain**. Don't worry about the IP shown; the server fills in its own.
3. Keep the page open. You'll need the **token** shown near the top.

## Step 2: Fill in the setup helper

1. Open **`https://YOUR-USERNAME.github.io/ghost-lab/setup.html`** (or open `web/setup.html` from the zip in your browser).
2. Fill in:
   - **Subdomain:** your DuckDNS name from step 1, e.g. `apex-ghost-4821`
   - **Token:** copy and paste it from duckdns.org
   - **Drivers:** one name per line (include yourself)
   - **Repository:** `YOUR-USERNAME/ghost-lab`
   - **GitHub Pages address:** optional. Leave it blank and links open the app from your server.
3. Tap **Create my server script**.
4. **Save your details now:**
   - **Copy admin link** and keep it somewhere private (notes app, password manager). It's the key to everything.
   - **Download all as a text file** (or **Copy all**): everyone's links and codes, ready to send.

Everything is made in your browser. Nothing is sent anywhere, and the page remembers the codes if you come back to it.

## Step 3: Create a free Oracle Cloud account

1. Go to **oracle.com/cloud/free** and choose **Start for free**.
2. **Home region:** pick **UK South (London)**, or the region closest to you. This keeps the lag low, and **it can't be changed later**.
3. Oracle asks for a card to check you're a real person. Always Free resources aren't charged, and the account stays free unless you choose to upgrade it.

## Step 4: Create the server and paste the script

In the Oracle Cloud console: **☰ menu → Compute → Instances → Create instance**. It's a five-page form; fill in each page as below and click **Next** to move on.

**Page 1: Basic information**

| Setting | Choose |
|---|---|
| **Name** | `ghost-lab` |
| **Placement** | leave as it is |
| **Shape** (do this *first*) | **Change shape → VM.Standard.E2.1.Micro** (marked *Always Free-eligible*). The Ampere **VM.Standard.A1.Flex** with 1 OCPU and 6 GB also works. If one says *out of capacity*, try the other. |
| **Image** | **Change image → Ubuntu → Canonical Ubuntu 24.04** (the plain one, not *Minimal* or *aarch64*, unless you picked A1.Flex) |
| **Advanced options → Management** (the expandable section *below* Image and shape) | Under **Initialization script**, choose **Paste cloud-init script**. Go back to the setup helper, tap **Copy script**, and paste it here. |

> The script box is on page 1. If you've already gone past it, click **Back**.

**Page 2: Security**
- **Shielded instance: off**
- **Confidential computing: off**

Ghost Lab doesn't need either, and switching them on can rule out the free shape.

**Page 3: Networking**
- **Primary VNIC:** **Create new virtual cloud network** and **Create new public subnet** (the suggested names are fine).
- **Primary IP address:** tick **Automatically assign public IPv4 address**. Without it nobody can reach the server.
- **Add SSH keys:** **No SSH keys**. You never need to log in.

**Page 4: Storage**
- Leave the boot volume at its default size (Always Free covers up to 200 GB).

**Page 5: Review**
- Check the shape says *Always Free-eligible*, then click **Create**.

When the status turns **Running**, note the **Public IP address** on the instance page, e.g. `141.147.12.34`. Consoles send to this address.

> The script contains your DuckDNS token and access codes. Only your Oracle account can see it, so don't share it.

## Step 5: Open the ports in Oracle's firewall

Oracle blocks everything from outside until you allow it.

1. On the instance page, under **Primary VNIC**, click the **Subnet** link.
2. Open **Security** (or **Security Lists**), then **Default Security List**.
3. Click **Add Ingress Rules** and add these three (each as its own rule, Source CIDR `0.0.0.0/0`):

| Source CIDR | IP Protocol | Destination Port Range | For |
|---|---|---|---|
| `0.0.0.0/0` | **UDP** | `20777-20799` | game telemetry (one port per driver) |
| `0.0.0.0/0` | **TCP** | `80` | getting the free HTTPS certificate |
| `0.0.0.0/0` | **TCP** | `443` | the app and live data |

## Step 6: Wait, then open your admin link

The server installs itself and gets its HTTPS certificate. Give it **about 15 minutes**, then open your **admin link** on your phone.

You should see the Ghost Lab detection screen with **Connected as admin**, and a driver picker at the top of the dashboard. Add it to your home screen:
- **iPhone:** Share → **Add to Home Screen**
- **Android:** ⋮ menu → **Add to Home screen**

Not working yet after 20 minutes? See Troubleshooting below.

## Step 7: Set up your console

In F1 26: **Settings → Telemetry Settings → UDP Telemetry Settings**

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | **your server's Public IP** (step 4) |
| UDP Port | **your port** (from the setup helper, e.g. 20777) |
| UDP Send Rate | 60 Hz |
| UDP Format | 2026 |

Start a Time Trial and your car appears.

## Step 8: Send everyone their details

Send each driver:
1. **Their link** (from the downloaded text file). They open it once, and the app remembers the server and their code.
2. **The UDP settings:** your server's **Public IP** and **their own port**.

In the app, the **Drivers** panel (driver picker → *Manage drivers…*) has a **Copy details** button that copies all of this for one person, including the IP.

---

## Using it

**Viewing anyone:** use the driver picker at the top of the dashboard. A green dot means that person is driving right now. You see their live car, ghosts, traces, sectors and saved laps.

**Managing drivers** (picker → *Manage drivers…*):

| Button | Does |
|---|---|
| **Add** | Creates a driver with the next free UDP port and a new code. It works straight away, with no restart. |
| **Copy details** | Copies that person's link, code, UDP IP and port, ready to paste into a message. |
| **View** | Switches the dashboard to that driver. |
| **New code** | Makes a new code. Their old link stops working (tap twice to confirm). |
| **Remove** | Removes the driver. Their saved laps stay on the server (tap twice to confirm). |

**Saved laps:** every complete lap (yours and the ghosts') is saved on the server, per driver. Load past laps with **Laps → Import → Saved on the server**, and use **Export** to keep copies or share them.

**Updates:** the server checks your GitHub repo once a day and updates itself, restarting only when something changed. Push changes to GitHub and the server picks them up overnight.

## Keeping it free

- **Don't upgrade the account** to Pay As You Go, and only create things marked **Always Free-eligible**.
- **Sign in to Oracle Cloud at least every couple of months.** Oracle keeps free accounts active while they're used.
- **Export laps now and then.** Oracle may reclaim Always Free servers that sit nearly idle for a week. If that ever happens: create a new instance with the **same script** (step 4) and the **same firewall rules** (step 5). Everyone's codes and ports come back as they were in the script, so links keep working (drivers you added later on the website need adding again). Then **Import** your exported laps.

## Privacy and security

- **Your home IP is never involved.** Friends only see the Oracle server's IP.
- **Everything is HTTPS**, with a free certificate that renews itself.
- **Access codes** protect live data and laps. Each driver only sees themselves, and only the admin code sees everyone. Keep the admin link private. If a code leaks, use **New code**.
- **The UDP ports** only accept game telemetry. While a driver's game is active, a second sender on the same port is ignored.
- **The public GitHub repo holds only code.** Codes, your DuckDNS token and laps live on the server, and in your browser for the setup helper.

## Troubleshooting

| Problem | Fix |
|---|---|
| **Admin link won't load after 20 minutes** | Check the three rules in step 5 (Oracle's firewall blocks everything else). On duckdns.org, your name's **current ip** should match the server's Public IP; if it doesn't, the script probably didn't run, so check step 4 (Ubuntu image, script pasted in *Initialization script*). |
| **"Out of capacity" when creating** | Try the other shape (E2.1.Micro or A1.Flex), or try again later. |
| **Forgot to paste the script** | Terminate the instance (**More actions → Terminate**, and tick *permanently delete the boot volume*) and create it again, pasting the script on page 1. |
| **Script ran but nothing works** | The simplest fix is to terminate the instance and create a new one with the same script. Advanced: Oracle's **Console connection** lets you read `/var/log/ghostlab-setup.log`. |
| **Stuck on "Listening for the game…"** | The console's UDP IP must be the server's **Public IP**, with **that person's port** and Broadcast **Off**. Telemetry only flows while you're on track. |
| **"Access code needed"** | Use the code from the setup helper, or copy fresh details from **Manage drivers**. Codes aren't case-sensitive. |
| **A friend's link opens but shows another person** | Each link carries its owner's code. Send each person their own link. |
