# Elite Student Progress Tracker — Final

A Flask + SQLite student self-learning tracker designed around an Elite-style daily workflow.

## Included
- Student and Admin portals
- Student registration with Program, Department and Year
- Daily 7-hour planner — one plan per student per day, editable
- FXEC PS Portal course/level tracker for all 19 official course groups (62 levels)
- Flexible course/level input normalization on the backend
- Daily PS reports are **Pending** until Admin verification
- Previous PS completion verification workflow
- Verified-only Skill Matrix, Performance and Leaderboard
- Student profile and history
- Private Help Desk
- Admin Student Lookup and Student Directory
- Admin verification center
- Admin password reset, name correction, academic correction, activate/deactivate
- Admin support desk
- Admin audit trail
- Light/dark UI with `#c8ff00` progress accent
- Responsive UI

## Run
Open this folder in VS Code / PowerShell and run:

```text
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

If Flask is not installed:

```text
pip install -r requirements.txt
```

## Admin login

- Email: `admin@elite.edu`
- Password: `admin123`

Change the Admin authentication before any real deployment. This project is a local student prototype, not production-grade institutional authentication.

## Important data rule

Students can report daily PS activity, but self-reported data does not become official progress automatically. Previous completed levels are submitted as verification requests. Admin approval creates the verified completion record used by the Skill Matrix, Performance and Leaderboard.

## College wording

The UI describes this as a student project / prototype designed for the FXEC Elite self-learning workflow. It does **not** claim to be an officially authorized college portal.

## Database

`database.db` is created automatically on first run. The package also contains `database_backup_current.db` as a backup copy of the previous working project's database; it is not used automatically.
