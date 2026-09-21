# Entra checklist: WORK FROM THIS FILE

(`entra-setup.md` is only background reading. Everything you need to do is here.)

Portal: https://entra.microsoft.com > Identity > Applications > App registrations > **All applications**

## DO THIS NOW, in this order
- [ ] **1. Turn on "Allow public client flows" on `iceberg-mcp-client`.** Details in section B3 below.
- [ ] **2. Confirm the Foundry redirect URL is on `iceberg-mcp-client` as a Web redirect.** Details in section B5 below.
- [ ] **3. Run the token script** in the Cloudera AI prompt:
      `! python3 scripts/get_entra_token.py`
      Open the URL it prints, enter the code, sign in as yourself. It should end with
      "Saved token to .entra_token" and a list of claims.
- [ ] **4. Send the printed claims to Claude** (nothing secret in them). Claude then switches `app1`
      to token checking. Do not test through Foundry until Claude says so.

## Values (all verified except where noted)
| Item | Value |
|---|---|
| Tenant ID (verified with Microsoft) | `650a1000-e5e3-40bf-97a9-d62002a0934b` |
| `iceberg-mcp-server` Application (client) ID | `1217683d-abaf-41ff-bbf7-c53a0a8814a9` (deduced; a real token's `aud` will confirm) |
| `iceberg-mcp-client` Application (client) ID | `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` |
| Foundry redirect URL | `https://global.consent.azure-apim.net/redirect/9e7a7a930b29482a9ff0291aaf0aa410` |
| Not app registrations, do not use | `30fac747-...` (your user ID), `3d27c83f-...` ("Entra agent identity") |

## Why two apps
| App | Job | Foundry field it feeds |
|---|---|---|
| `iceberg-mcp-server` (the API) | The token's audience | **Scopes** |
| `iceberg-mcp-client` | What Foundry (and our token script) signs in through. Has the secret. | **Client ID**, **Client secret** |

## Part A: `iceberg-mcp-server`  (you reported these as done)
- [x] **A1. Register.** Single tenant, no redirect URI.
- [x] **A2. Expose an API.** Application ID URI `api://<client-id>`; scope `access_as_user` (Admins and users, Enabled).
- [x] **A3. Token version 2.** Manifest, `api` block: `"requestedAccessTokenVersion": 2`
      (older manifest format: `"accessTokenAcceptedVersion": 2`).

## Part B: `iceberg-mcp-client`
- [x] **B1. Register.** Single tenant.
- [x] **B2. API permission.** API permissions > Add > `iceberg-mcp-server` > Delegated > `access_as_user`.
- [x] **B2b. Admin consent granted** (status column green).
- [ ] **B3. ALLOW PUBLIC CLIENT FLOWS = Yes.  <- NOT DONE YET, this is what is blocking the token script.**
      Our token script logs in as a "public client". Entra rejects that unless this switch is on.
      On `iceberg-mcp-client`, use the first way that works for you:

      **Way 1 (newer portal):**
      1. Left menu > **Authentication**.
      2. Across the top of the page there are tabs. Click the **Settings** tab.
      3. Find **Allow public client flows** ("Enable the following mobile and desktop flows").
      4. Set it to **Yes** / **Enabled**, then **Save**.

      **Way 2 (older portal):** left menu > **Authentication** > scroll to the bottom to the section
      **Advanced settings** > **Allow public client flows** > **Yes** > **Save**.

      **Way 3 (if you see no such setting): edit the manifest.** Left menu > **Manifest**.
      - If there are two tabs, use **Microsoft Graph App Manifest**. Find `"isFallbackPublicClient"`
        and change `false` to `true`.
      - In the older format the field is `"allowPublicClient"`: change `null` or `false` to `true`.
      Click **Save**. (These field names are from general knowledge; tell Claude if neither exists.)

      Check: reopen the setting and confirm it now says Yes.
- [x] **B4. Client secret created** and pasted into Foundry. Never paste it in chat, docs or git.
- [ ] **B5. Foundry redirect URL added as a WEB redirect.**
      1. `iceberg-mcp-client` > **Authentication**.
      2. Open the **Redirect URI configuration** tab (or the "Platform configurations" list in the older layout).
      3. If nothing is listed: **+ Add a platform** (or **+ Add Redirect URI**) > **Web**.
      4. Paste: `https://global.consent.azure-apim.net/redirect/9e7a7a930b29482a9ff0291aaf0aa410`
      5. **Configure**, then **Save**.
      Check that it appears under **Web**, not "Single-page application" or "Mobile and desktop".
      (This is for Foundry's sign-in. The token script does not use it.)

## Foundry fields
Copy-paste values are in `docs/foundry-connection-fields.txt`. Authentication must be
**OAuth Identity Passthrough** (custom OAuth), not "Microsoft Entra" or key-based: those are shared
identities, so the server would never see who is asking.

## Do not do yet
Do not test through Foundry until Claude has switched `app1` from its fixed test user to real token
checking. Until then `app1` accepts anything, so a working chat proves nothing.

## Where we are
1. Register the two apps: done, except B3 and B5 above
2. Claude adds user mapping and the token script: done
3. You run the token script and send Claude the claims  <- you are here
4. Claude switches `app1` to token checking
5. You test through the Foundry agent
