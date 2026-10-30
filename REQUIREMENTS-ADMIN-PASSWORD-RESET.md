# Requirements — admin password reset

Status: approved for build.

## Goal

Let any admin set the password of **any** account, so an instance owner can get a locked-out
user back in without touching the database by hand.

## What already exists (mirror this, don't invent)

- `UserDataModel.is_admin` is the admin flag; `admin_required` (app.py:548) guards admin routes.
- Admin surface: `/admin`, `/admin/health`, `/admin/audit`, and JSON POST endpoints under
  `/admin/users/<id>/…` — `create`, `update`, `reset-token`, `delete`.
- **`admin_reset_token` (app.py:1342) is the exact analogue**: `@login_required` +
  `@admin_required`, look the user up, 404 if absent, set the credential, commit, return JSON.
  The new endpoint should read like its sibling.
- `MIN_PASSWORD_LENGTH = 8` (app.py:130) is enforced by registration, self-service password
  change, and `admin_create_user`. A reset is a password write and must enforce it too.
- There is **no audit table**: `/admin/audit` tails the last 100 lines of
  `instance/logs/beakon.log`. So `app.logger.info(...)` *is* the audit trail, and the reset must
  write one.
- There is **no CSRF protection** anywhere in the app. Do not add a CSRF scheme to this one
  endpoint; consistency matters more here and a lone token would break the existing JS pattern.

## 1. Endpoint

`POST /admin/users/<int:user_id>/reset-password`, decorated `@login_required` then
`@admin_required`, reading JSON `{"password": "…"}`.

Behaviour, in order:

1. Unknown `user_id` → `404 {'error': 'User not found'}` (matching `admin_reset_token`).
2. Missing or empty `password` → `400`, with the same wording style as `admin_create_user`.
3. Shorter than `MIN_PASSWORD_LENGTH` → `400` naming the minimum.
4. Otherwise set the password, commit, log, and return `200` with a small JSON body.
5. **Never return the password in the response.** `reset-token` echoes the token because the
   admin has to hand it to the user; here the admin *chose* the password, so echoing it only
   risks it landing in a response body, a log line, or browser history.

## 2. Authorization

- Any admin may reset **any** account, including another admin's and their own — that is the
  literal requirement.
- A non-admin hitting the endpoint must be refused by `admin_required`, not by luck.
- A non-admin must not see the button. `inject_admin_status` already exposes `is_admin` to
  templates; use it.

## 3. Audit

Log one line per successful reset, in the existing house style:

```
app.logger.info('%s reset the password for %s', current_user.get_username(), user.get_username())
```

Never log the password, and do not log a failed attempt as though it succeeded. This is the
only record that the action happened, since the audit page reads the log file.

## 4. UI

On `/admin`, each row in the user list already has Edit / Reset Token / Delete buttons driven by
`data-action` and `data-user-id`. Add a **Reset Password** button in the same style, plus a small
modal with a single password input and a confirm button, POSTing to the new endpoint exactly the
way the existing buttons do.

Keep it consistent with the existing modal (`#user-modal`) rather than introducing a new
pattern. On success, close and give brief feedback; on a 400, show the error text the endpoint
returned rather than a generic failure.

## 5. Tests

Add to the existing admin test module:

- an admin resets another user's password, and that user can then log in with the new one;
- the target's **old** password no longer works;
- a non-admin gets 403 (and the password is unchanged);
- a short password gets 400 (and the password is unchanged);
- a missing password gets 400;
- an unknown user id gets 404;
- one admin can reset another admin's password;
- the response body does **not** contain the password;
- a successful reset writes a log line naming both accounts.

## 6. Deliberate non-goals, and one limitation to state plainly

- No CSRF token (see above).
- No email or "forgot password" flow.
- No forced change on next login.
- **A reset does not invalidate sessions that are already logged in.** Sessions are Flask-Login
  cookies with no server-side store, so a user who is currently signed in stays signed in until
  their cookie expires, even after their password changes. Fixing that needs a per-user session
  version column and a schema migration — worth doing, but it is a separate change and the docs
  should say so rather than implying the reset revokes access.

## Acceptance criteria

1. `python -m pytest -q` passes with the new tests present and no existing test weakened.
2. The endpoint is reachable only by an authenticated admin.
3. The password write goes through `UserModel.set_password`, so it is hashed — never stored or
   logged in clear.
4. A reset is visible on `/admin/audit` (i.e. it appears in the log tail).
5. The UI button appears only for admins, and the flow works end to end from the page.
