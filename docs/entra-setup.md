# Entra setup for the MCP server (runbook)

Purpose: get real Entra ID tokens that the MCP server can validate, in a test tenant first
and then in Sofra's tenant. The same steps apply to both; only the IDs and domain change.

Portal: https://entra.microsoft.com (or Azure portal > Microsoft Entra ID).
Portal labels drift between releases. Items marked **(verify)** are from general knowledge
and have not been checked against a live tenant. Client secrets: the Foundry connection (section 9) needs one. It is entered only into
Foundry, never pasted into chat, docs or git.

## 0. Identify the tenant
Overview page: note **Tenant ID** and **Primary domain** (`<name>.onmicrosoft.com`).

## 1. Create test users (skip in Sofra's tenant: use their real users)
Identity > Users > All users > New user > Create new user. Create one per Cloudera user:
`ozarate`, `jgaragorry`, `fcobo`, `jcaseiro`.
- User principal name: `<cloudera-username>@<primary-domain>`. The part before `@` must
  equal the Cloudera username exactly, in lowercase: the server maps `jcaseiro@x` to `jcaseiro`.
- Auto-generate the password and keep it privately; do not paste it into chat or commit it.
- Account enabled: yes.
- Heads-up: users must change the password at first sign-in, and a new tenant usually has
  **security defaults** on, which forces MFA registration at first sign-in. For a throwaway
  test tenant you may turn that off (Identity > Overview > Properties > Manage security
  defaults). Do not do this in a real tenant.

## 2. Register the MCP server (the API)
Identity > Applications > App registrations > New registration.
- Name: `iceberg-mcp-server`
- Supported account types: **Single tenant** (this directory only)
- Redirect URI: leave empty
Register, then copy **Application (client) ID** from the overview page.

## 3. Expose an API
On that app: Manage > Expose an API.
- Application ID URI: Add > accept the default `api://<client-id>` > Save.
- Add a scope: name `access_as_user`, who can consent: **Admins and users**, display name
  and description e.g. "Access the Iceberg MCP server as the signed-in user", state
  **Enabled**.

## 4. Make it issue v2 tokens
Manage > Manifest. Set the access token version to 2 and save.
- Microsoft Graph manifest format: `"api": { "requestedAccessTokenVersion": 2 }`
- Older AAD Graph format: `"accessTokenAcceptedVersion": 2`
This decides the `iss` and `aud` formats. The server's default expects v2. **(verify)**

## 5. Optional claims
Manage > Token configuration > Add optional claim > token type **Access** > tick `email`
and `upn` > Add (accept the prompt to enable the Graph email permission if shown).
`preferred_username` should already be present in v2 tokens. **(verify)** The server tries
`preferred_username`, then `upn`, then `email`.

## 6. Register the client (used by Foundry, and by our test script)
New registration, name `iceberg-mcp-client`, single tenant. Then:
- Certificates & secrets > New client secret. Copy the value once, straight into Foundry
  (section 9). Note its expiry date.
- Authentication (or its Settings tab) > **Allow public client flows: Yes** > Save.
  This also enables the device-code login used by `scripts/get_entra_token.py`, which lets
  us test the server with a real token before involving Foundry.
- Section 9 later adds a Web redirect URI that Foundry gives you.
- API permissions > Add a permission > My APIs > `iceberg-mcp-server` > Delegated
  permissions > tick `access_as_user` > Add.
- Click **Grant admin consent for <tenant>** so users are not prompted.
Copy this app's **Application (client) ID**.

## 7. Values to hand over (none are secrets)
| Value | Where it comes from | Used as |
|---|---|---|
| Tenant ID | step 0 | `ENTRA_TENANT_ID` |
| MCP server client ID | step 2 | basis for `ENTRA_AUDIENCE` (confirm against a decoded token) |
| Scope | `api://<mcp-server-client-id>/access_as_user` | requested by the token script |
| Client app ID | step 6 | Foundry "Client ID" and the token script |
| Primary domain | step 0 | test user names |

## 8. Verify
Get a token with `scripts/get_entra_token.py`, then decode it with
`scripts/call_mcp.py --claims`. Compare against the server's settings:
- `iss` equals `https://login.microsoftonline.com/<tenant-id>/v2.0`
  (v1 tokens use `https://sts.windows.net/<tenant-id>/`: set `ENTRA_ISSUER` if so)
- `aud` equals `ENTRA_AUDIENCE` (often the client ID GUID on v2, not the `api://` URI)
- `preferred_username` (or `upn`/`email`) is present and its local part is the Cloudera user
- `ver` is `2.0`

## 9. Connect Foundry (custom OAuth identity passthrough)
Foundry portal > Tools > add Model Context Protocol tool. Authentication: **OAuth Identity
Passthrough** > custom OAuth. Do NOT pick "Microsoft Entra" (agent identity / project managed
identity) or key-based: per Microsoft's docs those are shared identities where the user context
does not persist, so the server would never see who is asking.

| Foundry field | Value |
|---|---|
| Remote MCP Server endpoint | `https://<app-subdomain>.<cdsw-domain>/mcp` |
| Client ID | client app ID (step 6) |
| Client secret | the secret from step 6 |
| Auth URL | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize` |
| Token URL | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
| Refresh URL | same as Token URL |
| Scopes | `api://<mcp-server-client-id>/access_as_user offline_access` (single space, no comma) |

After connecting, Foundry shows a **redirect URL**. Add it to the client app: Authentication >
Add a platform > Web > paste it.

Constraints from Microsoft's docs:
- The signed-in user's Entra tenant must match the Foundry project's tenant.
- Users need at least the **Foundry Agent Consumer** role on the project.
- The MCP server's audience must be one you control (our own app registration), which is why
  custom OAuth is required.
- Each user is prompted once per tool to consent (`oauth_consent_request`).

Source: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication
