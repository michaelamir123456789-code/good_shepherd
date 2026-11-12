# الراعي الصالح — The Good Shepherd
Sunday-school management: kids, classes, attendance, visitation (افتقاد), reports. Arabic-first (RTL) with English.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then fill SECRET_KEY (see the comment inside)
python init_db.py             # creates tables + the leader account
python app.py                 # http://127.0.0.1:5000
```
Existing database? Skip `init_db.py` — the schema is unchanged, just keep your `instance/sunday_school.db`.

Forgot a password: `python reset_password.py USERNAME_OR_PHONE_OR_EMAIL` (typed hidden, never printed).
Can't sign in and don't know why: `python check_login.py USERNAME_OR_PHONE_OR_EMAIL` — shows which database this folder uses, lists the accounts, and says exactly what is wrong (e-mail not found, wrong password, disabled, locked).

## Production
```bash
FLASK_ENV=production gunicorn -w 3 -b 127.0.0.1:8000 "app:create_app()"
```
* Put it behind **HTTPS** (nginx/Caddy). Secure cookies + HSTS are on in production.
* Set `TRUSTED_PROXY_COUNT=1` when behind one proxy, so audit-log IPs are real.
* With several workers set `RATELIMIT_STORAGE_URI=redis://...` (memory storage is per-worker).
* Back up `instance/sunday_school.db` regularly. Never share `.env` or the `.db` file.
* The app refuses to start in production without a strong `SECRET_KEY`.

## Two levels of admin (and every admin is a servant too)
```
Big admin (leader)          creates the grades, chooses each grade's admins, sees everything
  └─ Admin of a grade       sees ONLY his grade(s) — he can be admin of several grades —
       │                    and can make servants of his own grade admins of that same grade
       └─ Servant           works in his own class(es)
```
Every admin or big admin is a servant as well (he can have a class, take its attendance, appears in the servants list with an "Admin" badge); not every servant is an admin.

| | Big admin | Admin of grade(s) | Servant |
|---|---|---|---|
| Sees | everything | only his grade(s) | only his class(es) |
| Grades | create / rename / delete; **choose each grade's admins** | — (sees his own) | — |
| Admins | make anyone admin of any grades, remove, disable, unlock | make servants **of his grade** admins of **his grade**, remove admins **from his grade** | — |
| Kids | all | add / edit / delete / restore / import in his grades | add / edit / delete / import in his class |
| Servants | all | add, edit, delete, approve account requests (and assign a class) in his grades | — |
| Reports, visitation, PDFs | all | his grades | his classes |
| Settings, activity log | ✔ | — | — |

How it works in the app
* **Manage → Grades & classes → a grade → "Admins":** shows the grade's admins, lets you **add** one (the big admin may pick any servant; an admin of the grade only people who serve in that grade) or **remove** one *from this grade*. A new grade takes you straight to this page. No grade left = the person is simply a servant again (classes kept).
* One person can be admin of **several grades**: add him in each grade (or Servants → "Make admin" and tick several). Removing him from one grade never touches the others. Promoting only ever *adds* grades.
* An admin never sees the names of grades that aren't his, even when a co-admin also manages another grade.
* An admin can't change another admin's login, password or status, and nobody except the big admin's own account is ever a big admin — promotions are always "admin of grade(s)".
* **Delete a kid** hides him everywhere; an admin can restore from "Deleted (archive)". **Delete a servant** removes the account; kids, attendance and notes stay.
* **Absence pop-up:** N weeks in a row, set per grade on the Grades page (empty = the default in Settings).
* The system always keeps at least one active big admin. All admins of a grade are peers: any of them can remove another from that grade; the big admin can always put someone back.

## Accounts: sign-up, approval, and how people sign in
* **Sign in with any of:** username, phone number or e-mail (phone formats like `+20 101 234 5678`, `0101-234-5678` or Arabic digits all match). E-mail is optional now.
* **Servants create their own account** from the login page ("Create your account"): name, username, phone, password, the grade they will serve in, and which admin should get the request. Nothing works until approved.
* **Approval:** the request appears in **Manage → Requests** (with a red counter) for the chosen admin, the other admins of that grade, and the leader. The approver picks the class(es) and presses Approve — or Decline (this deletes the request so the person can re-apply). The servant can sign in right after approval and is **not** forced to change the password they chose.
* **Leader control:** Settings → "Let servants request their own account" switches the public sign-up page on/off. Every request still needs approval; the form has a honeypot, rate limit and a cap on waiting requests.
* Everyone can edit their own name / username / phone / e-mail under **My account** (the current password is required to change how they sign in, and at least one way to sign in must remain).
* Existing accounts keep working. The database is upgraded automatically at startup (new columns, unique-username index, phone numbers written in one format).

## Kid photos
* Add a photo from the kid form (leader) or from the kid's profile page (leader or the servant of that class — on a phone it opens the camera). One tap, auto-saved.
* Shown on the attendance screen (tap a photo to enlarge it), class lists, visitation and profiles. Kids without a photo show coloured initials.
* Safety: photos are decoded and re-encoded as a fresh 320×320 JPEG (strips GPS/EXIF and anything hidden), stored in `instance/photos` (not public) and served only to people allowed to see that kid. JPG/PNG/WEBP only, up to 15 MB.
* Updating an existing install: run `pip install -r requirements.txt` (adds Pillow). The new `photo_filename` column is added to your database automatically at startup — no data is touched.

## Importing kids from Excel (into ONE class)
Open a class → **Import from Excel** (or Kids → Import), **choose the class**, upload the file, check the preview, confirm. **Every kid in the sheet goes into the class you chose** — the sheet's own grade/class columns are ignored.

* Only the **name** is required. Missing data stays empty; the kid is still created.
* Recognised columns (Arabic or English, many spellings, header row anywhere in the first 15 rows): name, mother's name, father phone, mother phone, kid phone, school, address / location, area, date of birth, siblings, father of confession, notes, gender. The preview shows exactly how each of your columns was understood.
* **Nothing is lost:** columns the system has no field for (e.g. "الحالة الصحية", "اسم الأب") and anything that can't be read (a second phone number, text next to a number, an unreadable date, text too long for a field) are saved inside the kid's **Notes**.
* Phones: `01012345678`, `+20 101 234 5678`, Arabic digits, and numbers where Excel dropped the leading 0 are all fixed. Dates: `20/5/2015`, `2015-05-20`, Excel dates, Arabic digits (day first).
* **Re-importing repairs earlier imports:** a kid already in the class (same name) gets only his *missing* data filled in — nothing already written is overwritten; kids that an old import left with **no class** are moved into the class.
* Download the template from the import page. `.xlsx` / `.csv` (save old `.xls` as `.xlsx`).

## Visitation (الافتقاد) — and keeping a trace
* **Needs a visit** tab: kids who missed weeks first, then anyone not visited for 30 days. Each card shows the address, who visited last and what was written. Log a visit with its **type, date (today or earlier) and notes**.
* **Visit history** tab: every visit made to the class's kids — **including by the other servants of the same class** — with who made it and the notes; the number of visits and kids visited; and the list of kids **not visited yet**. Filter by period (30 / 90 / 365 days / all) and *my visits only*. A servant sees only his own classes; an admin gets a **class selector** (a grade admin only his grades; the leader all classes). You can delete a visit you entered by mistake (an admin can delete any in his scope).
* Everything is also on the kid's profile.

## PDF downloads (servant and admin)
| What | Where |
|---|---|
| Class kids list (names, parents' phones, school, area, address) | class page → *Kids list PDF* |
| One kid's full file (photo, all data, attendance, **visits with who/notes**, notes) | kid profile → *Kid file (PDF)* |
| Attendance of one day / summary of all my classes | Reports → PDF |
| Attendance over the last 4–12 meetings (who came each week) | class page / daily report → *Last 8 meetings PDF* |
| **Visitation report** (full notes + kids never visited) | Visitation → *Visitation report PDF* |

Arabic is right-to-left with proper letter shapes; Latin words, codes and numbers stay readable inside Arabic text. Needs `pip install -r requirements.txt` (reportlab, arabic-reshaper and `python-bidi==0.4.2` — pinned on purpose, newer versions break brackets in Arabic). If the libraries are missing the app still starts and says what to install.

## Backup reminder
Every screen ends with a reminder to download your data as PDFs (or keep a copy outside the system) in case anything happens to the system. Make a habit of downloading the kids list, attendance and visitation report regularly, and keep a copy of `instance/sunday_school.db` (the whole database) somewhere safe.

## Passwords
No spaces anywhere: the browser blocks typing/pasting spaces in every password box and the server rejects them too (min 8 chars with upper, lower, number and symbol).

## What changed in this version
**Security**
* Deactivated users are signed out immediately; a password change/reset signs out every other device.
* Forced password change is enforced on *every* page, not only the home page. Current password required to change it.
* Logout is POST-only (CSRF-safe). Open-redirect protected. Login limited per IP+e-mail (a shared church Wi-Fi can't lock everyone out), no account enumeration, equal-time checks for unknown e-mails.
* Strict Content-Security-Policy (no inline JS/CSS, all fonts self-hosted — **zero third-party requests**), `no-store` on pages with kids' data, X-Frame-Options, nosniff, HSTS in production.
* Dev server no longer runs `debug=True` on `0.0.0.0` (that exposes remote code execution). Weak/missing SECRET_KEY refused in production.
* Attendance: every endpoint checks class ownership; only the lock holder can release; leader takeover is audited.
* Excel export is formula-injection safe; import is validated row by row; all inputs length-limited; whitelists for note categories / visit types / statuses.
* `reset_password.py` and `init_db.py` no longer take or print passwords on the command line. Activity-log page added.

**Logic**
* Attendance is for the most recent meeting day (not next Friday in the future); future dates impossible; servants can correct only the last 14 days.
* Every kid must be explicitly marked (no silent "present" default) + "rest present" shortcut, progress bar, lock heartbeat so a long session isn't lost.
* The settings page (meeting day, absence threshold) is now actually used everywhere.
* Visitation prioritises kids who are *absent*, then those not visited in 30 days; address/phone list can be printed.
* Servants now have a class roster page; leader dashboard lists who needs follow-up; Archived kids can be restored; missing `kid_history` page added.
* Import fixed (it stored rows in the 4 KB cookie, so real files failed). Phone numbers keep their leading 0. English names are optional everywhere.
* Dates use Africa/Cairo and show Arabic weekday/month names.

**UI** — new mobile-first design system (bottom tab bar on phones, big tap targets for attendance), consistent cards/tables, empty states, live password checklist + generator, Arabic/English.
