# Entra app registration checklist

Do this in order. Tick each box as you go. Full background is in `entra-setup.md`; this file
is the short version to work from. Nothing here is secret except the client secret (B4),
which goes only into Foundry.

Portal: https://entra.microsoft.com > Identity > Applications > App registrations

## Why two apps
| App | Job | Foundry field it feeds |
|---|---|---|
| `iceberg-mcp-server` (the API) | Defines what a token "for the MCP server" is. The token's audience. | **Scopes** |
| `iceberg-mcp-client` | The app Foundry signs users in through. Has the secret. | **Client ID**, **Client secret** |

Keep them straight: **Client ID = client app. Scopes = server app.**

## Values so far (fill in as you go)
| Item | Value |
|---|---|
| Tenant ID (verified) | `650a1000-e5e3-40bf-97a9-d62002a0934b` |
| `iceberg-mcp-server` Application (client) ID | `1217683d-abaf-41ff-bbf7-c53a0a8814a9` (deduced, confirm via the `aud` claim of a real token) |
| `iceberg-mcp-client` Application (client) ID | `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` |
| Rejected: `30fac747-...` (your user object ID), `3d27c83f-...` ("Entra agent identity") | not app registrations, do not use |

## Part A: `iceberg-mcp-server` (the API)
- [ ] **A1. Register.** New registration. Name `iceberg-mcp-server`. **Single tenant only**.
      Redirect URI blank. Register. Copy the **Application (client) ID** into the table.
- [ ] **A2. Expose an API.** Manage > Expose an API.
  - [ ] Application ID URI > Add > keep default `api://<client-id>` > Save
  - [ ] + Add a scope: name `access_as_user`, who can consent **Admins and users**,
        display name `Access the Iceberg MCP server`, description
        `Access the Iceberg MCP server as the signed-in user`, state **Enabled** > Add scope
- [ ] **A3. Token version 2.** Manage > Manifest. In the `api` block set
      `"requestedAccessTokenVersion": 2` (older manifest format: `"accessTokenAcceptedVersion": 2`).
      Save.

## Part B: `iceberg-mcp-client`
- [ ] **B1. Register.** New registration. Name `iceberg-mcp-client`. **Single tenant only**.
      Redirect URI blank for now. Register. Copy the **Application (client) ID** into the table.
- [ ] **B2. Permission to call the API.** Manage > API permissions > + Add a permission >
      "APIs my organization uses" (or "My APIs") > `iceberg-mcp-server` > Delegated permissions >
      tick `access_as_user` > Add permissions.
- [ ] **B2b. Grant admin consent** for your tenant (button on the same page). Status turns green.
- [ ] **B3. Public client flows.** Manage > Authentication (or Settings tab) >
      **Allow public client flows = Yes** > Save.
- [ ] **B4. Client secret.** Manage > Certificates & secrets > New client secret > 6 months >
      Add. Copy the **Value** (not the Secret ID) right away. It is shown once.
      Keep it for Foundry. Do not paste it in chat, docs or git.

## Send back to Claude
- [ ] `iceberg-mcp-server` Application (client) ID
- [ ] `iceberg-mcp-client` Application (client) ID
- [ ] Did admin consent turn green? Did the manifest save?

## Then: Foundry OAuth fields
Foundry > Tools > add MCP tool > Authentication: **OAuth Identity Passthrough** (custom OAuth).
Do not pick "Microsoft Entra" or key-based: they are shared identities, so the server would not
see who is asking.

| Foundry field | Value |
|---|---|
| Client ID | `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` |
| Client secret | the secret from B4 |
| Auth URL | `https://login.microsoftonline.com/650a1000-e5e3-40bf-97a9-d62002a0934b/oauth2/v2.0/authorize` |
| Token URL | `https://login.microsoftonline.com/650a1000-e5e3-40bf-97a9-d62002a0934b/oauth2/v2.0/token` |
| Refresh URL | same as Token URL |
| Scopes | `api://1217683d-abaf-41ff-bbf7-c53a0a8814a9/access_as_user offline_access` (one space, no comma) |

After Connect, Foundry shows a **redirect URL**. Add it to `iceberg-mcp-client`:
Authentication > Add a platform > Web > paste it.

## Do not do yet
Do not finish the Foundry connection until Claude has switched `app1` from its fixed test user
to real token checking. Until then `app1` accepts anything, so a working call proves nothing.

## Where we are after this
1. Register the two apps (this file)
2. Claude adds the user-mapping setting and a token test script, tests a real token
3. Claude switches `app1` to token checking
4. You finish the Foundry connection, then test through the agent
