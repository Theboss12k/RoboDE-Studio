# Deploy RoboDE Studio (Google edition) on Cloud Run

You run these steps; nothing here has been run or tested against a real project (2026-11-21). The container is one
service: the Spring Boot API, the Python engine and the built web client. **Recommended: Gemini on Vertex AI.** A new
install on Cloud Run defaults every role to the `vertex_*` profiles, which call Vertex AI with the Cloud Run service
account's own token (no API key) and are billed to this Google Cloud project. Google Cloud credits apply to usage billed to
the project; check your credit terms to see which services your credits cover (nothing here asserts coverage). The Gemini
API key route still works and is optional (step 2). No key or token is baked into the image.

Replace `PROJECT_ID`, `REGION` (for example `asia-south1`) and the values marked `<...>`.

## 1. One-time setup

```bash
gcloud auth login
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com

# Let the Cloud Run service account call Vertex AI (the default is the Compute Engine service account)
PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format='value(projectNumber)')
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/aiplatform.user
gcloud artifacts repositories create studio --repository-format=docker --location=REGION
```

## 2. Secrets (Secret Manager)

The `gemini-api-key` secret is **optional** with Vertex AI: create it only if you also want the Gemini API profiles
(`gemini_*`); then keep `GEMINI_API_KEY=gemini-api-key:latest` in `--set-secrets` and in the loop below, otherwise drop
both. The Gemini key comes from <https://aistudio.google.com/apikey>. The studio token protects `/api/v1`; the server
refuses to listen on `0.0.0.0` without it. Use letters and digits only (the hex below is safe in a link and a cookie).

```bash
# optional (Gemini API profiles only):
# printf '%s' '<your Gemini API key>' | gcloud secrets create gemini-api-key --data-file=-
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create studio-token --data-file=-

PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format='value(projectNumber)')
for s in studio-token; do   # add gemini-api-key here if you created it
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

Vertex AI needs no secret; only `studio-token` is required.

The Java process reads `GEMINI_API_KEY` as the fallback key (a key saved in Settings wins over it) and sends it to the
Python engine only on stdin. The engine's own environment does not contain it.

## 3. Build the image (Cloud Build)

Run from the repository root (the folder with `Dockerfile`):

```bash
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/studio/robode-studio:latest
```

The build compiles the client (Node), the Java jar (Maven, tests skipped) and installs `requirements.txt` into a
Python venv. Expect several minutes; the CAD packages are large.

## 4. Deploy

```bash
gcloud run deploy robode-studio \
  --image REGION-docker.pkg.dev/PROJECT_ID/studio/robode-studio:latest \
  --region REGION \
  --memory 4Gi --cpu 2 \
  --timeout 3600 \
  --no-cpu-throttling \
  --min-instances 1 --max-instances 1 \
  --concurrency 20 \
  --set-secrets STUDIO_TOKEN=studio-token:latest \
  --set-env-vars GOOGLE_CLOUD_PROJECT=PROJECT_ID \
  --allow-unauthenticated
```

Why these settings:

| Setting | Reason |
|---|---|
| `--timeout 3600` | A design job can run for minutes; 3600 s is Cloud Run's maximum request timeout |
| `--no-cpu-throttling` | Jobs continue in the background between your polling requests; without it the CPU is throttled and they stall |
| `--min-instances 1` | Keeps the instance (and its running jobs and ephemeral files) alive |
| `--max-instances 1` | The project store is files on one disk (`runtime/`); two instances would not see each other's projects or jobs |
| `--memory 4Gi --cpu 2` | CadQuery, trimesh and matplotlib run in Python subprocesses next to the JVM; raise memory if a job is killed for memory |

`--allow-unauthenticated` only lets a browser reach the page. The API itself needs the studio token (step 5). If you
prefer Google sign-in in front, drop that flag and open the service with `gcloud run services proxy robode-studio
--region REGION` instead; the token is still needed.

## 5. Open it

```bash
TOKEN=$(gcloud secrets versions access latest --secret=studio-token)
URL=$(gcloud run services describe robode-studio --region REGION --format='value(status.url)')
echo "$URL/#token=$TOKEN"
```

Open that link once in your browser. The page stores the token in the browser's local storage, clears it from the
address bar and also sets it as a `studio_token` cookie, so API calls (Authorization header) and image, mesh and
download links (cookie) both work. Under **AI models** every role should show a "Vertex AI" profile. Use **Test** on a
profile to check the setup with one small real call (this is a billed Vertex AI call). A 403 means the API is not enabled or
the service account lacks `roles/aiplatform.user`; a 404 means the model is not available in that location (profiles
use the `global` location by default). Existing installs keep their saved roles: assign `vertex_pro`, `vertex_flash` and
`vertex_flash_lite` under Settings (or delete `runtime/llm/profiles.json`).

## Test Vertex AI on your own computer

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=PROJECT_ID
gcloud services enable aiplatform.googleapis.com
# start the studio as usual, then Settings -> a Vertex profile -> Test.
# Or: STUDIO_LLM_DEFAULT=vertex before the first start makes every role of a new install use Vertex.
```

Your own account needs `roles/aiplatform.user` on the project (owners already have it). The studio runs `gcloud` to get a
short-lived token; nothing is stored. Or add a model under Settings: "Gemini on Vertex AI (Google Cloud project, no key)".
No real call to Vertex AI has been made by the authors of this guide.

The Gemini CLI and Antigravity CLI profiles (`gemini_cli_*`, `antigravity_*`) do not work on Cloud Run: they need a Google sign-in on the machine that
runs the CLI, and the image does not contain the CLI. They are for running the studio on your own computer.

## 6. Where projects are stored

By default `runtime/` is `/data/runtime` inside the container: **ephemeral**. Projects, saved profiles, the usage log
and generated robots are lost when the instance restarts or is redeployed. Download the design package from a project
you want to keep.

To keep them, mount a Cloud Storage bucket (Cloud Run volume mount, second-generation execution environment):

```bash
gcloud storage buckets create gs://PROJECT_ID-studio-runtime --location=REGION
gcloud run services update robode-studio --region REGION \
  --add-volume name=runtime,type=cloud-storage,bucket=PROJECT_ID-studio-runtime \
  --add-volume-mount volume=runtime,mount-path=/data/runtime
```

The container runs as uid 10001. If it cannot write the mount, add the volume option `mount-options=uid=10001;gid=10001`
(check the current syntax in the Cloud Run "Cloud Storage volume mounts" documentation). The bucket is a file-system view
over object storage: it is slower than a disk, has no file locking and its renames are not atomic, so it suits one instance
(which is why `--max-instances 1`). This mount has not been tested with this studio's store; try a throwaway project first.

## 7. Update, logs, cost, remove

```bash
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/studio/robode-studio:latest
gcloud run deploy robode-studio --image REGION-docker.pkg.dev/PROJECT_ID/studio/robode-studio:latest --region REGION
gcloud run services logs read robode-studio --region REGION --limit 100
gcloud run services delete robode-studio --region REGION
```

With `--min-instances 1` and CPU always allocated the service is billed continuously; delete it or set
`--min-instances 0` when you are not using it (jobs then stop when the instance scales down). Vertex AI calls are billed
to this project (Gemini API key calls to the key's project) at Google's current prices; the **Usage** page shows token counts and estimates.
