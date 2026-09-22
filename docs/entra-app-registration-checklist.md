# Entra checklist: WORK FROM THIS FILE

This is the only file with steps to do. `entra-setup.md` is background reading (why, not how).

## Read this first: what tripped us up the first time
- **Two apps, one letter apart in name.** `iceberg-mcp-server` and `iceberg-mcp-client`.
  Nearly every real problem in the first pass was doing a step on the wrong one (the API
  scope got added to the client by mistake; a secret was believed created but wasn't).
  **Every step below is tagged `[SERVER]` or `[CLIENT]`. Before each step, check the app name
  in the browser breadcrumb or page title matches the tag.**
- **"Looks okay" is not verification.** Every checked box below was confirmed with a
  screenshot of the actual page, not a self-report. Do the same: send a screenshot after each
  step, not just "done".
- **Tokens from this tenant come back as v1** (`ver: 1.0`, issuer `sts.windows.net/...`),
  even with the server app's manifest set to request v2. v1 tokens have no
  `preferred_username` or `upn`, only `email`. Don't assume v2; check with
  `scripts/verify_real_token.py` (step 6) before configuring anything against it.
- **The tenant ID is not the GUID next to your name on the Entra homepage** — that's your
  user object ID. Get the tenant ID from Identity > Overview, or from any app registration's
  Overview page ("Directory (tenant) ID"), or ask Claude to verify one via the public
  `/.well-known/openid-configuration` endpoint (works for any GUID, no login needed).
- **Screenshots and downloaded files:** save them somewhere other than the repo root if you
  can (a Downloads folder, or a scratch folder). The repo root is watched for exactly one new
  screenshot at a time; a build-up of old ones makes "the screenshot" ambiguous. `.gitignore`
  covers `Screenshot*.png` and `*.png` (except the tracked architecture diagram) either way,
  so stray screenshots won't get committed.

Portal: https://entra.microsoft.com > Identity > Applications > App registrations > **All applications**

## Known-good values (verified 2026-09-22 against a real token, see build-plan.md)
| Item | Value |
|---|---|
| Tenant ID | `650a1000-e5e3-40bf-97a9-d62002a0934b` |
| `iceberg-mcp-server` Application (client) ID | `1217683d-abaf-41ff-bbf7-c53a0a8814a9` |
| `iceberg-mcp-client` Application (client) ID | `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` |
| Foundry redirect URL | `https://global.consent.azure-apim.net/redirect/9e7a7a930b29482a9ff0291aaf0aa410` |
| Token version this tenant issues | v1 (`ver: 1.0`, `iss` = `https://sts.windows.net/<tenant>/`) |
| Not app registrations — do not use | GUID next to your name on the Entra homepage (your user object ID); "Entra agent identity" shown in Foundry's auth dropdown (that's the Microsoft Entra agent-identity option, not a registration you made) |

## Why two apps
| App | Job | Foundry field it feeds |
|---|---|---|
| `iceberg-mcp-server` (the API) | The token's audience | **Scopes** |
| `iceberg-mcp-client` | What Foundry (and our token script) signs in through. Has the secret. | **Client ID**, **Client secret** |

## Part A — `[SERVER]` `iceberg-mcp-server`
Breadcrumb/title must say **iceberg-mcp-server** before you do any of these.

- [x] **A1. Register.** Single tenant, no redirect URI. Verified via Overview screenshot:
      client ID `1217683d-...`, tenant ID `650a1000-...`.
- [x] **A2. Expose an API.** Left menu > **Expose an API**.
  - Application ID URI: **Add** > keep default `api://1217683d-abaf-41ff-bbf7-c53a0a8814a9` > Save.
  - **+ Add a scope**: name `access_as_user`, who can consent **Admins and users** (not
    "Admins only" — the first pass left this on the default, which still works while you're a
    Global Administrator granting consent yourself, but won't for a non-admin user later),
    state Enabled.
  - Verified via screenshot: Application ID URI is filled in (not "Add an Application ID
    URI"), and the scope shows **State: Enabled**.
- [ ] **A3. Token version 2 (best-effort).** Manifest > `api` block >
      `"requestedAccessTokenVersion": 2` (older format: `"accessTokenAcceptedVersion": 2`) > Save.
      Note: on this tenant, a real token still came back as v1 after this was set. Do it
      anyway (Sofra's tenant may honor it), but don't be surprised if `ver` is still `1.0` —
      that's a known, handled case (see USER_MAP below), not a failure.

## Part B — `[CLIENT]` `iceberg-mcp-client`
Breadcrumb/title must say **iceberg-mcp-client** before you do any of these.

- [x] **B1. Register.** Single tenant. Verified: client ID `ac7b4ab5-...`.
- [x] **B2. Permission to call the API.** API permissions > Add a permission >
      "APIs my organization uses" > `iceberg-mcp-server` > Delegated > `access_as_user` > Add.
- [x] **B2b. Grant admin consent** for the tenant. Status column green.
- [x] **B3. Allow public client flows.** Left menu **Authentication (Preview)** > click the
      **Settings** tab (not "Advanced settings" — that's the old portal's name, the current
      one has this under Settings). Toggle **Allow public client flows** to **Enabled**. Save.
      Verified via screenshot: toggle shown blue/Enabled.
- [x] **B4. Client secret.** Certificates & secrets > **Client secrets** tab > **+ New client
      secret**. Copy the **Value** column immediately (shown once) and paste it into Foundry's
      Client secret field. Verified via screenshot showing "Client secrets (1)" with a row
      present (the first pass had "Client secrets (0)" despite believing this was done —
      always check this count, not just whether you remember doing it).
- [x] **B5. Foundry redirect URL(s).** Authentication (Preview) > **Redirect URI configuration**
      tab, as **Web** entries. **Confirmed: every new or recreated Foundry OAuth tool
      connection mints its own unique redirect URL** (the GUID at the end of
      `https://global.consent.azure-apim.net/redirect/<guid>` differs per connection, not per
      client app or project). Azure also does not allow editing an OAuth connection once
      created, so a new connection is common. Each time you create or recreate a Foundry
      connection for this tool: **ADD its redirect URL as a new Web entry, don't remove the
      old ones** — Entra allows multiple. Skipping this gives `AADSTS50011: redirect URI
      mismatch` on first sign-in, with the mismatched URL visible right in the error page.
      Known redirect URLs added so far:
      - `.../redirect/9e7a7a930b29482a9ff0291aaf0aa410` (first connection, pointed at old app1)
      - `.../redirect/6fe68cb71a2a4029b75a88132ac4d97e` (second connection, `iceber-mcp`)

## After both parts: verify before touching Foundry
- [x] **6. Get a real token and check it against our code, no deploy needed:**
      ```
      ! python3 scripts/get_entra_token.py
      ```
      Sign in via the printed URL/code. Then check the claims it prints:
      - `aud` must equal `api://1217683d-abaf-41ff-bbf7-c53a0a8814a9` (the server app)
      - `appid` must equal `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` (the client app)
      - note `ver`: if `1.0`, identity claims are limited to `email` (no
        `preferred_username`/`upn`) — this is the normal case for this tenant, not an error
      - if the token saved to a subdirectory (e.g. `scripts/.entra_token`) rather than the repo
        root, that's just because of the working directory the script ran from — either path
        is fine, pass it to `--token-file`
      Then, without deploying anything:
      ```
      ! python3 scripts/verify_real_token.py --token-file <path to the saved token>
      ```
      (export `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`, `ENTRA_ISSUER`, `USER_MAP` first — see
      values in build-plan.md's "app2" section). A `[PASS] Mapped to Cloudera user: 'ozarate'`
      line here means the whole chain works before any CML redeploy.

## Foundry fields
Copy-paste values: `docs/foundry-connection-fields.txt`. Authentication must be
**OAuth Identity Passthrough** (custom OAuth) — not "Microsoft Entra" or key-based, which are
shared identities and never carry a per-user token.

## Test app pattern (reuse this for Sofra too)
Keep a known-working app running while testing a risky change: don't edit it in place, deploy
a second one (`app1` = fixed test user, always up; `app2` = real token checking). Once the new
one is proven, decide whether to fold it back or keep both. Cheaper than debugging a broken
redeploy of the one app you rely on for demos.

## Where we are
1. Register the two apps — done (this file)
2. Claude adds user mapping and token scripts — done
3. Run the token script, verify locally — done, confirmed against a real token
4. Claude deploys `app2` with token checking, `app1` stays as-is — done, both 401 cases and
   the real-token case verified against the live app (see build-plan.md)
5. Finish the Foundry connection and test through the agent — next
