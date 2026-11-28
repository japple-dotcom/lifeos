# Life OS

Your own household assistant, with a phone app you install on Android. The brain is **one AI at a time,
Claude or Gemini**. It works with Home Assistant, each person's Google Tasks, Calendar and (optionally)
Gmail, NFC tags and scheduled routines, and asks you with an **Approve / Reject** button before anything risky.

```
 Phone app (Android, installable) ─┐                      ┌─ Home Assistant (lights, plugs, sensors, robot, heating)
 NFC tags, Home Assistant         ─┼─▶  Life OS service  ─┼─ Google: each person's Tasks · Calendar · Gmail
 Schedules (cron)                 ─┘   Claude or Gemini   └─ ntfy push to your phones (approve / reject)
```

## Getting it: an app, updated from inside itself

Life OS runs on one computer at home (a Mac, a Windows PC, a mini PC or a Raspberry Pi) and the phones use it
through the installable phone app. Pick one:

| Where | How |
|---|---|
| **Mac (now)** | Unzip `lifeos.zip`, then in Terminal: `bash ~/Downloads/lifeos/scripts/install-mac.sh`. It finds Python 3.10+ on the Mac, installs everything privately, and puts **Life OS** in Applications with an icon in the menu bar. |
| **Mac, Windows, Linux, Raspberry Pi (standalone)** | Download the app from your GitHub releases page (`LifeOS-mac-arm64.dmg`, `LifeOS-windows.exe`, `lifeos-linux-x86_64`, `lifeos-linux-arm64`). Nothing else to install. Built by `.github/workflows/release.yml` (see "Publishing releases" below). |
| **Raspberry Pi or server without a screen** | The Linux download, then `./lifeos install-service`; or from source `./scripts/install-box.sh`. |
| **Docker** | `docker compose up -d --build` (as before). |

**The first time it opens, a setup page appears in the browser**: your names, the children's names, your area,
the AI key and (optionally) the phone address and Home Assistant. It shows the household key once, for the
phones. If you used Life OS before (a folder like `~/Documents/lifeos`), it offers to **bring your settings,
Google connections and history across**. On a box without a screen, the setup page is opened from another
computer with the address Life OS prints (it ends `?code=...`); or run `lifeos setup` for the same questions in
the terminal.

**Where things are kept**: settings, Google connections and history live in their own folder, never inside the
app: `~/Library/Application Support/Life OS` (Mac), `%APPDATA%\Life OS` (Windows), `~/.local/share/lifeos`
(Linux), or `LIFEOS_HOME`. A source folder that already has a `.env` keeps using itself.

### Updates, without reinstalling

Settings → **Updates and this computer**:
- **Update to 0.15.1**: shown when the update feed has a newer version (checked each morning, with a
  notification). Or turn on **Install updates overnight by themselves**.
- **Install an update file**: choose a `lifeos-update-<version>.zip` (from a phone or the computer).

An update is about 250 KB of code: every file is checked against the SHA-256 list inside it, it's installed
beside the current version (in `<home>/versions/`), and Life OS restarts into it. It starts **on trial**: if it
fails to start, or stops before it has run happily for 20 seconds, Life OS goes back to the version before by
itself and says so in Settings. Phones pick up the new screens automatically. Only adults signed in can install
updates. The rare update that needs new Python packages says so and links to the new app download instead (a
source install installs them itself). Also in this card: start with the computer, keys and addresses (Claude,
Gemini, the phone address, Home Assistant, DVLA, Octopus), restart, and quit (on that computer only).

### Publishing releases (GitHub, free)

Push this folder to a **public** GitHub repository (only code; no settings, keys or data are in it). Then
`git tag v0.15.1 && git push origin v0.15.1`: the workflow tests it, builds the app for Mac (Apple silicon and
Intel), Windows, Linux and Raspberry Pi, checks each one starts, and publishes them with the update file and
`latest.json`. Every copy built there checks `https://github.com/<you>/lifeos/releases/latest/download/latest.json`
for updates. Unsigned apps: on a Mac, the first open needs System Settings → Privacy & Security → **Open
Anyway** (the Terminal install avoids this); on Windows, **More info → Run anyway**. Signing removes these and
costs money (Apple Developer, Windows code-signing certificate).

To make an update file by hand: `python packaging/make_update.py` (writes `dist/lifeos-update-<version>.zip`;
notes come from `CHANGES.md`).

## Try it first, with sample data

From the app's setup page: **Look around with sample data**. Or from source:
```
pip install -r requirements.txt
python scripts/demo.py
```
Open http://localhost:8800/app/ (household key: `demo`) or the kitchen display at
http://localhost:8800/app/wall.html. It's a sample family with calendars, lists, dinners, chores, renewals and
an approval waiting, plus a simple stand-in assistant that understands "add eggs to shopping", "what's on
today" and "plan dinners". No accounts or keys needed; nothing is saved.

To see a job arranged end to end: To-do → the kitchen socket → **Send email to Spark Electrical**. About 20
seconds later "Dave" replies (pretend, nothing is really sent); within half a minute the card shows his reply
and a booking form filled in with the time he agreed.

For the family, `GUIDE.md` is a plain-English guide to using the app.

## The phone app

Five screens, built to be easy for everyone in the house:

| Screen | What it is for |
| --- | --- |
| **Today** | The latest morning brief, things waiting for your OK (big Approve / Reject), everyone's calendar combined, tasks due, renewals coming up |
| **Ask** | Talk or type to the assistant; it remembers the conversation, so "yes, do that" works. **Speak** for voice, **New conversation** to start fresh |
| **To-do** | **Smart to-dos** sorted for you, **Predicted** (what you'll need next), **Week** (the Sunday look ahead), **Projects**, simple lists (Shopping, with **Order online**), **this week's dinners** and **Renewals** |
| **Home** | One-tap buttons (Leaving home, Goodnight, Vacuum now...), the **Leaving home check**, **kids' chores and pocket money**, and device status |
| **Settings** | Notifications, Claude or Gemini, Google accounts for each person, text size, high contrast, read aloud, install |

Until everything is set up, Today shows a **Getting started** checklist for each person (install, connect
Google, notifications, renewals, dinners) that disappears once it's done. Google calendars and lists are
remembered for up to a minute so the app opens quickly; anything changed through Life OS shows immediately,
and **Refresh** on Today always fetches the latest.

### Everyday shortcuts

- **+ Add** (bottom right on every screen except Ask and Settings): type or speak a job. It shows where it went and
  what happens next, with **Open it** to jump to it.
- **Next up** on Today: your top three jobs, each with its main button (Send the email, Choose and buy, See the
  steps). Anything else due is listed under **Also due**.
- **Undo**: ticking a job hides it with **Done … Undo** for a few seconds; nothing changes in Google until then.
- **More** on each job: move it to **Tomorrow**, **This weekend** or **Next week**, or **Change** its name, date,
  list, who it's for, importance or next step. **Delete this job** is in the same box.
- **Search** and **Show** on the To-do screen: every word you type must match; filter by person, quick jobs,
  urgent ones or a list.
- **Plan my week**: free evening and weekend slots this week for jobs you'll do yourself, never overlapping. Untick
  any you don't want; **Add to calendar** puts the rest in.
- **Shopping mode** (To-do, Lists): big ticks, nothing else on screen, and the screen stays on until you tap
  **Finished shopping**.
- **Notifications about a job open that job**, highlighted.
- **Settings, Google accounts** has a checklist: the sign-in file, the address to paste into Google Cloud (with a
  Copy button) and who's connected.
- **Share into Life OS** from any app: in Chrome, Gmail, WhatsApp or Photos tap **Share → Life OS**. Add opens with
  the text and link filled in. Long-press the Life OS icon for an **Add a job** shortcut.
- **No signal?** Jobs you add and things you tick are kept on the phone (a yellow bar says how many) and go
  through as soon as Life OS can be reached again.

### Predictions: what you'll need before you need it

Everything done through Life OS is tracked, wherever it happens: ticked in the app, ticked in Google Tasks or
Gemini, bought on Amazon, or on a shop receipt in Gmail. From that history Life OS learns how often things come
round and sees the next one coming:

- **Shopping**: "Toilet roll: likely needed in about 3 days. You buy it about every 14 days."
- **Jobs that repeat**: bleeding the radiators, cleaning the oven, washing the car.
- **Check-ups and services** (`checkups` in `config/settings.yaml`): dentist every 6 months for everyone, eye
  tests, boiler service, MOT. Ticking off a matching job ("Book dentist for Ava") resets the clock. If it doesn't
  know when the last one was, it asks.
- **Birthdays** from the calendar: a present to sort two weeks ahead.

They live on their own tab, **To-do → Predicted**, not in your active to-dos, grouped **Due now**, **This month**
and **Later**, each with how sure it is and why. Each has **Add** (to Shopping or your to-dos), **Not yet** (back
in a week), **Not needed** (skip this time) and **Stop predicting**. Every choice teaches it.

What moves across by itself (each morning at 7:20): needs it's **very sure** about move onto Shopping 2 days
ahead, jobs a week ahead, check-ups a month ahead and presents two weeks ahead, with one notification listing
them. **Likely** ones stay on the Predicted tab and get a single reminder on the day. Change this with
`predictions.auto_add` (`high`, `medium` or `off`) and `predictions.lead_days`.

### Ordering the shopping online

To-do → Lists → Shopping → **Order online**:

- **Amazon, one tap**: items with a usual Amazon product go into your Amazon basket in one tap (you still press
  Buy on Amazon). Life OS learns the usual product from Amazon orders it suggested; or tap **Usual product** on any
  item and paste an Amazon link.
- **Your supermarket** (Tesco, Sainsbury's, Asda, Ocado, Morrisons, Waitrose): UK supermarkets don't let other
  apps fill your basket, so each item gets a **Find at Tesco** link, and **Copy the list** puts the whole list on
  the clipboard to paste into the supermarket's own list search (Sainsbury's SmartLists, Waitrose and Ocado have
  one). Set your usual shop with `shopping.supermarket`, or any other shop with `shopping.search_url`.
- **I've ordered these / I've ordered everything** ticks them off and remembers the purchase for predictions.

### Emails Life OS reads by itself

Only from household Gmail accounts that allowed it (never work email), each email once, labelled "Life OS/Handled":

- **Receipts and order confirmations** (`mail.receipts`): what you buy and how often, for predictions. Anything
  with a guarantee or warranty goes into **Renewals** with a reminder a month before it runs out.
- **School emails** (`school.senders`: the school's addresses or domain): dates go into the calendar, forms and
  payments become to-dos with their deadline, and "Pack PE kit for Ava (Thursday)" appears the day before. Without
  an AI connected, each school email becomes a "Read school email" to-do so nothing is missed.

### Money: subscriptions and spending (To-do → Money)

From receipts and payment emails Life OS already reads (and stays on the home computer):
- **Subscriptions**: each one with its cost and renewal date, and the monthly and yearly total. A few days before
  one renews (a week for yearly ones) a notification asks **Still using Disney+? £89.90 a year, renews Friday**,
  and mentions it if the price went up. **Keep** (asked again in a year), **Cancel it** (a to-do the day before,
  with the cancel link) or **Not a subscription**. Add one yourself too.
- **Spending**: this month by category (groceries, kids, household, eating out...) compared with the same point
  last month, the biggest changes, and the last six months. It's what receipts show, not a bank feed.

### Cars (To-do → Renewals)

Add a car by its registration. With a free DVLA Vehicle Enquiry API key (Settings → Keys) the **MOT and tax
dates come from the DVLA** and are checked every Monday, with a warning if it shows as untaxed or without an MOT.
Without a key, type the two dates once. Either way they go into Renewals with reminders.

### School holidays and childcare (To-do → Week)

School closures come from the school's emails, or paste the term dates from the school's website. For each
weekday the school is closed in the next eight weeks, Life OS checks whether **both adults are busy** (2+ hours
booked in school hours, from busy/free only) and nothing looks like childcare ("holiday club", "Grandma"...).
Each gap gets: **Ask someone by text** (or straight to a contact saved as family or childminder), **Find a
holiday club**, **Ask for time off** (a to-do) and **It's sorted** (puts "Childcare: Grandma" in the family
calendar). A heads-up arrives on Sunday evenings, and on Today when one is within three weeks.

### Appliance care (Predicted tab)

With Home Assistant, Life OS counts real cycles: the dishwasher finishing, the washing machine, a vacuum's filter
life. "Dishwasher salt: 27 cycles since 2 Nov; it's needed about every 30" appears on the Predicted tab and moves
to Shopping when due. Buying it or ticking the job off starts the count again. Set up in `appliance_care`.

### Cheapest time to run (Home)

On Octopus Agile or Go: put the tariff code in Settings → Keys and list the appliances on smart plugs under
`energy.appliances`. Home shows "Dishwasher: tomorrow 00:00 to 02:00, 9.4p (17.5p if now)". **Run it then**
switches that plug **on** at the cheap time (set the programme going with the plug off). Life OS never turns
anything off.

### Talking to it: Google speakers and Home Assistant Assist

Google switched off third-party voice apps on its speakers in June 2023, so "Hey Google, talk to Life OS" can't
exist. Life OS works through Home Assistant instead (Settings → **Voice and speakers** has everything to copy):

| How | Say | What happens |
|---|---|---|
| A Google Home routine (fixed phrase) | "Hey Google, what's for dinner?" | The routine presses a Home Assistant button (Google sees it as a scene); Life OS answers out loud on the speaker. Twelve phrases: today, dinner, what's next, shopping, waiting on, the week, running out, bins, chores, leaving check, the brief, the home manual. |
| Gemini for Home (or Gemini on the phone) | "Hey Google, add oat milk to my to-dos" | It lands in Google Tasks; within 5 minutes Life OS moves shopping to Shopping and sorts everything else, as before. |
| Announcements | — | The morning brief at 7:15 on weekdays on the kitchen speaker (`voice.announce`), quiet hours respected. Add bins on Wednesday night, "time to get ready for school", or anything else. |
| Home Assistant Assist (an HA Voice speaker, the HA app, a watch) | "Ask Life OS to move the dentist to Friday" | Anything at all goes to Life OS's assistant and the reply is spoken. The same safety rules apply: it can draft but not send, and anything that spends money or unlocks waits for a tap on a phone. |
| The kitchen display | Tap **Ask** and speak | Read-only questions ("what's on tomorrow?") answered on the screen and out loud. |
| A speaker just for Life OS | "Move the dentist to Friday" | A Home Assistant Voice speaker, or a Google Nest Mini with its board swapped for an open one: use the "everything goes to Life OS" sentences in Settings → Voice and speakers, and no "ask Life OS" is needed. |

Setup: connect Home Assistant (Settings → Keys) with the Google Cast integration on, pick the speaker, then copy
the package into `packages/lifeos_voice.yaml` and the sentences into `custom_sentences/en/lifeos.yaml` in Home
Assistant (packages switched on in `configuration.yaml`: `homeassistant: packages: !include_dir_named packages`),
add `lifeos_key: <household key>` to `secrets.yaml`, restart Home Assistant, and expose the Life OS
buttons to Google Home. In the Google Home app, make one routine per phrase: starter "When I say...", action
"Adjust Home devices → Scenes → Life OS: ...". **Say something** checks the speaker.

**Life OS as Home Assistant's conversation agent (v0.18).** Settings → Voice and speakers → Talk to Life OS on a
Home Assistant Voice speaker → **Download the Home Assistant integration**. Unzip it into Home Assistant's
`config/custom_components/` (giving `custom_components/lifeos/manifest.json`), restart, add the **Life OS**
integration (Life OS's address and the household key), then choose Life OS as the assistant's conversation agent.
Everything said to that speaker goes to Life OS (`POST /api/voice/converse`, household key), which answers in a
sentence or two and, when it asks a question back, tells the speaker to keep listening. The same safety rules
apply as everywhere else.

### Bills and direct debits (To-do → Money)

Energy, water, council tax, broadband, mobile, insurance: from bill and direct debit emails (`mail.bills`), or
added by hand. Each shows the amount, how often, the next payment and the monthly total for all of them. When a
bill comes in at least 5% and £3 higher than the last one, a **Bill gone up** notification gives the old and new
amounts and what it adds up to over a year. Attach the PDF or a photo to any bill.

### Trips and holidays (To-do → Projects)

Where, when, how you're getting there, abroad or not, kids and pets. Life OS makes a **Packing: Cornwall** list in
Google Tasks (tick it off from Gemini or Google Tasks too), the jobs before you go with sensible dates (someone
to keep an eye on the house, travel insurance and passports if abroad, airport parking and check-in if flying, a
car check if driving, pet care), an **Away** entry in the family calendar, and a warning if a passport in Renewals
runs out within six months of coming home. While you're away, shopping and appliance predictions pause. On the
morning you go, a notification reminds you to do the leaving check.

### Attachments: photos and PDFs

Renewals, home manual entries, bills and trips can each keep photos and PDFs: the insurance schedule, the
boiler's manual, a picture of the stopcock. They're kept on the home computer only (never Google), up to 15 MB
each, and only signed-in household members can open them.

### The school run (To-do → Week)

Each school day this week: drop-off and pick-up (and club pick-ups from calendar entries like "Ava swimming
club"), who's free from both calendars (busy/free only, never what the appointment is), and a suggested person,
shared out fairly. **Claim it** puts "School run: Pick-up (Sue)" in the family calendar. Sunday at 6:25pm a
notification flags any run with nobody free. Times are in `school_run`.

### Life admin hour (To-do → Week)

One hour a week for the paperwork: Life OS picks the three most pressing admin jobs (forms, renewals due soon,
returns, claims) and a free hour when someone is in. **Book it** puts it in the calendar with the three jobs in
the description; **Another time** offers the next one. Sunday at 6:40pm a notification suggests the week's hour
and the three jobs.

### Energy this week (Home)

With a kWh meter sensor and/or the heating's thermostat in Home Assistant (`energy.usage_entity`,
`energy.heating_entity`): this week's electricity and roughly what it cost, hours the heating ran, both against
last week, and one suggestion ("The heating ran 7 hours more than last week. Turning the thermostat down by one
degree typically saves about a tenth of heating costs."). Also in the Sunday evening notification.

### Getting set up (Settings)

A checklist of everything that makes Life OS useful, ticked off by itself as it's done: the essentials (names,
Google for each adult, the AI key, the phones' address, notifications) and then the household and the extras,
each with a **Go** link to the right place. Today shows the essentials until they're done.

### Check everything (Settings)

Tests every real connection from the app (settings, Google for each adult, Home Assistant, the assistant,
notifications, the speaker, the Gemini and Google Tasks check, backups, disk space, the phones' address) and lists
anything to fix with a **Go there** link. Tick the box to include a tiny test message to the assistant.
`scripts/doctor.py` runs the same checks from a terminal.

### Backups off this computer (Settings → Backups)

Every night at 2:40am an encrypted copy goes to a folder: a USB stick, or a folder that Google Drive for desktop,
iCloud Drive or OneDrive keeps in sync. It holds the database, settings, keys and Google sign-ins, and attachments,
encrypted with AES-256-GCM using your backup passphrase. **Write the passphrase down somewhere safe, away from this
computer**: without it a backup can't be opened. Each copy is opened again straight after it's written and the
database inside checked; the last 14 are kept. If a night fails (the USB stick isn't plugged in) you get one
notification. **Restore from a backup** checks the file and puts it in place at the next start; what was there
before is kept in a `before-restore-...` folder.

### Kids' chores on the kitchen tablet

`/app/kids.html` (or **Chores** on the kitchen display): a grown-up types the household key once, then each child
taps their name and the chores they've done. They can undo their own tap for 10 minutes. The tablet can't mark money
as paid or change anything else; ticks from the tablet are marked "(on the tablet)" on the Home screen.

On the same tablet (v0.18) the **Reading**, **Homework** and **Stars** tabs let a child log reading ("15 minutes"),
tick homework that a grown-up added (Home → Reading, homework and stars) and see the week's star chart: a star per
chore, for 10 minutes' reading in a day, and per homework (two if it's done early). Stars are never money. The
tablet still can't add or remove homework, pay out, or change anything else.

### Parcels (Today and the kitchen display)

Dispatch, "out for delivery", "delivered" and "ready to collect" emails (Amazon, Royal Mail, Evri, DPD, Yodel,
Parcelforce, UPS, DHL, InPost and the shops' own) are read like other emails: each once, labelled, treated as data.
Today shows what's coming today, what's waiting to be collected and what's due in the next few days, with
**Track** (the email's own https link), **Got it** and **Hide**. At 8:45 and 12:45, if a parcel's due and Home
Assistant says nobody's home (or, without Home Assistant, both calendars are full in the day), you get one
notification with the tracking link to pick a safe place. Life OS never changes a delivery itself.
`mail: deliveries: false` turns it off.

### Bin days (Home)

Install **Waste Collection Schedule** from HACS in Home Assistant and choose your council: Life OS finds its
calendar by itself (or choose it in Home → Bin days → Where the dates come from). Without Home Assistant, type
each bin, its day and whether it's every week or every other week. The night before a collection (19:30 by
default) there's a notification and, outside quiet hours, an announcement on the speaker. The kitchen display and
"which bin goes out" on the speakers use the same dates.

### Cleaning rota (Home)

Weekly jobs for the two grown-ups (bathroom, hoovering, kitchen, beds, towels...) with points for effort. Each week
the jobs that are due are shared so the points even up over four weeks; tick a job when it's done, **Swap** to
give it to the other person, and **Put it in our to-do lists** to file each person's jobs into their own Google
Tasks (so Gemini knows). Sunday evening says who's got what. Choose a **robot vacuum** (a Home Assistant vacuum)
and its days: it starts once the house is empty (everyone's phone away), or at the latest time you set if you
allow it with someone in. Never in quiet hours. Starting it is the only thing it can do.

### Date night (Home)

Evenings in the next four weeks when both of your calendars and the family calendar are clear (busy/free only),
a ready message for the babysitter (contacts saved as babysitter, nanny or grandparent) to copy into a text or put
in your Gmail drafts, ideas near `household.area` (real places from the web with Claude; map searches
otherwise), and **Put it in the calendar**.

### Babysitter sheet (Home)

Fill it in once: address, both phones, other people to call, each child's bedtime and notes, house rules, Wi-Fi
(the password only if you type it). **Open the sheet to print** gives one clean page (`/app/sitter.html`) with the
UK emergency numbers at the top and the home manual's non-private entries. Nothing on it is sent anywhere.

### Car running costs (To-do → Renewals → Cars → Running costs)

Add fuel, charging, servicing, repairs, tyres, parking, insurance and tax with the mileage when you know it: the
last 12 months by kind, per month, pence per mile and per litre, and miles a year. Servicing is every 10,000 miles
or 12 months unless you change it; "Book a service" goes on the to-do list a month before. MOT advisories can be
typed from the certificate, or come in by themselves with the free DVSA MOT history API (apply at
documentation.history.mot.api.gov.uk; put the client ID, client secret, API key and token URL in Settings → Keys
and addresses). Each advisory goes on the to-do list with one tap.

### Christmas (Home, from October)

A budget for the whole of Christmas and for each person, ideas from **Gift ideas** (likes, sizes and wish list) with
a link to look for each, what's been bought and what it cost, and **Mark wrapped**. The cards list ticks off sent and
received. Key dates: Royal Mail's last posting days (2026: 2nd Class 18 December, Tracked 48 19 December, 1st Class
21 December, Special Delivery 23 December), your own ("Order the turkey"), and school events found in the calendar
(nativity, carol concert, Christmas jumper day). **Put the dates in our to-dos** files each once (one "post the
cards" job, two days before the next posting date). Today and the kitchen display count down. Nothing is ordered.

### Signing phones in by QR code

On the Life OS computer, click the menu bar icon → **Show household key**. Under the key there's a code for each
grown-up; point that person's phone camera at it and it opens Life OS signed in as them. Each code works once, for 10
minutes, and the page only opens on that computer.

### Phone access, set up by Life OS (Settings → Updates and this computer)

**Set up phone access** runs `tailscale serve --bg http://127.0.0.1:8800` on the computer and saves the https address
it gives as the address for phones, then restarts. If Tailscale needs HTTPS switched on for your network first, it
shows Tailscale's own link to do that. Only on the Life OS computer itself; install Tailscale and sign in first.

### Meter readings (Home)

Electricity and gas to start with (add water if you're metered). Pick the Home Assistant sensor that counts each
meter (an energy, gas or water total), or type readings in. On the day you choose (the 1st by default) at 9am, Life
OS reads the sensors and sends one notification with what it read and what still needs typing. A reading lower than
the last one is refused. **Put an email to the supplier in Gmail drafts** writes the reading out for you to send.

### School kit (Home)

For each child: PE days, packed-lunch days, and anything else on a fixed day (swimming bag, recorder, library book).
At 6:30pm Sunday to Thursday one notification says what's needed tomorrow and whose PE kit to wash; Today, the
kitchen display, the weekly sheet and "what kit tomorrow" on the speakers say the same. School closures (from
emails, pasted term dates or settings) are skipped. **Lost something?** adds "Check lost property" for the next
school day.

### Garden (Home)

Tick what the garden has (lawn, hedge, pots, veg, greenhouse, fruit trees). The forecast comes from Home
Assistant's weather, or from Open-Meteo for your postcode (free, no key). At 7:30am: a frost warning when tonight
goes to 1°C or below (September to May), and a watering nudge when three dry, warm days are coming (April to
September), each at most every three days. The month's jobs, with **Add to to-dos**.

### What can we make? (To-do → Dinners)

Take a photo of the open fridge (and cupboard), or type what's in it. A photo goes to the active assistant once,
shrunk on the phone, when you tap **Find dinners**; Life OS doesn't keep it. You get up to three dinners (ones you've
rated well first), what each still needs, **Have it tonight** / **Tomorrow** (into the meal plan) and **Add to
Shopping** (skipping cupboard staples and what's already on the list). A typed list needs no assistant: it's matched
against dinners you've had before.

### This week on the fridge (Home)

**Print this week's sheet** opens one landscape A4 page (`/app/week.html`): each day's calendar, dinner, school kit,
"put the bins out tonight" and birthdays, plus the cleaning rota and the kids' chores. From Saturday it shows the
week ahead, and on Sunday at 6:45pm there's a notification with the link.

### Home contents list (Home)

Valuables room by room: what it is, what it would cost to replace, when it was bought, serial number, and photos or
receipts attached. Totals by room and overall against the contents cover you type in, with a warning when the list
is worth more than the cover and for single items over £1,500 (often needed on the policy by name). **Download the
list as a spreadsheet** is what an insurer asks for after a break-in, fire or flood. Kept on the Life OS computer
only.

### Documents (Home)

Passports, birth and marriage certificates, the will, deeds, policies, the V5C... whose each one is, where it's kept
and its expiry date, with a photo or PDF if you like (kept on the computer only). An expiry date puts it on
Renewals with a reminder well ahead (passports 9 months). Life OS refuses document numbers. "Where's Leo's
passport?" works in the search box and on the speakers. None of it goes to the assistant or Google.

### Brain dump and Snap a letter (Today, or "Lots to add, or a letter?" in Add)

Type or paste everything on your mind; Life OS splits it into to-dos, shopping and calendar dates (with the
assistant in one go, or by rules without it) and shows them with ticks, a kind and a date to change. Nothing is saved
until **Add these**. **Snap a letter** reads a photo of a school letter, appointment card or bill the same way:
dates, jobs, and any amount to pay as a "Pay £x to ..." to-do (never a payment). The photo goes to the active
assistant once, shrunk on the phone, and isn't kept. Not for work or patient letters.

### Email to to-do (Gmail)

Give any email the label **Life OS** in Gmail (phone or computer). Every 10 minutes, 6am to 10pm, Life OS turns each
one into a to-do with the sender and a link back to the email, and changes the label to **Life OS/Added**. The
email's words are treated as data, never instructions. Needs Gmail ticked when the person connected.

### The evening "Tomorrow" message (Settings → Notifications)

One notification at 7pm (you choose 4pm to 10pm): bins to put out, school kit and PE washing, each grown-up's first
appointment, what to wear for the weather (coats, umbrellas, sun cream), dinner, parcels due and birthdays. It can be
said on the kitchen speaker too (never in quiet hours). While it's on, the separate bins and school kit evening
notifications stay quiet. "What's on tomorrow?" on the speakers says the same.

### Freezer (Home)

What's in, how many portions, which drawer, since when. **Used one** takes a portion off. "What can we make?" counts
the freezer list (tick box), and on the 1st of the month there's a nudge for anything in three months or more.
"What's in the freezer?" works on the speakers.

### Loft and garage boxes (Home)

Number and name each box, say where it lives and list what's in it. **Print box labels** makes a sheet of labels with
a QR code; scanning one with a phone signed in to Life OS opens that box's list (add things, mark things taken out).
"Where are the Christmas lights?" works in the search box and on the speakers. Kept on the Life OS computer only.

### Guests (Home)

Save the Wi-Fi (network, password, WPA/WEP/none) and house notes; **Print the guest sheet** has the Wi-Fi as a code to
scan. Add a visit for a room-ready checklist (bed, towels, drawer, bathroom, key, sheet), and **Put it in the to-dos
and calendar** files what's left for the day before and a "Staying: name" calendar entry. The kitchen display shows
the Wi-Fi code only during a visit or for the days you switch it on. The password never goes to the assistant or
Google.

### Something's broken (Home)

Type what's broken: Life OS looks in warranties (from receipts), purchases and the contents list, and says where you
stand under the Consumer Rights Act 2015 by when it was bought: within 30 days a full refund; up to six months one
repair or replacement with the fault taken to be there from the start; after that a repair or replacement if it
hasn't lasted as it reasonably should (you may need to show the fault); up to six years to claim (five in
Scotland), against the shop, not the maker. **Write to the shop** puts the claim email in Gmail drafts and adds a
chase-up to-do in a week; **Get it repaired** files a repair job (the tradespeople flow takes over). General
information, not legal advice.

### Kids' routines (kids' tablet → Routine; set up on Home)

Picture checklists for the morning (teeth, dressed, breakfast, shoes, bag) and bedtime, changeable per child, with a
"leave in 12 minutes" countdown in the morning. Finishing a routine earns a star on the star chart. The tablet can
tick steps; changing them stays with grown-ups.

### Check the Google set-up (Settings → Google accounts)

Checks the sign-in file (Web application, with a secret), the address, each connected account's Google Tasks,
Calendar and Gmail (finding a switched-off service), and lists the steps Google won't let it check (the redirect
address, test users, publishing), each with a direct link built from your project number.

### Live test (Settings → Check everything)

Runs every Gmail, Tasks and Calendar feature against each connected account: a test to-do in its own list
(added, read back, notes changed, ticked, deleted), a test calendar entry at 3am in 30 days (added, deleted), a
search, a Gmail draft with no recipient (made, checked, deleted). Nothing is sent. Each step is timed and anything
that fails is explained in plain English, with **Copy the report**.

### Ask for a better price

On any bill (To-do → Money → Bills) and on insurance, broadband, mobile and energy renewals: the old and new price
and what it adds up to over a year, a short script for the phone call (ask for retentions), an email that goes into
your Gmail drafts (Life OS doesn't send it), and where to compare. **Add a to-do to ring them** puts the script in
the to-do's notes. Water, council tax and the TV licence get a note on what can be done instead.

### Away mode for trips

A switch on each trip. While you're away: the heating goes down to frost protection (`away.frost_temp`, 12°C), the
lights in `away.lights` come on at dusk and go off at 22:30, and a **House check** notification each evening says
whether anything's open or unlocked and the temperature inside. On the day you come home the heating goes back up
at 2pm. Nothing is ever unlocked, opened or disarmed.

### Recipe links (To-do → Dinners)

Paste a recipe link and pick the day: the dinner is saved with its link, and the ingredients that aren't cupboard
staples (`pantry_staples`) go on Shopping. Works with most recipe sites (they publish the recipe in a standard form).
Life OS only reads pages on the public internet, never addresses on your home network.

### Seasonal house jobs (Home)

Each month's jobs for a UK house: test the alarms, clear the gutters, bleed the radiators, book the boiler service
and so on, only for things the house has (tick them under **What the house has**). On the 1st they go on the to-do
list; **Not for us** stops one being suggested.

### The Sunday agenda (To-do → Week)

One page for the two of you: decisions to make (things waiting for an OK, replies to deal with, subscriptions to
keep or cancel, bills that went up, the admin hour, party replies), the week ahead, school runs or childcare with
nobody free, money going out, overdue and stuck to-dos, the kids, and what's coming later. **Print** or **Copy as
text**. A notification says it's ready at 6:50pm on Sundays.

### Birthday parties (To-do → Projects)

Whose, when, where, the theme. Life OS adds the jobs before it (book the venue, invites, cake, party bags, food,
thank-you cards), a party-day checklist in Google Tasks and the calendar entry. Add guests with a parent's email;
**Make the invites** puts one invite per guest in your Gmail drafts to send, and there's the same text to paste into
WhatsApp for the rest. Replies to the emails are read every 30 minutes and ticked off as coming or not; tap
**Coming / Maybe / Can't come** for the others. **Food to Shopping** adds party food for the number coming.

### Dinners that learn

After dinner, Today asks **How was it? Loved it / Not again**. **Suggest dinners for the gaps** fills empty
evenings with dinners you loved and haven't had for a while, quick ones on busy evenings, and ones that use up
what the week's other dinners need (the other half of the peppers). Disliked dinners aren't suggested again. No
AI needed.

### People and presents (Home)

Likes, sizes, wish-list links and what each person has been given. Their birthdays are predicted (as well as
those in the calendar), "Sort a present" starts from their wish list and likes, and anything given in the last
three years isn't suggested again. Ordering a present records it. Answering "What's Mum into?" fills this in.

### Home manual (Home)

Stopcock, fuse box, boiler, gas meter, Wi-Fi, bin days: where things are and how they work. Ask the assistant
("where's the stopcock?"), see it on the kitchen display, or ask Gemini: it's copied to a Google Tasks list called
**Home manual**. Anything private (passwords, codes) stays in Life OS only.

### Who's home for a visit

Times offered to a tradesperson are ones when at least one adult is free, and the app says who ("Monday 9am to
11am (Sue in)"). The booking form checks who'll be in as you change the time, the calendar entry says who, and
if that person later books something over it, Life OS tells you (and who else is free).

### The Sunday look ahead

To-do → **Week**: what got done and bought this week, who you're waiting on, the week's calendar, what's
predicted for the next seven days and a **Plan my week** button.

### Leaving home check

Home → **Check before I go**: anything open, on or unlocked, each with a button to sort it (**Lock it**, **Turn
off**, **Down to 16°C**). Doors and windows can only be closed by hand, so those are just shown. It never unlocks
or opens anything. Plugs to switch off (`leaving.switch_off`), devices to leave alone (`leaving.ignore`) and the
eco temperature (`leaving.eco_temp`) are in `config/settings.yaml`.

### Notifications, with Approve / Reject on the notification

Settings → Notifications → **Turn on notifications** (each phone). The morning brief, renewal reminders and
approval requests arrive as normal Android notifications from Life OS. Approval requests show **Approve** and
**Reject** buttons on the notification itself. No extra app needed; ntfy is still supported as an optional
second channel.

### Smart to-dos

To-do → type or say what needs doing, the way you'd say it out loud: "the bath tap is dripping again",
"Sue's passport runs out in March", "clear the gutters every autumn". The AI judges what you didn't say:

| It works out | Example |
| --- | --- |
| Category (= a Google Tasks list) | Home maintenance, DIY and projects, Garden, Car, Admin and renewals, Health and appointments, Kids, Errands, Family and social, or Shopping for groceries |
| Importance | Urgent for safety, damage or a deadline within days (gas, leaks, smoke alarms); high, normal or low otherwise |
| Timescale and due date | A real date when one is implied ("before the school trip on Friday"), otherwise today / this week / this month / later / someday |
| Effort | Realistic minutes; jobs of 15 minutes or less are flagged **Quick** |
| Next action | The very first physical step ("Check the washer size and buy two") |
| Repeats | Weekly, monthly, yearly...; ticking it off brings it back on its next date |
| Who | One of you, a child, or anyone |

The To-do list is grouped **Now / This week / This month / Later / Someday** and sorted by date, importance and
effort, with filters for **For me**, **Quick jobs**, **Urgent and high** and each category. Everything is stored
in your Google Tasks (one list per category), so it also shows in the Google Tasks app; Life OS keeps the extra
details alongside. Anything you type into a Google Tasks list called **Inbox**, on either phone, is sorted
automatically within 15 minutes. If the AI is unavailable, simple built-in rules sort it instead. The categories
and their descriptions are in `config/settings.yaml` (`todo_categories`).

### "Sort it for me": to-dos that solve themselves

For anything that needs buying, booking or a gift, Life OS works out the answer and you just approve it:

- **Buy**: up to three real products with an **Add to Amazon basket** button (Amazon opens with it in your basket;
  you press Buy there, because Amazon doesn't let any app place orders, and nothing spends money without you).
  With Claude, it searches the web for current UK products (about 1p per search). With Gemini, the buttons open
  an Amazon search for the exact product. Tap **I've ordered it** afterwards: the to-do is ticked off and the
  purchase is remembered ("same as last time" next time).
- **Book**: who to book and how, with links. **Do**: short steps and any parts needed.
- **Question**: if something essential is missing ("What fitting is the landing light?", "What's Mum into?"),
  it asks once with tap-to-answer choices, saves the answer to **Home details**, and never asks again.

Gifts, errands, appointments and anything with "buy", "replace", "bulb" or "book" in it are sorted the moment you
add them; everything else has a **Sort it for me** button. Not right? Type a hint ("under £30", "she has one")
and **Try again**.

### Getting a tradesperson or an appointment, start to finish

Add "kitchen socket has stopped working", "boiler's banging", "book the MOT" or "Ava needs a dentist check-up" and
Life OS works out who's needed (electrician, plumber, gas engineer, roofer, locksmith, garage, dentist, vet...)
and lines it up:

1. **Who to ask.** Your saved go-to for that trade comes first (Home → Trades and contacts). With Claude it also
   searches for up to three well-reviewed local ones near your area (`household.area` in `config/settings.yaml`,
   for example "Leeds LS6"), with phone, email and website. Without an area it asks you once.
2. **Times you're free.** Three slots on different days that are clear in everyone's Google Calendar, inside your
   usual hours (`availability`), at least `trade_notice_days` ahead. Holidays marked "Away" are skipped.
3. **A ready email.** "Hello Dave, ... would any of these times suit?" with the times listed. Edit anything, then
   tap **Send email to Spark Electrical** (and confirm). It goes from your own Gmail. Phone-only firms get
   **Call** and **Text** buttons with the message pre-written.
4. **Watching for the reply.** Every 30 minutes Life OS checks the email thread. A reply (from them or a colleague
   at the same firm, not an out-of-office) sends you a notification and shows on Today under **Waiting on**.
   No reply after `chase_after_days` (3): a nudge, and **Send a reminder** shows the reminder text for you to
   send or edit. **Ask someone else** starts again with the next option.
5. **Booking it.** The date and time they agreed to are filled in for you. **Add to calendar** puts the visit in the
   family calendar (with their phone number) and moves the to-do to that day. Tick **Also email Dave to confirm**
   and the short confirmation shown is sent in the same thread.
6. **Afterwards.** A couple of hours after the visit: "Did Spark Electrical sort it?" **Yes, it's done** ticks it
   off; **Make Dave our go-to** saves them for next time; **No, ask again** starts over.

**Quotes from several firms.** Tick **Also ask for a quote from** under the email and **Ask 3 firms for quotes**
sends the same email to each (greeted by name, each from your Gmail). The job shows **Quotes from 3 firms** side
by side: who replied, what they said, who's gone quiet. Booking one cancels the others' reminders.

Anyone new you email is saved under Trades and contacts automatically. Nothing is ever sent without you tapping
Send on the exact text, and the assistant's own tool for emailing a tradesperson always waits for approval.

**Find time** on a job you'll do yourself (bleed the radiators, paint the shed) offers three free evening or
weekend slots and puts the one you tap in your calendar.

### Using it from Gemini, without opening Life OS

Gemini can't connect to Life OS directly in the UK yet (Google's custom app connections are US-only for now).
It does work with Google Tasks, Gmail and Calendar, so Life OS keeps those up to date and acts on what Gemini
does there. Every 5 minutes it checks for new to-dos, emails sent from your drafts and ticks.

| Say to Gemini (or "Hey Google") | What happens |
|---|---|
| "Add *the kitchen socket has stopped working* to my to-dos" | Within 5 minutes Life OS sorts it into Home maintenance, works out it needs an electrician, and puts the email (with times you're free) in your **Gmail drafts**. You get a notification. |
| "Add *milk* to my to-dos" | It goes on the Shopping list. |
| "Remind me on Friday at 6 to call Mum" | Left alone: reminders with a date stay where Gemini put them. |
| Open Gmail, Drafts, tap **Send** | Life OS notices it went and starts watching for the reply. |
| "What's on my Waiting on list?" | Gemini reads the current state, like "Spark Electrical replied. Tick to book Thursday 26 November, 8am to 10am". |
| "Mark *Spark Electrical replied* as done" | That time goes in the family calendar. Nothing is sent. |
| "When's the electrician coming?" | It's in Google Calendar. |
| "Mark *change the water filter* as done" | Same as ticking it in Life OS: a repeating job comes back with its next date. |
| "Mark *Did Spark Electrical sort it?* as done" | The job is ticked off. |

Notes:

- Only to-dos added **after** you turn this on are picked up; what's already in your main Google Tasks list stays
  yours.
- Both adults' Gemini can add jobs. They go into the household lists, which live in one Google account
  (`household.tasks_owner`), so on the other adult's phone Gemini can add jobs but can't read those lists out.
  Tradesperson emails, and their Waiting on copies, are in the Gmail and Google Tasks of whoever sends them.
- No-reply chasers wait in Gmail drafts too, in the same thread.
- Each to-do's notes in Google Tasks carry a **--- Life OS ---** section with the next step, the suggestion,
  Amazon basket links and who to call, kept up to date. Your own notes above it are left alone, so the Google
  Tasks app works as a second view of Life OS.
- **Settings → Gemini and Google apps** shows when Life OS last checked, what it did recently and any problem.
  **Check now** is there for when you've just told Gemini something and don't want to wait 5 minutes.
- Sunday at 6:10pm you get one notification about up to three jobs that have sat for 4 weeks
  (`stalled_after_days`) with nothing booked or under way, each with the quickest way to close it.
- Settings are under `gemini:` and `draft_trade_emails` in `config/settings.yaml`.
- Gemini's wording varies. If it doesn't find a list by name, say "in Google Tasks" or start with "@Google Tasks".

**The house by voice.** Gemini controls devices through Google Home. Link Home Assistant to Google Home, either
with Home Assistant Cloud (a paid subscription, easiest) or the free manual "Google Assistant" integration. Then
choose what to expose: lights, plugs, heating, the vacuum and the Life OS scenes (`scene.away`, `scene.home`,
`scene.goodnight`), so "Hey Google, activate Goodnight" works. Keep locks, the alarm and any gate or garage
opener out of Google Home, or protect them with Google's PIN, in line with Life OS's own approval rule.

If Google brings custom app connections to the UK, Life OS could plug into Gemini directly. That would need it to
be reachable from the internet, so it's not built yet.

### Home details

Home → Home details is the house's memory: bulb fittings per room, appliance models, the boiler, the car's tyre
size, who likes what. Life OS reads it before suggesting anything, and fills it in from your answers and orders.
You can add or correct anything there, or just tell the assistant ("the kitchen spotlights are GU10").

### Projects

To-do → Projects: name it ("Redecorate the hallway") and optionally say what's involved. The AI breaks it into
5 to 12 ordered steps in its own Google Tasks list ("Project: Redecorate the hallway"). Each card shows progress
and the next step, which also appears in your To-do list, so a project never stalls. When a to-do sounds like a
bigger job, Life OS offers to turn it into a project.

### Dinners

Lists → This week's dinners: type a dinner for any day (your favourite meals are suggested as you type), or tap
**Plan the week for me** and the assistant fills the gaps around busy evenings and puts the ingredients on the
Shopping list, skipping pantry staples and anything already on it. Typed the dinners yourself? **Add this week's
ingredients to Shopping** does the same; each dinner's ingredients are remembered, so it only asks the AI about
new ones. Tonight's dinner shows on Today and on the
kitchen display. Favourites and pantry staples are in `config/settings.yaml`; the Sunday weekly review plans the
next week automatically.

### Kitchen wall display

Any spare tablet (an old Android tablet or a cheap Fire tablet with Chrome is fine): open
`<your Life OS address>/app/wall.html`, type the household key once, and leave it on the wall. It shows the clock,
today and tomorrow from everyone's calendars, tonight's dinner, each child's chores and pocket money, renewals
coming up, the morning brief, and how many things are waiting for an OK. It refreshes every minute, keeps the
screen on, and **can't change anything**, so it's safe in a shared space. For the weather, set
`wall: {weather_entity: weather.home}` in `config/settings.yaml` (any Home Assistant weather entity).

### Quiet hours

Between 21:30 and 07:00 (change `notifications.quiet_hours`), ordinary notifications wait and arrive as one
"Overnight" summary in the morning. Approval requests and urgent alerts (a water leak, say) always come through.

### Renewals

Lists → Renewals: add car insurance, MOT, home insurance, TV licence, broadband, passports, boiler service.
Six weeks before each date Life OS adds a "Renew ..." task (due two weeks before) and sends a notification.
Yearly renewals move to next year's date automatically once passed. You can also just tell the assistant:
"my car insurance renews on 10 January".

### Kids' chores and pocket money

List the children in `household.kids` and the chores (with a value in pounds) under `chores` in
`config/settings.yaml`. On the Home screen each child has a card: tap a chore when it's done, undo a mistake,
see what is owed, and tap **Mark as paid** when you hand it over. An NFC tag can log a chore for a child too
(`log_chore: {chore: Make bed, who: Ava}`). The weekly review uses the chore log for next week's split.

**Pocket money and saving goals.** A weekly amount per child (in the app, or `pocket_money.allowance`) is added
every Saturday morning. Each child can save for something ("Lego treehouse, £25"): **Put £X towards it** moves
money from what they're owed into the goal, with a progress bar on their card and on the kitchen display. Removing
an unfinished goal gives the savings back.

Accessibility is built in, not bolted on:
- Checked with axe-core against WCAG 2.2 AA on every screen (no violations), on a phone-sized screen in light and dark mode.
- Every button is at least 48 px tall; important ones are larger. Text is 18 px by default, with Large and Extra large options.
- High-contrast mode, automatic dark mode, and reduced motion when the phone asks for it.
- Works with TalkBack (Android's screen reader): proper headings, labels, live announcements for replies and
  confirmations, focus moves to each new screen's heading, and a skip link.
- Voice input (Chrome's speech recognition, UK English) and optional spoken replies.
- Plain language throughout; errors say what to do next.

### Installing on Android

It is a Progressive Web App: installed from Chrome, it gets its own icon, opens full screen like any app,
and appears in the app drawer. No Play Store needed.

1. On the phone, open Chrome and go to your Life OS address (for example `https://lifeos-box.your-tailnet.ts.net`).
2. Sign in: tap your name, type the household key.
3. Tap **Install Life OS** on the Settings screen, or Chrome menu (three dots) → **Install app**.
4. Long-press the icon for shortcuts straight to Ask, Lists or Home.

The app needs an **https** address (Android only installs apps from secure sites, and Google sign-in requires it).
Tailscale gives you one for free; see Setup, step 2.

> Want a real `.apk` later, for example for a Play Store listing? Tools such as Bubblewrap or PWABuilder can
> wrap this same app as a Trusted Web Activity. They need the site reachable from the internet, so this is
> optional and not needed for your own phones.

### The Google sign-in file

In Google Cloud (Google Auth Platform → Clients) create a client of type **Web application**, with
`https://<your Tailscale address>/auth/google/callback` under **Authorised redirect URIs** (not JavaScript origins).
Then in Life OS, **Settings → Google accounts → Add the Google sign-in file**: choose the JSON file Google gives you,
or paste the client ID and secret. Add both of your Gmail addresses under **Audience → Test users** before
connecting, and once you're both connected click **Publish app** there (otherwise Google signs you out every 7 days).

### Each person connects their own Google account

On **your wife's phone**, signed in to the app as her: **Settings → Google accounts → Connect**. Google shows
its own sign-in screen, she chooses her account and approves. You never see her password.

- She chooses whether Life OS may use her **email** (tick box). Calendar and tasks are always included.
- Both calendars are combined on Today; an event on both calendars shows once, with both names.
- Shared lists (Shopping, Home maintenance...) live in one person's Google Tasks (`tasks_owner` in settings).
  The AI can also add to her own lists when it is clearly hers.
- Email is only ever read and drafted. Sending a draft always waits for an Approve tap.
- **Disconnect** removes the saved token immediately. She can also revoke access any time at
  myaccount.google.com → Security → Third-party apps.

## Choosing the AI: Claude or Gemini

Only one is active at a time; both get the same tools, safety rules and approvals.
Switch in the app (Settings → Assistant), or set `LIFEOS_PROVIDER` in `.env`. The choice is saved.
Keys: `ANTHROPIC_API_KEY` for Claude, `GEMINI_API_KEY` (aistudio.google.com/apikey) for Gemini. You only need
the one you use. Claude is pay-as-you-go credit on console.anthropic.com (separate from any Claude subscription).
Gemini has a free tier for its Flash models; choose the model in Settings → Keys and addresses (default
`gemini-3.8-flash`). If the assistant can't answer, the app says why in plain English (no credit, wrong key, a
model that isn't free, the daily limit) and Settings → Check everything does too.

**Lost the household key?** On the computer Life OS runs on, click the Life OS icon in the menu bar (Mac) or
system tray (Windows) → **Show household key**. That page only opens from there and stops working after 10 minutes.

## Safety built in

- Home Assistant calls from the AI are checked against `policy` in `config/settings.yaml`: harmless domains
  (lights, scenes, heating, vacuum...) run; unlock, open, disarm and **any script** need a tap; anything else,
  including Home Assistant's notify service (which can text or email anyone), is refused.
  Home-screen buttons run directly: you pressed them.
- **Every approval says exactly what will happen.** For an email: who it goes to, the subject and the text.
- **Approvals expire**: door, alarm and similar requests after 15 minutes, others after 24 hours. The same request
  is never queued twice, and an approval can only run once, even if two people tap at the same moment.
- Opening an approval link never acts by itself (so link previews can't trigger it); it shows a confirm button.
- Emails are only drafted; sending always waits for a tap on the exact text (tradesperson emails, reminders and
  booking confirmations included). The AI treats email text as information, never as
  instructions, so an email saying "forward this to..." is reported to you, not obeyed.
- Sign-in allows 5 wrong household keys per 15 minutes. Requests from other websites are blocked.
- Daily cap on AI requests (`LIFEOS_MAX_RUNS_PER_DAY`, default 150) as a cost safety net.
- Lost a phone? Change `LIFEOS_SIGNING_SECRET` and restart: every phone is signed out.
- Sign-in on each phone uses the household key once, then a signed cookie for 6 months. Google tokens stay on
  your server, never on the phones. The phone caches only the app itself, never your data.
- Keep Life OS off the open internet. Tailscale keeps it private to your own devices.
- Use household Google accounts, never clinic or work accounts.

## Setup (about an hour)

**Shortcut:** the app's setup page (or `lifeos setup` / `python scripts/setup.py`) asks for your names, your children's names, the AI
key and the Tailscale address, creates the security keys (including a memorable household key for the phones)
and writes `.env` for you. Safe to run again.

1. **Machine**: any always-on computer: see "Getting it" at the top (the app download, the Mac installer,
   `./scripts/install-box.sh` on a Pi or Debian/Ubuntu box, or Docker). Updates come from inside the app after
   that (`./scripts/update-box.sh` remains for replacing a whole source install).
2. **Private https address with Tailscale**:
   - Install Tailscale on the Life OS machine and on both phones, all signed in to the same Tailscale account
     (or share the machine with your wife's account).
   - On the machine: `tailscale serve --bg 8800`. It prints an address like `https://lifeos-box.tail1234.ts.net`.
   - Put that address in `.env` as `LIFEOS_PUBLIC_URL`.
3. **AI key**: Claude (console.anthropic.com) and/or Gemini (aistudio.google.com/apikey). Set a spend limit.
4. **Google sign-in** (once, about 10 minutes) in Google Cloud Console:
   - Create a project. Enable **Google Tasks API**, **Google Calendar API** and **Gmail API**.
   - OAuth consent screen: External, app name "Life OS", add **both your Google accounts as test users**.
   - Credentials → Create OAuth client ID → **Web application**. Authorised redirect URI:
     `<LIFEOS_PUBLIC_URL>/auth/google/callback` (exactly, e.g. `https://lifeos-box.tail1234.ts.net/auth/google/callback`).
   - Download the JSON and save it as `secrets/client_secret.json`.
   - While the app is in "Testing" mode Google may ask each person to sign in again every 7 days. To avoid that,
     publish the app to "In production" (for personal use you can skip Google's verification; you will see an
     "unverified app" warning once when connecting, which is expected for your own app).
5. **Notifications**: nothing to install. Each person turns them on in the app (Settings → Notifications).
   Optional: the ntfy app as an extra channel (set `NTFY_TOPIC`).
6. **Home Assistant token**: Profile → Security → Long-lived access tokens.
7. **Configure**: `cp .env.example .env` and fill it in. In `config/settings.yaml` set your real first names under
   `household.names`, the kids, NFC tags, Home buttons and routines.
8. **Run**:
   ```
   docker compose up -d --build
   curl localhost:8800/health
   ```
   `/health` lists anything still missing from the setup in plain English; the app shows the same list
   under Settings until it is fixed.
9. **Phones**: install the app (above), sign in, and each person connects their Google account.
10. **Home Assistant side**: copy `homeassistant/lifeos_package.yaml` into `/config/packages/`, set the Life OS
    address, add `lifeos_key` to `secrets.yaml`, restart HA. Write NFC stickers from the HA phone app
    (Settings → Tags) using the tag IDs in `settings.yaml`.

**Trying it with your real Google accounts on one computer first** (no Tailscale yet): set
`LIFEOS_PUBLIC_URL=http://localhost:8800`, add `http://localhost:8800/auth/google/callback` as a second authorised
redirect URI on the same OAuth client, run `python -m lifeos.app` and open http://localhost:8800/app/. Google
allows plain http for localhost only. Both of you can connect from that computer (sign in to the app as each
person in turn). The Google connections carry over when you later switch to the Tailscale address; just add that
redirect URI too.

Fallback if in-app Google sign-in is not possible: `python scripts/google_auth.py wife --no-gmail` on a laptop
(needs a "Desktop app" OAuth client saved as `secrets/desktop_client_secret.json`).

## Backups

Every night at 03:17 Life OS copies its database to `data/backups/` and keeps the last 14 days. Google Tasks,
Calendar and Gmail stay in Google. Also keep a copy of `.env` and the `secrets/` folder somewhere safe (a
password manager is ideal); they hold your keys.

## How the inbox routine avoids repeats

The inbox check runs three times a day. Each email it has dealt with gets the Gmail label **Life OS/Handled**
(it stays unread for you), and later checks skip labelled emails. Tasks are never added twice to the same list.

## The doctor: check every connection

```
python scripts/doctor.py          # or --no-ai to skip the one tiny AI request
```
It tests the settings, Home Assistant (and warns about any device name in `settings.yaml` that doesn't exist),
each person's Google account, the Google sign-in client and redirect address, the AI key, notifications and
whether the phones can reach Life OS, then says in plain English what to fix. Run it after setup and whenever
something stops working. Inside Docker: `docker compose exec lifeos python scripts/doctor.py`.

## Other entry points

| Entry point | What happens |
| --- | --- |
| `POST /ask` `{"text": "..."}` | The AI does what you asked and replies (header `X-LifeOS-Key`) |
| `POST /nfc/{tag_id}` | Instant actions from `settings.yaml`, then an optional AI prompt |
| Schedules | Morning brief (weekdays 06:30), inbox triage (08:00, 13:00, 18:00), evening reset (20:30), weekly review (Sun 18:00) |
| `POST /loops/{name}/run` | Run a routine now |
| `GET` / `POST /provider` | See or switch the AI |
| `GET /health`, `GET /events` | Status and audit log |

## Extending it

Add a tool in `lifeos/tools.py` (handler, JSON schema, policy). Ideas: supermarket basket builder,
bank-statement subscription scan, school-email parser, energy tariff scheduling, pocket-money ledger.

## Tests

```
pip install -r requirements.txt
python -m pytest -q
```
The tests use stand-ins for Claude, Gemini, Google, Home Assistant and notifications, so they need no keys.
`tests/test_e2e_browser.py` also drives the real app in Chrome (sign in, approve, ask, lists, dinners,
renewals, chores, settings, predictions, ordering online, sharing in, adding offline, the leaving check,
money, childcare, dinner suggestions, the home manual, cheapest time, gift memory, and the first-run setup page),
checks Chrome considers it installable and that it opens offline, and checks the
kitchen display can't do anything but display. It is skipped if Playwright isn't installed
(`pip install playwright && python -m playwright install chromium`). `tests/test_app_packaging.py` covers the app
itself: the home folder, setup, update files (damaged, too old, escaping their folder), the trial and both kinds of
roll-back, the update feed, and the update builder. The same start, set up, update and roll-back sequence has been
run against the built Linux app.

## Running costs

Claude and Gemini API usage is billed per token. Four routines a day plus a weekly review is modest, but set
a spend limit and check the first week's usage before adding more.
