# RoboDE Studio on a Compute Engine VM

Nothing here has been run against a real project (written 2026-11-21). The whole app (Spring Boot API, Python engine,
built web client) runs on one CPU VM. Every LLM role uses Gemini on Vertex AI through the VM's service account: no API keys.
Caddy terminates HTTPS (automatic certificate for `<ip-with-dashes>.sslip.io`) and proxies to the app on `127.0.0.1:8800`.
Studio's bearer token is required for every `/api/v1` call.

## What `create.sh` creates (your project `$PROJECT`, zone `asia-south1-a`)

| Resource | Name | Notes |
|---|---|---|
| APIs | compute, aiplatform, iam | enabled |
| Service account | `robode-studio-vm` | `roles/aiplatform.user`, `roles/logging.logWriter` |
| Static IP | `robode-studio-ip` | regional, asia-south1 |
| Firewall | `robode-studio-web` | tcp 80, 443 from anywhere to tag `robode-studio` |
| Firewall | `robode-studio-iap-ssh` | tcp 22 from 35.235.240.0/20 (IAP) only |
| VM | `robode-studio` | e2-standard-4 (4 vCPU, 16 GB), Ubuntu 24.04 LTS, 60 GB pd-balanced, shielded VM, no GPU |
| Metadata | `studio-token` | the access token (random 40 characters), plus the startup script |

Billing must be enabled on the project first. Set a budget alert: see `budget.md`.

## Deploy

```bash
bash deploy/vm/create.sh        # from the repository root; prints the URL and how to read the token
```

The first boot installs JDK 25, Python 3.12, Node 22, Caddy, builds the app and starts it (10 to 20 minutes; follow
`/var/log/robode-setup.log`). Then open `https://<ip-with-dashes>.sslip.io/#token=<token>` once. The page stores the token in the
browser, removes it from the address bar and sets a `studio_token` cookie (path `/api/v1`, Secure, SameSite=Strict) so
that image, mesh, PDF and export links work; API calls send `Authorization: Bearer`. Cookies are only accepted for
GET/HEAD; writes always need the header. (This was already built into the web client; no code change was needed.)
Read the token back:

```bash
gcloud compute instances describe robode-studio --zone asia-south1-a --project "$PROJECT" \
  --format='value(metadata.items.filter(key:studio-token).extract(value).flatten())'
```

First install writes `runtime/llm/profiles.json` with every role on a `vertex_*` profile (pro roles on `vertex_pro`,
flash roles on `vertex_flash`, object detection on `vertex_flash_lite`), project from the metadata server. It is never
overwritten afterwards, so changes made in Settings survive updates. Use Settings, AI models, **Test** for one real (billed)
call. A 403 means billing or the Vertex AI API is not enabled, or the service account lacks `roles/aiplatform.user`.

## Update

Pulls `main` (fast-forward), rebuilds and restarts. Run over IAP SSH:

```bash
gcloud compute ssh robode-studio --zone asia-south1-a --project "$PROJECT" --tunnel-through-iap
sudo google_metadata_script_runner startup
```

(Rebooting does the same.) The script is idempotent. To change the startup script itself:
`gcloud compute instances add-metadata robode-studio --zone asia-south1-a --metadata-from-file startup-script=deploy/vm/startup.sh`.

## Logs and operations

```bash
sudo journalctl -u robode-studio -f      # the app
sudo journalctl -u caddy -f              # HTTPS / certificate
sudo tail -f /var/log/robode-setup.log   # install and build
sudo systemctl restart robode-studio
```

Projects and settings live in `/var/lib/robode-studio/runtime` on the boot disk (survives stop/start and reboot, lost on delete).
Back up by downloading design packages, or snapshot the disk.

## Save money

A stopped VM costs only its disk and reserved address:

```bash
gcloud compute instances stop robode-studio --zone asia-south1-a --project "$PROJECT"
gcloud compute instances start robode-studio --zone asia-south1-a --project "$PROJECT"
```

The address is static, so the URL is unchanged. After a start the startup script runs again and pulls updates.

## Rotate the token

```bash
NEW=$(openssl rand -hex 20)
gcloud compute instances add-metadata robode-studio --zone asia-south1-a --project "$PROJECT" --metadata "studio-token=$NEW"
gcloud compute ssh robode-studio --zone asia-south1-a --project "$PROJECT" --tunnel-through-iap -- sudo google_metadata_script_runner startup
```

Then open the new `#token=` link in each browser.

## Cost estimate (rough, USD per month, on-demand; check the pricing calculator, prices change)

| Item | Estimate |
|---|---|
| e2-standard-4, asia-south1, running 24/7 | about 120 |
| 60 GB pd-balanced | about 7 |
| Static IP while attached to a running VM | about 3 |
| Egress, logging | a few |
| Vertex AI (Gemini) | by usage; the Usage page in Studio shows tokens and estimates |

Roughly 130 USD a month if left on; about 10 when stopped. Sustained-use or committed-use discounts and credits may change this.

## Delete everything

```bash
gcloud compute instances delete robode-studio --zone asia-south1-a --project "$PROJECT"
gcloud compute addresses delete robode-studio-ip --region asia-south1 --project "$PROJECT"
gcloud compute firewall-rules delete robode-studio-web robode-studio-iap-ssh --project "$PROJECT"
gcloud iam service-accounts delete robode-studio-vm@$PROJECT.iam.gserviceaccount.com --project "$PROJECT"
```

## Notes

- `sslip.io` is a public wildcard DNS service that maps `34-1-2-3.sslip.io` to 34.1.2.3; Let's Encrypt must be reachable on
  port 80 and 443 for the certificate. The host name changes only if you release the static IP.
- The Gemini CLI and Antigravity CLI profiles need a Google sign-in on the machine and do not work here; use the `vertex_*` profiles.
- The Vertex adapter detects Compute Engine through the DMI product name ("Google Compute Engine") as well as Cloud Run
  variables, and the service sets `STUDIO_LLM_DEFAULT=vertex` and `GOOGLE_CLOUD_PROJECT`.
- Not validated: nothing in this folder has run on a real VM; startup.sh was syntax-checked only.
