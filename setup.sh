# sudo apt-get install python3.10
python3 -m venv ../.venv
source ../.venv/bin/activate
TMPDIR=.. pip install -r requirements.txt
touch ./.env