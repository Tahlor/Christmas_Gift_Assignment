from pathlib import Path
import sys
import pickle
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from typing import List, Dict
from google_auth_oauthlib.flow import InstalledAppFlow

def create_service():
    creds = None
    token_path = Path('./credentials/token.pickle')
    # The file token.pickle stores the user's access and refresh tokens
    if token_path.exists():
        with token_path.open('rb') as token:
            creds = pickle.load(token)
                
    # If there are no (valid) credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_path = Path('./credentials/credentials.json')
            if not credentials_path.exists():
                print("""
Error: credentials.json not found!
To create one: https://console.cloud.google.com/apis/credentials?project=fit-drive-237820
                    """)
                sys.exit(1)
                    
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path),
                ['https://www.googleapis.com/auth/spreadsheets.readonly']
                )
            creds = flow.run_local_server(port=0)
                
        # Save the credentials for the next run
        with token_path.open('wb') as token:
            pickle.dump(creds, token)
    
    service = build('sheets', 'v4', credentials=creds)
    return service

def load_family_addresses(service, config):
    """Load family data from Google Sheet"""
    sheet = service.spreadsheets()
    
    result = sheet.values().get(
        spreadsheetId=config['spreadsheet']['id'],
        range=f"{config['spreadsheet']['sheet_name']}!A:T"
    ).execute()
    return result

# https://docs.google.com/spreadsheets/d/17CqgSA17lEpMjJBRsrsyb2EKMTTA66FcohAdacjuTok/edit?resourcekey=&gid=628606659#gid=628606659
# Timestamp	What are some things your family might want for Christmas?	Of which family are you a part?

def load_gift_preferences(service, config):
    """Load gift preferences from Google Form responses sheet if available
    
    Args:
        service: Google Sheets service object
        config: Configuration dictionary containing spreadsheet details
        
    Returns:
        Dict[str, List[str]]: Mapping of family names to their gift preferences

    """
    # Check if required config keys exist
    if not config.get('spreadsheet'):
        return {}
    
    spreadsheet_config = config['spreadsheet']
    if not (spreadsheet_config.get('form_responses_id') and 
            spreadsheet_config.get('form_responses_sheet')):
        return {}
    
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_config['form_responses_id'],
            range=f"{spreadsheet_config['form_responses_sheet']}!A:C"
        ).execute()
    except Exception as e:
        print(f"Warning: Could not load gift preferences: {e}")
        return {}
    
    if 'values' not in result:
        return {}
        
    headers = result['values'][0]
    rows = result['values'][1:]
    
    preferences = {}
    for row in rows:
        if len(row) < 3:
            continue
            
        timestamp = row[0]
        preference = row[1]
        family_name = row[2]
        
        if family_name not in preferences:
            preferences[family_name] = []
        preferences[family_name].append(preference)
    
    return preferences

