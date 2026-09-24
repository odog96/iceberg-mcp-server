# Add test users in Azure

## What this is for
We want the server to show three different people as three different Cloudera users. To test that
without real employees, we create fake accounts in your Azure tenant. **Entra** is Microsoft's login
system, and a "user" there is just a login account. Portal labels can differ slightly from what is
written here (they change over time); if something is missing, send a screenshot.

## Accounts to create
| Display name | Username (the part before the @) | Purpose |
|---|---|---|
| Joao (test) | `joao` | test user |
| Fernando (test) | `fernando` | test user |
| Jimmy (test) | `jimmy` | test user |
| Outsider (test) | `outsider` | **optional but recommended:** a person NOT on our list. The server should refuse them. |

## Part 1: create each account (about 2 minutes each)
1. Go to https://entra.microsoft.com and sign in.
2. Left menu: **Entra ID** > **Users** > **All users**.
3. Click **+ New user** > **Create new user**.
4. On the **Basics** tab:
   - **User principal name:** the username from the table (for example `joao`). Leave the domain
     next to it on your default (it ends in `.onmicrosoft.com`).
   - **Mail nickname:** leave "Derive from user principal name" ticked.
   - **Display name:** for example `Joao (test)`.
   - **Password:** tick **Auto-generate password**.
   - **Account enabled:** ticked.
5. **Before you click Create, copy the password** (click the eye icon to show it, then the copy icon).
   It is shown only now. Save it in `/home/cdsw/azure-creds-testusers.txt`, which git ignores. Never
   paste passwords into chat or into a file that git tracks.
6. Click **Review + create**, then **Create**.
7. Repeat for the other accounts.

## Part 2: sign in once as each user (about 3 minutes each)
This proves each account works and shows what Microsoft puts in its token. Run these from the
project folder `/home/cdsw`, once per user, changing the name each time:

```
! python3 scripts/get_entra_token.py --out .entra_token_joao
```

1. The script prints a web address and a code.
2. Open the address in a **private (incognito) window**, so Microsoft does not reuse your own login.
3. Enter the code, then sign in as `joao@<your-domain>.onmicrosoft.com` with the temporary password.
4. Microsoft makes you choose a new password. Do, and save it in the same ignored file.
5. If it says "More information required", choose **Skip for now** if that option appears. If there
   is no skip option, see "If Microsoft insists on extra security" below.
6. Approve. The script then prints the token's details.

Send me each printout (nothing secret is in it). Use `fernando`, `jimmy` and `outsider` for the others,
for example `--out .entra_token_fernando`. Those files are ignored by git.

## If Microsoft insists on extra security
New tenants usually have "security defaults" switched on, which can force each account to set up an
authenticator app. For this throwaway test tenant you can switch it off: **Entra ID** > **Overview** >
**Properties** > **Manage security defaults** > **Disabled** > choose a reason > **Save**.
Only do this in a test tenant, and switch it back on afterwards. It lowers protection for every
account in the tenant, including yours.

## Part 3 (optional): also test through the Foundry chat
Do this only after Part 2 works. Each person needs permission to use your agent:
1. Azure portal (https://portal.azure.com) > your Foundry project > **Access control (IAM)**.
2. **+ Add** > **Add role assignment**.
3. Role: **Foundry Agent Consumer** (the older name is **Azure AI User**) > **Next**.
4. **+ Select members**, pick the test users > **Review + assign**.
5. For each person, in a private window: sign in to the Foundry portal as that person, open the
   agent, ask a question, and click the **Sign in** link when it appears. Each person sees that
   prompt once.

## What happens next
Once you send me the printouts and tell me which Cloudera user each person is, I will:
1. add all of them to the server's allowed list and redeploy,
2. run `whoami` and a query as each one,
3. check that Impala's own log shows the right Cloudera user for each person,
4. confirm `outsider` is refused.
