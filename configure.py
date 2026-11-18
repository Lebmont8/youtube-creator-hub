"""Configure your own local credentials without printing secrets."""
import argparse,getpass,json
from pathlib import Path
from urllib.parse import urlsplit
from core import Store,ORIGIN,PROJECT
def main():
 p=argparse.ArgumentParser();p.add_argument("--client",type=Path,required=True)
 a=p.parse_args()
 if urlsplit(ORIGIN).scheme!="https":raise SystemExit("YOUTUBE_HUB_ORIGIN must use HTTPS")
 web=json.loads(a.client.read_text()).get("web",{})
 if not web.get("client_id","").endswith(".apps.googleusercontent.com") or not web.get("client_secret"):raise SystemExit("Google Web OAuth client required")
 if web.get("project_id")!=PROJECT:raise SystemExit("Set YOUTUBE_GOOGLE_PROJECT to your client's project")
 if web.get("token_uri")!="https://oauth2.googleapis.com/token":raise SystemExit("Unexpected token endpoint")
 if ORIGIN+"/youtube-hub/oauth/callback" not in web.get("redirect_uris",[]):raise SystemExit("Register the exact HTTPS callback")
 password=getpass.getpass("New owner password (32+ characters): ")
 if len(password)<32 or password!=getpass.getpass("Repeat password: "):raise SystemExit("Password confirmation failed")
 store=Store()
 if store.configured():raise SystemExit("Existing configuration is not overwritten")
 store.set_password(password);store.set_setting("oauth",web);store.set_setting("origin",ORIGIN)
 print("Configured locally; secret values not displayed.")
if __name__=="__main__":main()
