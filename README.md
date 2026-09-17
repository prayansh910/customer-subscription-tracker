# SubTrack Cloud — Private Customer Subscription Tracker

This version converts the local tracker into a private Flask + PostgreSQL web application suitable for deployment on Render.

## Result

You can open the application from any device:

`https://<your-render-service>.onrender.com`

and sign in with your private admin account.

No Python, SQLite, or `start.bat` is required on the device you use to access the deployed version.

## Architecture

Browser → HTTPS → Flask/Gunicorn → Render PostgreSQL

The application does not automate or log into any external Google/family accounts.

## Security

- Login required for all management pages.
- Passwords stored using Werkzeug password hashing.
- CSRF protection on state-changing forms.
- HttpOnly/SameSite session cookies.
- Secure session cookies in PostgreSQL/cloud mode.
- Production Flask debug mode is disabled.
- Secrets are supplied through environment variables.
- Database credentials are not stored in source code.
- Customer records with assignment history cannot be deleted.
- Parent changes create a new assignment while preserving the old one.

## Deploy to Render

Render supports deploying Flask web services with Gunicorn and connecting them to managed Postgres. See the official deployment documentation:
https://render.com/docs/deploy-flask

### Easiest method

1. Put this project in a private GitHub repository.
2. In Render choose **New → Blueprint** and connect the repository.
3. Render reads `render.yaml`.
4. It creates:
   - a Flask web service
   - a PostgreSQL database
5. Set:
   - `ADMIN_EMAIL` = your login email
   - `ADMIN_PASSWORD` = a strong unique password
6. Deploy.
7. Open the generated HTTPS URL.
8. Log in.

### Important

Do not commit real passwords, database URLs, customer data, or `.env` files to GitHub.

## Database

Production uses PostgreSQL through `DATABASE_URL`.

Local development can still use SQLite if `DATABASE_URL` is not set.

## Backups

The application has a **Download Full Backup** function that exports all application tables as a JSON file.

You can restore that JSON file through Backup & Settings.

For the production PostgreSQL database, also use the hosting provider's managed backup/recovery features. Render documents point-in-time recovery and logical database exports for paid PostgreSQL instances.

Do not treat a web server's local filesystem as the production database.

## Date rule

Family expiry is always copied from the selected parent subscription period.

Example:

Family subscription:
`17 Sep → 17 Oct`

Customer added:
`19 Sep`

The record remains:

`Added: 19 Sep`
`Family Expires: 17 Oct`

The application never calculates family expiry from the customer-added date.

## Local development

Install Python 3.10+ and run:

```bat
start.bat
```

Local mode uses SQLite. Cloud mode uses PostgreSQL.

## Current functionality

- Dashboard
- Customers
- Customer search
- Customer details
- Customer subscriptions
- Parent accounts
- Multiple parent subscription periods
- Capacity / available slots
- Monthly assignments
- Change parent
- Permanent assignment history
- CSV exports
- Full JSON backup
- JSON restore
- Private authentication
- PostgreSQL cloud deployment
