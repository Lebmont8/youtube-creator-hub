# YouTube Creator Hub

Connect your YouTube channels through Google OAuth to see channel and video analytics, log revenue manually, and queue private uploads. This self-hosted Flask app stores data in SQLite and protects local credentials with AES-GCM.

## Public source edition
Runtime code, synthetic offline tests and required UI assets only. No account database, OAuth client credentials, tokens, owner password, channel snapshots, personal finance, bootstrap envelope, deployment configuration or original Git history is included.

The dashboard and analytics were tested with authorized channels. End-to-end upload and processing have not been fully verified. The queue accepts private uploads only. Revenue entries are manual, not YouTube payout data.

## Requirements
Python 3.11+, FFmpeg/ffprobe for queued media validation, your Google Cloud YouTube Data/Analytics APIs and Web OAuth client. Use a trusted HTTPS reverse proxy; secure session cookies are not disabled.

~~~sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
export YOUTUBE_HUB_HOME="$PWD/.state"
export YOUTUBE_HUB_ORIGIN="https://localhost:8443"
export YOUTUBE_GOOGLE_PROJECT="your-google-project-id"
python configure.py --client /path/to/your-google-web-client.json
python app.py
~~~

Mount at /youtube-hub/ and register your exact HTTPS origin plus /youtube-hub/oauth/callback. Configure your own client locally with configure.py; it never prints credentials or the owner password. No existing production state directory should be reused.

## Verification
Offline tests use temporary stores and synthetic credentials. No real uploads, Google requests or payments are needed. See VERIFICATION.md for public-edition acceptance.

See LICENSE. Font licensing is preserved in the bundled asset directory.

## CI configuration
The GitHub Actions example is included at ci/github-actions.yml as a reference, not an active workflow. Run the documented verification commands locally. To enable Actions in your own repository, place this file under .github/workflows/.
