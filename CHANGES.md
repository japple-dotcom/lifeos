# What's new

## 0.19.0
Christmas: a budget for each person with ideas from Gift ideas, what's bought, what it cost and whether it's
wrapped; the cards list; and the key dates (Royal Mail's last posting days, your own, and nativity, carol
concerts and the like from the calendar), with a countdown on Today and the kitchen display. Phones sign in by
scanning a code: Life OS icon at the top of the Mac's screen → Show household key. Settings → Updates and this
computer → Set up phone access runs Tailscale for you and saves the address. Meter readings with a monthly
reminder, read from Home Assistant where there's a meter sensor, and an email to the supplier in Gmail drafts.
School kit: PE, packed lunch and other kit days, said at 6:30pm the night before (holidays skipped), and "lost
something?". Garden: frost and watering warnings from the forecast, and the month's jobs. What can we make?: take a
photo of the fridge (or type what's in it) for three dinners and what to buy. A weekly A4 sheet for the fridge
door. A home contents list for the insurer, with photos and receipts. Documents: where each one is kept and when it
expires ("Where's Leo's passport?" works on the speakers too).
Fixes: add Google's sign-in file from Settings → Google accounts (choose the downloaded file, or paste the client
ID and secret) instead of copying it into a hidden folder; adding a to-do no longer sits on "Sorting it…" when the
assistant is slow or out of credit (after 20 seconds it's filed by Life OS's own rules); the babysitter sheet printed as a blank page; a phone
coming in through Tailscale is no longer treated as "this computer"; form mistakes now say so in words.

## 0.18.0
Talk to Life OS on a Home Assistant Voice speaker: it's now a Home Assistant conversation agent (download the small
integration from Settings > Voice and speakers) and keeps listening when it asks you a question back. Parcels from
dispatch and "out for delivery" emails on Today and the kitchen display, with a nudge when one's due and nobody's
in. Bin days from the council (Home Assistant's Waste Collection Schedule) or your own rota, with a reminder and
an announcement the night before. A printable babysitter sheet. Date night: evenings you're both free, a message
for the babysitter, ideas nearby. On the kids' tablet: a reading log, homework to tick and a weekly star chart.
A fair cleaning rota for the two of you, with the robot vacuum run while the house is empty. Car running costs:
fuel, cost per mile, servicing by mileage, and MOT advisories (typed, or from DVSA's MOT history API). A live test
of every Gmail, Tasks and Calendar feature against your real accounts that sends nothing.
Fixes: the assistant's errors in plain English ("Your Claude account has no credit left..."); "Show household key"
on the menu bar icon; signing in at http://localhost on the Mac now sticks in Safari; choose the Gemini model in
Settings (gemini-3.8-flash is the new default).

## 0.17.2
Phone notifications now switch on even when the notification key brought across from an older install can't be
read: Life OS keeps the old file beside it and makes a new key, so each phone turns notifications on once more. If
notifications still can't start, the message on the phone and Settings > Check everything now say why.
The Intel Mac app is rebuilt so notifications and encrypted backups work on it: download it again from the
release page and replace the copy in Applications (an update file can't fix this one).

## 0.17.1
Life OS now finds its own updates on GitHub (japple-dotcom/lifeos): Settings > Updates offers each new version, and
the overnight install works without downloading files.

## 0.17.0
Settings > Check everything tests every connection from the app and says what to fix. Nightly encrypted backups to
a USB stick or a synced folder (Google Drive, iCloud Drive, OneDrive), each one opened again and checked, with
restore from Settings. Kids' chores on the kitchen tablet (they tick, grown-ups pay). Ask for a better price when a
bill or renewal goes up: a call script, an email in your Gmail drafts and where to compare. Away mode for trips
(heating down, lights at dusk, an evening house check, heating back up for your return). Add a dinner from a recipe
link. Seasonal house jobs each month. A Sunday agenda for the two of you. A birthday party planner with invites as
Gmail drafts and replies read for you. Voice: an option to send everything a dedicated speaker hears to Life OS.

## 0.16.0
Life OS talks: answers and announcements on your Google speakers through Home Assistant (Google closed voice apps
on its speakers in 2023, so fixed phrases go through Google Home routines), anything at all through Home
Assistant Assist ("ask Life OS to..."), and an Ask button on the kitchen display. Things Gemini adds to your
to-dos that are shopping go straight to Shopping. New: a "Getting set up" checklist, bills and direct debits with
a warning when one goes up, trips with packing lists and an Away entry, photos and PDFs kept with renewals, the
home manual, bills and trips, the school run, weekly pocket money and saving goals, the weekly energy and heating
report, and a weekly life admin hour. Fixed: a rare mix-up when several screens loaded at once, and number boxes
that weren't styled.

## 0.15.0
Life OS is now an app: it opens a setup page the first time, keeps your data apart from the app, and updates
itself from Settings (or an update file) with automatic roll-back. New: subscriptions and monthly spending,
MOT and tax from your car's registration, school holidays and childcare gaps, appliance care from real use,
the cheapest time to run appliances (Octopus), dinners that learn what you like, gift memory, a home manual
Gemini can read, and visits that say who'll be in.

## 0.14.0
Predictions of what you'll need, ordering the shopping online, receipts and school emails, quotes from several
firms, the Sunday look ahead, the leaving home check, sharing into Life OS, and working offline.
