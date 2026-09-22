# Entra background (read-only reference)

> **To do the work, use `entra-app-registration-checklist.md`.** This file has no steps to
> execute and deliberately uses no step numbers, so it can't drift out of sync with the
> checklist's numbering again.

## Why two app registrations
An MCP server behind OAuth needs two separate Entra apps:
- **The API app** (`iceberg-mcp-server`): defines what a token "for this server" means. Its
  Application ID URI (`api://<id>`) is the audience our server checks tokens against.
- **The client app** (`iceberg-mcp-client`): what the user actually signs in through — Foundry,
  or our `get_entra_token.py` test script. It holds the client secret and the redirect URI.

Confusing the two is the single most common mistake (see the checklist's "what tripped us up"
section) because their names differ by one word and their Overview pages look identical.

## Why OAuth Identity Passthrough, not "Microsoft Entra" or key-based
Foundry's MCP tool connector offers four auth methods. Per Microsoft's docs
(https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication):

| Method | Whose identity the MCP server sees | Per-user? |
|---|---|---|
| Key-based | One shared static credential | No |
| Microsoft Entra (agent identity / project managed identity) | The agent or project itself | No |
| **OAuth Identity Passthrough** | Each signed-in user | **Yes** |
| Unauthenticated | Nobody | No |

The whole point of this project is per-user audit attribution, so only OAuth Identity
Passthrough works. "Microsoft Entra" sounds closer to what we want but issues a token for the
*agent*, with no user in it — our server would (correctly) reject it as unmappable.

Custom OAuth (bring-your-own app registration) is required rather than Foundry's managed
OAuth, because Microsoft's managed OAuth restricts tokens scoped to a Microsoft-owned audience
from being sent to third-party MCP servers. Our server needs to be its own audience.

## Token version: expect v1 from a personal tenant
Setting the server app's manifest to `requestedAccessTokenVersion: 2` is supposed to make
Entra issue v2 access tokens (`iss: https://login.microsoftonline.com/<tenant>/v2.0`, with
`preferred_username`/`upn` claims). On the personal/trial tenant used for this POC, tokens
still came back as v1 (`iss: https://sts.windows.net/<tenant>/`, `ver: 1.0`) after that setting
was saved. v1 tokens carry `email` but not `preferred_username`/`upn`.

Our server handles both: `ENTRA_ISSUER` can be set to either form, and `USER_MAP` lets you map
an arbitrary identity claim value to a Cloudera username when the local-part-of-email
convention doesn't produce the right name (e.g. a personal `@ymail.com` address). Don't assume
which version a given tenant will issue — check with `scripts/verify_real_token.py` against a
real token before configuring anything.

Corporate tenants (e.g. Sofra's) may behave differently — check `ver` on one of their real
tokens rather than assuming either outcome.

## Identity mapping strategy
Two independent options, both implemented in `identity.py`:
- **Local-part-of-email** (default): `ozarate@corp.com` -> `ozarate`. Works when the token
  identity's local part already matches the Cloudera username.
- **`USER_MAP`**: explicit `identity=cloudera_user` pairs. Required when it doesn't (personal
  accounts, different naming conventions). When set, it's exclusive — an identity not listed
  is rejected, with no local-part fallback.

`ALLOWED_USERS` is a further restriction on top of either: a mapped user not in the list is
still rejected.

## Values used in this POC
| Item | Value |
|---|---|
| Tenant ID | `650a1000-e5e3-40bf-97a9-d62002a0934b` |
| `iceberg-mcp-server` client ID | `1217683d-abaf-41ff-bbf7-c53a0a8814a9` |
| `iceberg-mcp-client` client ID | `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` |
| Scope | `api://1217683d-abaf-41ff-bbf7-c53a0a8814a9/access_as_user` |

## Reusing this for Sofra's tenant
Same two-app pattern, different tenant. Differences to expect:
- Real corporate users instead of a personal-tenant self-registration — no need to invent a
  `USER_MAP` entry if their email local part already matches Cloudera usernames.
- Their tenant may honor v2 tokens where this one didn't; check `ver`, don't assume.
- An actual Entra admin may be needed for admin consent (B2b) if you aren't one there.
- The Foundry project must be in the same tenant as the signed-in users (cross-tenant token
  exchange isn't supported), and users need at least the **Foundry Agent Consumer** role.

Source: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication
