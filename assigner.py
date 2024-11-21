import random
from typing import Dict, List, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
from pathlib import Path
from random import shuffle, seed, choice
from datetime import datetime
from general_tools.my_email import email as send_email
import sys
import yaml
import argparse
from assignment_algorithms import best_algorithm, validate_assignments

class FamilyGiftExchange:
    def __init__(self, config_path: str, test_mode: bool = False):        
        self.family_addresses: Dict[str, str] = {}
        self.email_to_family: Dict[str, str] = {}
        self.test_mode = test_mode
        
        # Load config from specified path
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with config_path.open('r') as f:
            self.config = yaml.safe_load(f)
        
        self.family_names = self.config['family_names']
        self.family_ids: List[str] = set(self.family_names.keys())

    def load_data(self) -> None:
        """Load family data from Google Sheet"""
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
        sheet = service.spreadsheets()
        
        result = sheet.values().get(
            spreadsheetId=self.config['spreadsheet']['id'],
            range=f"{self.config['spreadsheet']['sheet_name']}!A:T"
        ).execute()
        
        headers = result['values'][0]
        rows = result['values'][1:]
        col_idx = {name: idx for idx, name in enumerate(headers)}
        
        #self.family_ids = self._get_family_ids(rows, col_idx)
        self.family_addresses = self._get_family_addresses(rows, col_idx)
        self.email_to_family = self._get_email_mapping(rows, col_idx)
    
    def _get_family_ids(self, rows: List[List[str]], col_idx: Dict[str, int]) -> List[str]:
        """Extract list of valid family IDs"""
        family_ids = set()
        for row in rows:
            if len(row) < len(col_idx):
                continue
            
            family_id = row[col_idx['Family ID']]
            exclude = row[col_idx.get('Exclude from Calendar', -1)].lower() == 'yes'
            
            if family_id and not exclude:
                family_ids.add(family_id)
        
        return list(family_ids)
    
    def _get_family_addresses(self, rows: List[List[str]], col_idx: Dict[str, int]) -> Dict[str, str]:
        """Create mapping of family ID to address"""
        addresses = {}
        for row in rows:
            if len(row) < len(col_idx):
                continue
            
            family_id = row[col_idx['Family ID']]
            if not family_id or family_id in addresses:
                continue
            
            address = f"{row[col_idx['Address']]}\n{row[col_idx['City State Zip']]}"
            addresses[family_id] = address
        
        return addresses
    
    def _get_email_mapping(self, rows: List[List[str]], col_idx: Dict[str, int]) -> Dict[str, str]:
        """Create mapping of email to family ID"""
        email_mapping = {}
        for row in rows:
            if len(row) < len(col_idx):
                continue
            
            family_id = row[col_idx['Family ID']]
            email = row[col_idx['Primary Email']]
            
            if family_id and email:
                email_mapping[email] = family_id
        
        return email_mapping
    
    def make_assignments(self, 
                         exclude: Optional[List[str]] = None, 
                         seed_value: Optional[int] = 0,
                         verbose: bool = False) -> None:
        """Create random gift exchange assignments
        
        Args:
            exclude: Optional list of family IDs to exclude from assignments
            seed_value: Optional seed value for random assignments (defaults to current year)
        """
        
        # Use config exclusions if none provided
        if exclude is None:
            exclude = self.config.get('exclusions', [])
        
        family_ids = [fid for fid in self.family_ids if not exclude or fid not in exclude]
        
        self.assignments = best_algorithm(family_ids, seed_value)
        assert validate_assignments(family_ids, self.assignments)

        if self.test_mode:
            if verbose:
                print("\nAssignments:")
                for giver, receiver in self.assignments.items():
                    print(f"{self.family_names[giver]} -> {self.family_names[receiver]}")
        else:
            print(f"\nCreated assignments for {len(self.assignments)} families")

    def send_assignment_emails(self, is_reminder: bool = False) -> None:
        """Send emails to all families with their assignments"""
        for giver_id, receiver_id in self.assignments.items():
            subject = "Christmas Gift Exchange Assignment"
            if is_reminder:
                subject = "REMINDER: " + subject
            if self.test_mode:
                subject = "[TEST] " + subject
            
            message = self._compose_message(giver_id, receiver_id, is_reminder)
            
            giver_emails = [email for email, fid in self.email_to_family.items() 
                           if fid == giver_id]
            
            for email in giver_emails:
                if self.test_mode:
                    print(f"Would send email to: {email}")
                    print(f"Subject: {subject}")
                    print(f"Message:\n{message}\n")
                else:
                    send_email(
                        email,
                        None,
                        subj=subject,
                        msg_text=message
                    )

    def _compose_message(self, giver_id: str, receiver_id: str, is_reminder: bool) -> str:
        """Create email message text"""
        prefix = "REMINDER: " if is_reminder else ""
        receiver_names = self.family_names.get(receiver_id, receiver_id)
        return f"""
{prefix}Christmas Gift Exchange Assignment

You are giving to the family of: {receiver_names}

Their address is:
{self.family_addresses[receiver_id]}

Happy Holidays!
        """.strip()

def main():
    parser = argparse.ArgumentParser(description='Family Gift Exchange Assignment System')
    parser.add_argument('--config', type=str, default='./config.yaml',
                      help='Path to config file (default: ./config.yaml)')
    parser.add_argument('--test', action='store_true',
                      help='Run in test mode')
    parser.add_argument('--montecarlo-test', action='store_true',
                      help='Run tests on assignment distribution')
    args = parser.parse_args()

    exchange = FamilyGiftExchange(config_path=args.config, test_mode=args.test)
    exchange.load_data()

    if args.montecarlo_test:
        montecarlo_test()
        return

    current_year = datetime.now().year
    if args.test:
        current_year -= random.randint(0, 58)

    print("\nFamily Addresses:")
    for family_id, address in exchange.family_addresses.items():
        print(f"{family_id}: {address}")
        
    print("\nEmail to Family ID Mapping:")
    for email, family_id in exchange.email_to_family.items():
        print(f"{email}: {family_id}")
    
    exchange.make_assignments(exclude='0JMA', seed_value=current_year, verbose=True)
    exchange.send_assignment_emails()

def montecarlo_test():
    exchange = FamilyGiftExchange(config_path='./configs/config.example.yaml', test_mode=True)
    
    # Initialize tracking dictionary for all possible pairings
    pairing_counts = {}
    for giver in exchange.family_ids:
        pairing_counts[giver] = {receiver: 0 for receiver in exchange.family_ids if receiver != giver}
    
    total_runs = 10000
    for seed_value in range(total_runs):
        exchange.make_assignments(seed_value=seed_value)
        # Count each pairing
        for giver, receiver in exchange.assignments.items():
            pairing_counts[giver][receiver] += 1
    
    # Print results
    print(f"\nPairing Statistics (over {total_runs} runs):")
    print("\nGiver -> Receiver: Count (Percentage)")
    print("-" * 40)
    for giver in exchange.family_ids:
        print(f"\n{exchange.family_names[giver]} gives to:")
        for receiver, count in pairing_counts[giver].items():
            percentage = (count / total_runs) * 100
            print(f"  {exchange.family_names[receiver]}: {count} ({percentage:.1f}%)")


if __name__ == "__main__":
    main()
