# Verification
Verified on 2026-11-18, Python 3.12.
- python -m unittest test_uploader test_dashboard -v: 55 tests passed.
- Tests use temporary state, synthetic credentials and fake provider responses; verification ran with network isolated.
- Static assets were explicitly inspected; user audio and operational media are not shipped.
- Historical dashboard/read-side acceptance is not a claim that full real-video upload/processing has passed. End-to-end real upload acceptance is still pending; queue targets remain private-only.
- Matching existing Python dependencies were reused read-only for checks. The committed requirements.lock is the installation input; a clean dependency reinstall was not separately accepted.

## CI configuration
The GitHub Actions example is included at ci/github-actions.yml as a reference, not an active workflow. Run the documented verification commands locally. To enable Actions in your own repository, place this file under .github/workflows/.
