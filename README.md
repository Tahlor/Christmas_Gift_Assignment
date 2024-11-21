# Family Gift Exchange Assigner

A Python-based system for managing family gift exchanges using Google Sheets for participant data and automated email assignments.

## Features

- Loads participant data from Google Sheets
- Randomly assigns gift exchange pairs
- Sends automated assignment emails
- Supports test mode for verification
- Configurable exclusions and family groupings

## Setup

1. Create a Google Cloud Project and enable the Google Sheets API
2. Create credentials (OAuth 2.0 Client ID) and download as `credentials.json`
3. Clone the repository and install dependencies:
    ```bash
    git clone https://github.com/Tahlor/utils.git general_tools
    pip install -r requirements.txt
    ```

4. Place credentials in the project structure:
    ```
    family-gift-exchange/
    ├── credentials/
    │   ├── credentials.json
    │   ├── token.pickle (will be created automatically)
    │   └── email_credentials.json
    ├── configs/
    │   └── config.yaml
    ├── general_tools/  # Cloned from https://github.com/Tahlor/utils
    └── assigner.py
    ```

5. Set up email credentials:
   - Create an `email_credentials.json` file in the credentials directory
   - For Gmail, you'll need to:
     - Enable 2-factor authentication
     - Generate an App Password
     - Format the credentials file as:
       ```json
       {
           "email": "your.email@gmail.com",
           "password": "your-app-password"
       }
       ```
   See the [credential utils documentation](https://github.com/Tahlor/utils/blob/master/general_tools/credential_utils.py) and [email utils documentation](https://github.com/Tahlor/utils/blob/master/general_tools/my_email.py) for more details.

6. Create a configuration file (see Configuration section below)

## Usage

Basic usage:
```bash
python assigner.py --config ./configs/config.yaml
```

Test mode (prints assignments instead of sending emails):
```bash
python assigner.py --config ./configs/config.example.yaml --test
```

## Configuration

Create a `config.yaml` file with the following structure:
```yaml
spreadsheet:
  id: "your-google-sheet-id"
  sheet_name: "Sheet1"

# Map family IDs to display names
family_names:
  "1ABC": "Smith Family"
  "2DEF": "Jones Family"

# Optional: List of family IDs to exclude
exclusions:
  - "3GHI"
```

### Google Sheet Structure

The Google Sheet should have the following columns:
- Family ID
- Primary Email
- Address
- City State Zip
- Exclude from Calendar (optional)

## Command Line Arguments

- `--config`: Path to configuration file (default: ./config.yaml)
- `--test`: Run in test mode, which will:
  - Print assignments instead of sending emails
  - Prefix email subjects with "[TEST]"
  - Display email content that would be sent

## Development

To run tests on assignment distribution:
```bash
python assigner.py --montecarlo_test
```

## Algorithms

There are some subtlties when choosing a derangement algorithm. The easiest solution is to shuffle the list and produce a single n-cycle of assignments. However, if we choose to make random assignments (and validate them), we generally need to shuffle both the givers and receivers.

Simulating algorithms with 4 participants over 100000 trials:
```
| Algorithm                             | Time (s) | Max Deviation % |
|---------------------------------------|----------|------------------|
| smart_last_choice_with_shuffle        | 6.010    | 0.17             |
| random_choice_with_removal_shuffled   | 6.262    | 0.24             |
| double_shuffle                        | 6.193    | 0.23             |
| shuffle_and_zip                       | 9.750    | 0.27             |
| random_choice_with_removal_no_shuffle | 5.323    | 11.55            |
| shuffle_first_valid                   | 5.370    | 21.89            |
```

`smart_last_choice_with_shuffle` is the only one guaranteed to produce valid assignments on the first try. Shuffling is still needed, otherwise the second to last person is disproportionately assigned to the last person.