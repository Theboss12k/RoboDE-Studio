#!/usr/bin/env bash
# Creates the RoboDE Studio VM on Google Cloud. Run from the repository root AFTER billing is enabled on the project:
#   bash deploy/vm/create.sh
# Safe to re-run: every step checks first. STUDIO_TOKEN=<value> reuses a token; otherwise one is generated.
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[ -n "$PROJECT" ] || { echo "Set PROJECT=<your project id> or run: gcloud config set project <id>" >&2; exit 1; }
REGION="${REGION:-asia-south1}"
ZONE="${ZONE:-asia-south1-a}"
VM="${VM:-robode-studio}"
MACHINE="${MACHINE:-e2-standard-4}"
DISK_GB="${DISK_GB:-60}"
SA_NAME="robode-studio-vm"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
IP_NAME="robode-studio-ip"
TAG="robode-studio"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

g() { gcloud --project "$PROJECT" "$@"; }

echo "== APIs"
g services enable compute.googleapis.com aiplatform.googleapis.com iam.googleapis.com

echo "== Service account"
g iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || g iam service-accounts create "$SA_NAME" --display-name "RoboDE Studio VM"
for role in roles/aiplatform.user roles/logging.logWriter; do
  g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --condition=None >/dev/null
done

echo "== Static IP"
g compute addresses describe "$IP_NAME" --region "$REGION" >/dev/null 2>&1 \
  || g compute addresses create "$IP_NAME" --region "$REGION"
IP="$(g compute addresses describe "$IP_NAME" --region "$REGION" --format='value(address)')"

echo "== Firewall"
g compute firewall-rules describe robode-studio-web >/dev/null 2>&1 \
  || g compute firewall-rules create robode-studio-web --network default --direction INGRESS \
       --allow tcp:80,tcp:443 --source-ranges 0.0.0.0/0 --target-tags "$TAG"
g compute firewall-rules describe robode-studio-iap-ssh >/dev/null 2>&1 \
  || g compute firewall-rules create robode-studio-iap-ssh --network default --direction INGRESS \
       --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags "$TAG"

echo "== Instance"
if g compute instances describe "$VM" --zone "$ZONE" >/dev/null 2>&1; then
  echo "Instance $VM already exists; leaving it as is (see deploy/vm/README.md to update)."
else
  TOKEN="${STUDIO_TOKEN:-$(openssl rand -hex 20)}"   # 40 hex characters
  g compute instances create "$VM" --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced \
    --service-account "$SA" --scopes cloud-platform \
    --tags "$TAG" --address "$IP" \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata "studio-token=$TOKEN" \
    --metadata-from-file "startup-script=$HERE/startup.sh"
fi

HOST="${IP//./-}.sslip.io"
echo
echo "Created. The first boot installs and builds everything (about 10 to 20 minutes)."
echo "Watch:  gcloud compute ssh $VM --zone $ZONE --project $PROJECT --tunnel-through-iap -- tail -f /var/log/robode-setup.log"
echo "URL:    https://$HOST/"
echo "Token:  gcloud compute instances describe $VM --zone $ZONE --project $PROJECT --format='value(metadata.items.filter(key:studio-token).extract(value).flatten())'"
echo "Open https://$HOST/#token=<token> once in your browser."
