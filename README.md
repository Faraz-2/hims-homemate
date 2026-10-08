# Home Mate HIMS

Home Mate Integrated Management System: one internal web app for tasks, attendance, leave,
approvals, helpdesks, finance requests, vendors and their product lists, lead and recruitment pipelines, documents,
announcements and MIS reporting, for every department (13 as of version 1.5).

It runs on **one server laptop** in the office (Phase 1). Everyone else opens it in a browser over
the office Wi-Fi/LAN. Data lives in a single SQLite file, so there is nothing else to install.
When you outgrow the laptop, the same code runs on PostgreSQL (see "Moving to PostgreSQL").

> **Verification status (1.0 to 1.4):** version 1.0 was started and used on a real laptop. Versions 1.1 through 1.4 have
> passed the same static checks as 1.0 (templates parse, every link and permission resolves, model columns match
> their usage) but have **not yet been run**. For 1.4 specifically, the promotion rules were also exercised
> directly against 20+ test cases covering every role and actor combination, and the new named accounts and every
> workflow/department/permission reference were checked against each other for typos — but nothing replaces
> actually clicking through it. Do the test plans in "What's new" below before relying on any of this.
> Create a backup before upgrading.

---

## What's new in 1.5.3: chat scrolling fixed

- **Chats now scroll up and down** on laptops, tablets and phones. Before, a long conversation was cut off instead of scrolling, and the message box could be pushed out of view. Now only the message list scrolls, while the chat name and the "Write a message" box stay in place.
- **Load earlier messages** button at the top of a chat brings back older history 50 messages at a time (HIMS used to show only the latest 200).
- **Phones:** an open chat fills the screen like a messaging app, with **All chats** to go back to the list. The chat is sized to the visible screen, including when the keyboard is open.

---

## Deploying so everyone can reach it (internet, HTTPS)

See the step-by-step guide in the chat history that came with 1.5.2; in short: a small Ubuntu server, a domain name, Caddy for automatic HTTPS, HIMS under systemd with `HIMS_HTTPS=1` and `HIMS_BEHIND_PROXY=1`, SQLite in `/var/lib/hims`, and the daily backup copied off the server. Set `HIMS_BEHIND_PROXY=1` **only** when a proxy is in front of HIMS.

---

## What's new in 1.5.1: works properly on phones and tablets

Laptop and desktop screens look exactly as before; everything below only applies to screens narrower than about 900 px.

- **Slide-in menu** with a close button, a dimmed backdrop, tap-outside / Escape to close, and no scrolling of the page behind it. The menu closes by itself when you tap a link.
- **Top bar** becomes two rows on a phone: menu, logo and icons on top, a full-width search box below. The notification and profile drop-downs open as a sheet that fits the screen; the notification list still scrolls.
- **Tables turn into cards** on phones: each row is a card, each value is labelled with its column name, and action buttons sit in an even row. (The permission grids stay real tables and scroll sideways.)
- **Bigger touch targets** (44 px buttons and menu items), inputs at 16 px so iPhones don't zoom in, filters and forms one field per row, full-width buttons.
- **Tabs and boards swipe sideways**; KPI tiles show two across; the chat, task and project pages stack neatly.
- **Sign-in page** shows the form straight away instead of a big black banner.
- Notch-safe layout (iPhone and Android cut-outs), and you can **Add to Home Screen** so HIMS opens like an app without the browser bar.

**Using HIMS on a phone:** connect the phone to the same Wi-Fi as the server laptop and open the address shown in the start-up window under "From other laptops", for example `http://192.168.1.20:8000`. If it will not open, allow port 8000 in Windows Firewall on the server laptop (Windows Security > Firewall > Allow an app, or add an inbound rule for TCP 8000). On the phone, use the browser menu > Add to Home Screen.

---

## What's new in 1.5: Vendor Management, vendor product lists, hiring and removing, Files, complete backups

Nothing from 1.4 was removed or changed in behaviour. Everything below is added.

**Vendor Management is now its own department** (the 13th). It has its own workspace, team chat, tasks and department requests like every other department, and its members work in the Vendors area (Finance and Operations keep their access too).

**Product and menu lists for every vendor.** Open a vendor and press **Products and prices**. A food vendor gets a menu (sections such as Starters, Main course, Drinks; a price and a unit for every dish); a stationery vendor gets a catalogue; a service vendor gets a price list. You can **add, edit, mark unavailable, reprice and delete** items any time, change many prices at once by a percentage, import/export the list as CSV, and see each item's **price history**. Vendors can also have documents and files attached (contracts, price lists). Vendor product items appear in global search.

**Hire and remove people, in several departments.**
- **Employees > Hire into departments** (and **Hire into <department>** on a department's workspace) adds a head, co-head, team lead, employee, intern, or any custom role, with their main department plus up to three more departments and roles in the same form.
- On any profile, **Departments and roles** lets you add or end someone's role in another department later.
- **Remove from team** switches the account off, ends their other-department roles, and hands their open tasks, leads, candidates, tickets, requests, projects and campaigns to a colleague you choose (or cancels their tasks). It is recorded under **Removed from the team** and can be undone by reactivating the person.
- **Pause task assignment** keeps someone on the team but hides them from every "Assign to" list, so no new tasks are generated for them.
- Rules are unchanged in spirit: you can only act on people who rank below you; department heads act inside the departments they lead; administrators act everywhere; the last active Super Admin cannot be removed.
- A person with a Team-Lead-or-above role in a second department can now be given tasks in that department by its leaders, and those tasks are filed under that department.

**Files, any type.** A new **Files** area (left menu and every department workspace) accepts any file type, several at a time, into named folders, with visibility *only me*, *my department* or *whole company*. Leaders and admins can browse by department, then person. Only programs and scripts that run when opened (exe, bat, cmd, msi, ps1 and similar) are blocked; put them in a .zip if you must keep them. Change the list with `HIMS_BLOCKED_EXTENSIONS`, or set `HIMS_ALLOW_ANY_FILE_TYPE=0` to go back to the old short list.

**Everything is stored in sorted folders.** New uploads go to `instance/uploads/departments/<department>/<person>/<year>/<month>/<kind>/`. Files uploaded before 1.5 stay where they are and keep working. **Administration > Where data is stored** shows every location and how many files each department and person has.

**Complete backups: department-wise, user-wise, date-wise.** A *complete backup* makes the usual full backup **plus** one zip per department and one per person, filed as `instance/backups/organized/<year>/<date>/<time>/by-department|by-user/`. Each zip holds readable CSV files of that department's or person's records and their uploaded files sorted by department, person and date. Password hashes are never exported.
- HIMS makes one **automatically every day** while it is running (time and number kept are in System settings).
- **Administration > Backups > Complete backup now**, browse runs by date, or **Export a backup** for one department, one person or a date range. A person's profile links to all of their dated backups.
- `backup_now.bat` and `flask --app run backup-complete` now run the complete backup, so your existing Task Scheduler entry keeps working.
- Restoring after a disaster still uses the **full backup** (`flask --app run restore`). The organised zips are for finding, auditing and handing over data.

**Scroll bars in notifications.** The bell drop-down now holds your 25 latest notifications in a scrolling list with the header and "See all" link fixed, and the Notifications page list scrolls inside its card.

**Two folders: backend and frontend.**

```
hims/
  backend/    all Python code: run.py, config.py, requirements.txt, hims/ package
  frontend/   everything the browser shows: templates/ and static/ (css, js, images)
  instance/   your data (created at first start): database, uploads, backups, secret key
  start_hims.bat / start_hims.sh / backup_now.bat
```

### Upgrading from 1.4 (or earlier) without losing data
1. **Stop HIMS and make a backup first** (Administration > Backups).
2. In your existing project folder, **delete** the old `hims` folder (the Python package), `config.py`, `run.py` and `requirements.txt`. **Keep `instance` and `venv`.**
3. Copy the new `backend`, `frontend` folders and the three launcher files from this version into the project folder.
4. Start with `start_hims.bat`. HIMS adds the new tables and columns, the Vendor Management department and the new rights automatically. Rights for the new features follow each role's tier (for example heads get *Remove people*); adjust in Administration > Roles and permissions.
5. If `venv` stops working after the move, delete it and let the launcher recreate it.

### Test plan for 1.5 (about 25 minutes)
1. Notifications: make 10+ notifications (assign yourself tasks), open the bell, scroll the list; open the Notifications page and scroll.
2. Open **Departments > Vendor Management**. Hire a Team Lead there (**Hire into Vendor Management**), giving them an extra role as Intern in Marketing; confirm both show on their profile and on both team lists.
3. Add a vendor (category *Food & catering*), open **Products and prices**, add sections with a few dishes, edit one price (check **price history**), mark one unavailable, delete one, try the **bulk % change**, then export and re-import the CSV.
4. Attach a PDF and a spreadsheet to the vendor. Delete a vendor with no expenses; confirm one with expenses refuses.
5. As a department head, create a task and check **Assign to** lists the new hire. **Pause task assignment** for them and confirm they disappear from the list; resume.
6. **Remove** a test employee who has an open task: choose a colleague, confirm the task moves, the account cannot sign in, and **Removed from the team** records it.
7. Upload three files of different types (Word, image, video) in **Files**, one per visibility; sign in as a colleague and confirm each is seen or hidden correctly. Try a `.exe` and confirm it is refused.
8. **Complete backup now.** Open the run, download one department zip and one person zip, open the CSVs, and confirm the person's files are inside `files/<department>/<person>/<date>/`. Run `flask --app run verify-backup` on the full backup.
9. **Export a backup** for one department and a date range. Open **Where data is stored** and check the folders match.
10. In System settings, switch automatic backup on, set a time two minutes ahead, leave HIMS running, and confirm a run appears.

> **Verification status for 1.5:** every Python file compiles, every template parses, and every link in every page resolves to a real route. The app itself has **not been started** in the environment where this version was written (the libraries could not be installed there), so nothing replaces the test plan above. Please run it, and send me any error text that appears in the start-up window.

---

## What's new in 1.4: promotions, Project Management, campaigns, and your named leadership

**A real career ladder.** There is a new **Co-Head** role, sitting between Team Lead and Department Head. On any employee's profile, whoever has the right to (a Department Head, Admin, or Super Admin) now sees **Promote** / **move back one rung** buttons: Intern → Employee → Team Lead → Co-Head → Department Head. A Department Head can promote people in their own department all the way up to Department Head; only Admin or Super Admin can do that for people outside their own department. Nobody can promote someone to their own level or above.

**Two new departments:**
- **Project Management** — coordinates projects across every other department. Anyone in this department (or individually granted the right) sees and can manage **every project company-wide**, not just their own department's, and can add anyone from any department onto a project or a cross-department task.
- **Content Creation** — plans and produces the content Marketing and Digital Sales publish. It shares the design/content request queue and the campaign content library with Marketing, as one team.

**People can now hold a role in more than one department.** Someone's profile can show, for example, "Also: Co-Head in Sales" alongside their main role. This is for the org chart and for extending *management scope* (a secondary Team-Lead-or-above role lets someone assign and review work in that department too) — it never changes anyone's actual permissions, which always come from their main role.

**A "Founder" title.** A person can hold the Super Admin role (full rights) while their profile, ID card and directory listing show a different real-world title — used here for Home Mate's founder.

**Your named leadership accounts are created automatically** the first time you start this version (see the table below) — nothing to set up by hand.

**Marketing campaigns**, shared by Marketing, Digital Sales, and Content Creation:
- Plan a campaign: objective, target audience, platforms, budget and timeline.
- Log performance by hand (reach, leads, qualified leads, conversions, engagement, spend) — cost-per-lead and conversion rate are calculated for you.
- Upload content and creative as proof of work, kept as numbered versions, exactly like task attachments.
- Link tasks to a campaign, and link leads to the campaign that generated them — the campaign page then shows **real** pipeline numbers (leads, still-open, won, win rate) alongside the numbers you log by hand.

**Task attachments now accept photos, screenshots and videos** (not just documents), and the upload size limit is configurable (`HIMS_MAX_UPLOAD_MB`, default 100 MB) instead of a fixed 25 MB — useful for Digital Marketing's "upload a video as proof of completed work."

**Department team progress**: any department's manager-level view now shows a per-person and overall breakdown of Assigned / In progress / Submitted / Needs changes / Completed / Overdue, with a completion percentage — the "who's behind" view Digital Sales and Marketing asked for.

**Employee IDs are now typed by role**: `HM-HOD-0001` for a Department Head, `HM-COH-0001` for a Co-Head, `HM-TL-0001` for a Team Lead, `HM-EMP-0001` for an Employee, `HM-INT-0001` for an Intern, `HM-ADM-0001` for Admin/Super Admin — each series numbered on its own, and an ID never changes when someone is promoted.

**Renamed for clarity:** the task status "Waiting" is now "Blocked" (it was reusing the same word as a ticket "waiting for the customer's reply", which was confusing on a task).

### Your leadership accounts
Everyone below must **change their password at first sign-in**. They all start with the same password as your main admin account (`HIMS_ADMIN_PASSWORD`, default `HomeMate@2026`).

| Name | Email | Role | Department | Also |
|---|---|---|---|---|
| Faraz Haider | faraz.haider@homemate.local | Super Admin | — | Intern in Marketing, Team Lead in IT, Intern in Content Creation |
| Sheraz Syed | sheraz.syed@homemate.local | Super Admin (titled "Founder") | — | |
| Aisha | aisha@homemate.local | Department Head | HR | |
| Ramish | ramish@homemate.local | Department Head | IT | |
| Hamza Khan | hamza.khan@homemate.local | Department Head | Operations | Co-Head of Sales |
| Warisha | warisha@homemate.local | Co-Head | Operations | |
| Maryum Batool | maryum.batool@homemate.local | Department Head | Project Management | |

The original generic `admin@homemate.local` Super Admin from version 1.0 still exists alongside these (existing installs keep it; new installs still get it as a fallback). Deactivate it yourself from Employees once you've confirmed the named accounts work, if you'd rather not keep it. Every other department has no Head, Co-Head or Team Lead assigned yet — add people through Employees, or promote someone up from their profile.

### Upgrading from 1.3 (or earlier)
Take a backup first. Then: stop HIMS, copy the new files over the project folder (keep `instance` and `venv`), start again. On start-up HIMS adds the new database columns and tables, creates the Co-Head role, the Project Management and Content Creation departments, and the named leadership accounts above (only if their email doesn't already exist — it will never overwrite anyone).

### Test plan for 1.4 (about 20 minutes)
1. Sign in as `faraz.haider@homemate.local`, change the password, and confirm his profile shows "Also: Intern in Marketing, Team Lead in IT, Intern in Content Creation."
2. Sign in as `aisha@homemate.local` (HR Head). Open an HR employee's profile and **Promote** them one rung; confirm the button offering the *next* promotion disappears once they'd reach Aisha's own level.
3. As Aisha, confirm she **cannot** promote someone in a different department.
4. Sign in as an Admin and promote a Co-Head to Department Head in any department — confirm only Admin/Super Admin can do that step.
5. Open **Project Management**'s workspace as Maryum Batool, go to **Projects**, and confirm you can see and open a project that belongs to a different department.
6. Open **Campaigns** as a Marketing or Content Creation user: create one, upload a piece of content, log some performance numbers, then link a lead (from the Sales/Marketing lead pipeline) to that campaign and confirm the campaign page's "Customer acquisition" numbers update.
7. Attach a short video or a photo to a task and confirm it uploads and downloads correctly.
8. Open any department's workspace as its head and confirm the new **Team progress** table appears with correct per-person numbers.
9. Check a task moves correctly through **Assigned → Accepted → In progress → Blocked → In progress → In review → Completed**, and that its badge reads "Blocked", not "Waiting".
10. Print an ID card for Hamza Khan and Sheraz Syed; confirm Sheraz's shows "Founder" while Hamza's shows his normal role, and that Hamza's card or profile shows his Sales co-headship.

## What's new in 1.3: your logo, projects, a richer task workflow, and NIC Hyderabad

**Your logo** is now used throughout: the sidebar, the sign-in page, the browser tab icon, and the printable employee ID card.

**Projects**: group related tasks under one manager, one deadline and one progress bar (Projects, in the menu). Progress is calculated automatically from how many of the project's tasks are completed. Add members, assign tasks to the project, and discuss it in its own comment thread.

**Richer task workflow**, closer to how real handoffs work:
- New stages: a task is now **Assigned**, then the assignee can **Accept** it before starting, then **In progress**, with an optional **Waiting** state for blocked work, then **In review**, ending in **Completed**, **Rejected**, or **Cancelled**.
- **Checklists** on any task.
- **Attach a file** to a task at any point — every attachment is kept as a numbered version (v1, v2, v3...); nothing is ever overwritten, so the full history of submissions stays on the task.

**Employee documents and certifications**: every employee profile now has a Documents tab (CNIC, degree, contract, and anything else you configure). Uploading is optional unless an admin marks a document type as **Required** (Administration, Employee document types), and a document type can also require an **expiry date** or **HR verification** after upload.

**Printable Employee ID card**: open it from any employee's profile and print it or save it as a PDF from the browser's print dialog.

**NIC Hyderabad** is now its own department, with its own startup-incubation pipeline (Applied → Screening → Interview → Selected → Onboarded → Graduated, or Rejected), its own mentor assignment, cohorts, and the same team chat, tasks, documents and department-request tools every other department has.

### Upgrading from 1.2 (or earlier)
Take a backup first. Then: stop HIMS, copy the new files over the project folder (keep `instance` and `venv`), start again. On start-up HIMS adds the new database columns and tables automatically, seeds the standard document types, and creates the NIC Hyderabad department. Existing tasks, projects-less as they are, and documents are unaffected; the new task stages only appear going forward.

### Test plan for 1.3 (about 15 minutes)
1. Confirm your logo appears correctly: sidebar, sign-in page, browser tab icon, and one employee's printable ID card.
2. **Projects:** create a project, add two members, then create a task and assign it to that project. Confirm the project's progress bar updates once the task is completed.
3. **Task workflow:** create a task, and as the assignee, click through **Accept task → Start work → Mark as waiting → Resume work → Submit for review**. As the creator, **Request revision**, then **Approve and complete**. Try **Reject task** on a different one.
4. **Checklist:** add two checklist items to a task, tick one, delete the other.
5. **Versioned files:** attach a file to a task, then attach a second file. Confirm both appear in the history labelled v1 and v2, and both can still be downloaded.
6. **Employee documents:** as an admin, add a new document type marked Required with an expiry date. Open an employee's profile, upload that document with an expiry date, then mark it verified. Confirm it shows the right status badge (uploaded / pending verification / expiring soon / expired).
7. **NIC Hyderabad:** open the department from Departments, add a startup application, move it across the pipeline stages, and assign a mentor.
8. Check the activity log records the new actions, and that Roles and rights shows the two new "Projects" and "NIC Hyderabad" right groups.

## What's new in 1.2: people, roles and rights

**More kinds of employee**
- New built-in **Intern** role (own work only), next to Employee, Team Lead, Department Head, Admin and Super Admin.
- Every person now has an **employment type** (Full-time, Part-time, Contract, Intern, On probation) and a **line manager** ("reports to").
- **Create your own roles** (Administration > Roles > Manage roles): for example "Senior Designer", "HR Executive", "Trainee". Pick a *tier* (how much authority) and the role starts with that tier's standard rights; then fine-tune in the permission matrix.

**Adding people**
- **Admins** can add anyone below their level, and **import many employees from a spreadsheet** (Employees > Import from spreadsheet; a sample file is provided). Temporary passwords are shown once.
- **Department heads** can now add, edit, reset passwords for and deactivate people **in their own department** who rank below them. They cannot create roles above their own or move people to other departments.

**Who can give tasks to whom** (also explained on the new *Roles and rights* page in the menu)
- Everyone can create tasks for themselves.
- Team leads and heads can give tasks to people in their own department who rank below them, and to anyone who reports to them. Not to peers, their own boss, or other departments.
- Admins can give tasks to anyone. Employees and interns cannot assign work; they send a department request instead.

**Individual rights**: on any employee's profile, *Manage rights* lets you allow or deny one specific right for one person without changing their role. You can only grant rights you hold yourself, and every change is logged.

**Roles and rights page**: a plain-English table of what each role can do, built from the live permission matrix.

### Upgrading from 1.1 (or 1.0)
Same steps as before: stop HIMS, copy the new files over the project folder (keep `instance` and `venv`), start again. On start-up HIMS **adds the new columns to your existing database automatically** and creates the Intern role. Existing people keep their roles and become "Full-time" with no line manager. **Take a backup first** (Administration > Backups).

### Test plan for 1.2 (about 15 minutes, with the sample data)
1. Sign in as the Super Admin. **Administration > Roles and permissions > Add or edit roles**: create a role "Trainee" with the *Intern tier*. Open the permission matrix and confirm it has a column with a few basic rights ticked.
2. **Employees > Add employee**: add a person with the *Intern* role, employment type *Intern*, choosing a line manager. Note the temporary password.
3. Sign in as that intern: the menu should be short; **Tasks** lets them create a task only for themselves (no "Assign to" box).
4. Sign in as a **department head** (for example `sana.malik@homemate.local`). Confirm **Employees > Add employee** is available, the Department box offers only their own department, and the Role box offers only roles below theirs.
5. As that head: open a team member > **Manage rights**. Allow one extra right (for example *Post company announcements* if the head holds it), save, then sign in as that person and confirm it works. Then set it back to *Follow role*.
6. As the head, create a task and check the **Assign to** list contains only their department's juniors (and their direct reports), not themselves-plus-everyone.
7. Open **Employees > Import from spreadsheet**, download the sample, edit two rows, upload it, and confirm accounts and temporary passwords appear. Upload a row with a bad email and confirm it is skipped with a clear reason.
8. Open **Roles and rights** from the menu as any user. Your role's column is outlined.
9. Check **Administration > Activity log** shows the role, rights and import actions.

## What's new in 1.1

**Messages** (left menu, and the speech-bubble icon in the top bar with its unread count)
- **Team chat:** one chat per department; everyone in that department can read and post. Admins can open any department's chat.
- **Direct messages:** private between two people; the other person gets a notification.
- Share a file in any chat (same upload rules as everywhere else). Open chats update by themselves every few seconds.

**Department requests** (left menu)
- Any department can send a formal request to another: Sales to Finance, Marketing to Design, Operations to Vendor Management, and so on.
- Flow: **Submitted, Assigned, In progress, In review, Completed.** The receiving department's head assigns an owner, or a team member can pick it up. The sender approves the finished work or asks for a revision. It can also be declined (with a reason) or cancelled.
- Every step is recorded in the request's history, notifies the right people, and appears in the activity log. The dashboard shows what is waiting for you.

### Upgrading from 1.0 without losing data
1. Stop HIMS (Ctrl+C in its window).
2. Copy the new files **over** your existing project folder, replacing files when asked. Do **not** delete the `instance` folder (your database, uploads and backups) or `venv`.
3. Start HIMS again (`python run.py`). New tables and permissions are added automatically; existing data is untouched.
4. Recommended: create a backup first (Administration > Backups).

### Test plan for 1.1 (about 10 minutes, with the sample data)
1. Sign in as `ayesha.khan@homemate.local` and open **Messages**. You should see your department's team chat. Post a message.
2. In a second browser window (or a private window), sign in as a colleague in the same department. The message should appear within a few seconds, and the top-bar icon should show an unread count.
3. Start a **direct message** with someone from another department and confirm they get a bell notification.
4. As a normal employee in a *different* department, confirm you **cannot** read the first department's team chat (it does not appear in your list).
5. Open **Department requests > New request**, send one from Sales to Finance with an attachment.
6. Sign in as the Finance head: the request appears under **Incoming** and on their dashboard. **Assign** it to someone, then sign in as that person and click **Start work**, then **Submit for review**.
7. Sign back in as the sender and **Approve and complete** (also try **Request revision** on another request).
8. Check **Administration > Activity log** shows the steps, and that the global search finds the request by title.

## 1. What you need on the server laptop

- Windows 10/11 (Linux and macOS also work, see `start_hims.sh`)
- Python 3.10 or newer from python.org (tick **"Add Python to PATH"** in the installer)
- Internet access **once**, for the first start (it downloads the libraries)

## 2. First start (Windows)

1. Copy the whole `hims` folder (it contains `backend` and `frontend`) to the server laptop, for example `C:\HIMS`.
2. Double-click **`start_hims.bat`**.
   The first run creates a private Python environment and installs the libraries (a few minutes).
3. A window shows two addresses:

   ```
   On this computer : http://localhost:8000
   From other laptops: http://192.168.1.25:8000     <- yours will differ
   ```

   Leave that window open. Closing it stops HIMS.
4. Open `http://localhost:8000` and sign in as the Super Admin:

   | Email | Password |
   |---|---|
   | `admin@homemate.local` | `HomeMate@2026` |

   You are forced to choose a new password straight away. To use your own starting email or
   password instead, set `HIMS_ADMIN_EMAIL` / `HIMS_ADMIN_PASSWORD` **before the very first start**.
5. Go to **Employees > Add employee** and create everyone. Each person gets a temporary password
   and must choose their own at first sign-in.

**Linux / macOS:** run `./start_hims.sh` instead.

### Optional: try it with sample data first

This fills a *fresh* database with about 18 sample staff, tasks, leads, tickets and so on, so you can
click around. Do **not** run it on a database that holds real data.

```
venv\Scripts\activate
cd backend
flask --app run seed-demo
```

Sample users all use the password `Welcome@123` (for example `ayesha.khan@homemate.local`).
To start clean afterwards, stop HIMS and delete the `instance` folder.

## 3. Letting other laptops connect (LAN setup)

1. **Same network.** The server laptop and the other laptops must be on the same office Wi-Fi/LAN.
2. **Allow the port through Windows Firewall.** Run once in an Administrator Command Prompt:

   ```
   netsh advfirewall firewall add rule name="HIMS" dir=in action=allow protocol=TCP localport=8000 profile=private
   ```

   (Windows may also pop up a "Allow access?" prompt the first time. Choose **Private networks**.)
3. **Give the server a fixed address.** In the router, create a *DHCP reservation* for the server
   laptop so its IP never changes. Otherwise bookmarks break whenever the router hands out a new IP.
4. **Tell staff the address**, for example `http://192.168.1.25:8000`, and have them bookmark it.
5. **Keep the laptop awake.** Settings > System > Power: set sleep to **Never** while plugged in, and set
   "when I close the lid" to **Do nothing**. Keep it plugged in.

If a laptop cannot connect: confirm it is on the same network, try the address in the server's own
browser first, then re-check the firewall rule. Some guest Wi-Fi networks isolate devices from each other.

## 4. Backups (do this on day one)

A backup is one zip file containing the database and every uploaded file.

- **Manual:** sign in as Super Admin > **Administration > Backups > Create backup now**. You can download it there.
- **Automatic, daily:** schedule `backup_now.bat` with Windows Task Scheduler. In an Administrator
  Command Prompt (adjust the path and time):

  ```
  schtasks /create /tn "HIMS Backup" /tr "C:\HIMS\backup_now.bat" /sc daily /st 22:00
  ```

- **Second copy (important):** a backup that only lives on the server laptop dies with the laptop.
  In **Administration > System settings**, set **Second copy folder** to a folder on an external drive or a
  synced cloud folder (for example `D:\HIMS-Backups` or a OneDrive/Google Drive folder). Every backup is
  then also copied there. The Backups page shows whether the second copy is working.
- Old backups are pruned automatically; the number kept is set in System settings (default 14).

### Test a restore, regularly

An untested backup is only a hope. At least monthly:

```
venv\Scripts\activate
cd backend
flask --app run verify-backup "instance\backups\<file>.zip"
```

This opens the backup, runs a database integrity check and prints row counts, without touching live data.

To actually restore (SQLite only): **stop HIMS first**, then

```
flask --app run restore "instance\backups\<file>.zip"
```

It asks for confirmation and keeps the previous database next to it as a `.before-restore` copy.
For a full rehearsal, do this on a second laptop with a copy of the project and a backup file.

## 5. Everyday admin tasks

| Task | How |
|---|---|
| Add / deactivate an employee, reset a password | Employees page (deactivating keeps their history) |
| Unlock an account after too many wrong passwords | Administration page > Accounts needing attention (also unlocks itself after a while) |
| Change who can do what | Administration > Roles and permissions (permission matrix) |
| Working hours, leave allowance, currency, company name | Administration > System settings |
| See who changed what | Administration > Activity log |
| Locked out as Super Admin | `flask --app run reset-password you@homemate.local` |
| Create another Super Admin | `flask --app run create-admin` |

Built-in roles, from most to least access: **Super Admin, Admin, Department Head, Team Lead, Employee, Intern**, plus any roles you create.
Some areas are also limited by department no matter what the matrix says: Leads (Sales/BD, Marketing, Digital
Sales), Recruitment (HR), Finance and Vendors (Finance, Vendor Management, Operations), CSR helpdesk (CSR), IT
helpdesk staff (IT). **Operations** and admins see company-wide MIS reports; other heads see their own department.

## 6. Configuration (all optional)

Set as environment variables (for example in `start_hims.bat` with `set NAME=value` above the `python run.py` line).

| Variable | Default | Meaning |
|---|---|---|
| `HIMS_PORT` | `8000` | Port to listen on |
| `HIMS_HOST` | `0.0.0.0` | `0.0.0.0` = reachable from the LAN; `127.0.0.1` = this computer only |
| `HIMS_INSTANCE_DIR` | `.\instance` | Where the database, uploads, backups and secret key live |
| `HIMS_DATABASE_URL` | SQLite file in `instance` | Set to a PostgreSQL URL to switch database |
| `HIMS_SECRET_KEY` | generated once, saved in `instance\secret.key` | Signs login sessions |
| `HIMS_SESSION_HOURS` | `8` | Idle time before sign-in is required again |
| `HIMS_TIMEZONE` | `Asia/Karachi` | Time zone for attendance and reports |
| `HIMS_ADMIN_EMAIL`, `HIMS_ADMIN_PASSWORD` | see section 2 | First Super Admin (first start only) |
| `HIMS_HTTPS` | `0` | Set to `1` only if you put HIMS behind HTTPS |
| `HIMS_BEHIND_PROXY` | `0` | Set to `1` when Caddy/nginx/a tunnel is in front, so real visitor addresses are logged |
| `HIMS_AUTO_INIT` | on | Creates tables and starting data automatically at start |
| `HIMS_BLOCKED_EXTENSIONS` | exe,bat,cmd,com,scr,msi,vbs,vbe,ps1,jar,dll,hta,lnk,pif | File types refused on upload (`none` allows everything) |
| `HIMS_ALLOW_ANY_FILE_TYPE` | `1` | `0` returns to the old short list of allowed types |

Everything worth keeping is inside the **`instance`** folder. Never publish `secret.key`.

## 7. Moving to PostgreSQL (when you outgrow the laptop)

1. Install PostgreSQL, create an empty database and user.
2. In the project's environment: `pip install psycopg2-binary`.
3. Set `HIMS_DATABASE_URL=postgresql://user:password@host:5432/hims` and start HIMS. Tables and starting data are created on first start.
4. Backups then use `pg_dump`, so the PostgreSQL client tools must be installed on the server.

Be aware of two limits in this version: there is **no automatic copy of existing SQLite data into PostgreSQL**,
so it is best to make the switch before real data accumulates (or re-enter / import the data), and the
`restore` command works for SQLite only (restore PostgreSQL with `pg_restore`/`psql`).

## 8. Smoke test (10 minutes, on the server laptop)

Because the app has not yet been booted end to end (see the note at the top), please do this once:

1. `venv\Scripts\activate`, then `cd backend`, then `flask --app run init-db`, then `flask --app run seed-demo`.
2. `python run.py`. It should print the addresses with no errors.
3. Sign in as `admin@homemate.local`, change the password, and open every item in the left menu.
4. Sign in as `ayesha.khan@homemate.local` / `Welcome@123` (a non-admin) and confirm the menu is shorter.
5. Create a task, move it through To do > In progress > In review > Completed, and check the notification bell.
6. Submit a leave request and an expense; approve them as a manager / finance user.
7. Administration > Backups > Create backup now, then `flask --app run verify-backup` on it.
8. From another laptop, open the LAN address.

If anything fails, copy the error text shown in the start-up window; that is exactly what is needed to fix it.
When you are done, delete the `instance` folder to remove the sample data and start for real.

## 9. Project layout

```
start_hims.bat / .sh   one-click launchers      backup_now.bat   scheduled complete backup
backend/
  run.py               start the server (waitress; also starts the daily automatic backup)
  config.py            settings and environment variables
  requirements.txt
  hims/                app factory, models, constants, security, scopes, services
    backup_vault.py    complete / department-wise / user-wise / date-wise backups
    storage.py         where uploads are filed on disk
    blueprints/        one module per area (tasks, vendors, vendor_products, files, people, admin, ...)
frontend/
  templates/           pages (one folder per area)
  static/              css, js, images (orange/black/white styling)
instance/              created at first start: database, uploads/, backups/ (+ organized/), secret key
```

## 10. Security notes

- Passwords are stored as one-way hashes; five failed sign-ins lock an account for 15 minutes (an admin can unlock it sooner).
- Every form is CSRF-protected; uploads are extension-checked (programs and scripts are blocked) and size-limited (HIMS_MAX_UPLOAD_MB, default 100 MB); file paths cannot escape the uploads folder.
- HIMS speaks plain HTTP on the office network, which is normal for a Phase 1 LAN tool. Do not expose the port to the internet.
  If you later need remote access, put it behind a VPN or an HTTPS reverse proxy and set `HIMS_HTTPS=1`.
