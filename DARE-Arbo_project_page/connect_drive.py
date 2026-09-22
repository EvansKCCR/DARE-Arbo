"""One-time, local owner consent. Never prints credentials."""
import argparse
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow


def main():
    parser = argparse.ArgumentParser(description='Authorize the project folder owner locally.')
    parser.add_argument('client_file', type=Path, help='Google Desktop OAuth client JSON')
    parser.add_argument('--no-browser', action='store_true', help='Print the consent URL for opening in a selected browser')
    parser.add_argument('--browser', help='Browser command passed to the local OAuth helper')
    args = parser.parse_args()
    destination = Path(__file__).parent / 'owner_credentials.toml'
    if destination.exists():
        raise SystemExit('owner_credentials.toml already exists. Move it to secure storage before reconnecting.')
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_file),
        scopes=['https://www.googleapis.com/auth/drive'])
    credentials = flow.run_local_server(port=0, access_type='offline', prompt='consent',
        open_browser=not args.no_browser,
        browser=args.browser,
        authorization_prompt_message='{url}' if args.no_browser else 'Complete owner consent in the browser.',
        success_message='Drive connected. You can close this tab.')
    if not credentials.refresh_token:
        raise SystemExit('No refresh token returned. Revoke the prior grant and retry.')
    with destination.open('x', encoding='utf-8') as output:
        output.write('[drive]\n')
        for key in ('client_id', 'client_secret', 'refresh_token'):
            output.write(f'{key} = {json.dumps(getattr(credentials, key))}\n')
    print('Credentials saved privately to owner_credentials.toml. Move its [drive] section into .streamlit/secrets.toml, then remove the temporary credential file.')


if __name__ == '__main__':
    main()
