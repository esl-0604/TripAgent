# GCP Deploy Notes

## Prerequisites
- GCP project with Compute Engine API enabled
- Billing account linked
- gcloud CLI authenticated

## VM creation
```bash
gcloud compute instances create trip-listener \
    --project=<PROJECT_ID> \
    --zone=us-west1-a \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --tags=trip-listener
```

## Initial VM setup (SSH in)
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git tzdata
sudo useradd -m -s /bin/bash maestro
sudo mkdir -p /opt/maestroagent
sudo chown -R maestro:maestro /opt/maestroagent

sudo -u maestro git clone https://github.com/esl-0604/TripAgent.git /opt/maestroagent
cd /opt/maestroagent
sudo -u maestro python3 -m venv venv
sudo -u maestro venv/bin/pip install -r requirements.txt
```

## Inject .env (from local)
```bash
gcloud compute scp .env trip-listener:/tmp/.env --zone=us-west1-a
gcloud compute ssh trip-listener --zone=us-west1-a --command='
    sudo mv /tmp/.env /opt/maestroagent/.env &&
    sudo chown maestro:maestro /opt/maestroagent/.env &&
    sudo chmod 600 /opt/maestroagent/.env'
```

## systemd service
```bash
sudo cp /opt/maestroagent/deploy/trip-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trip-listener
sudo systemctl start trip-listener
sudo systemctl status trip-listener
```

## Logs
```bash
sudo journalctl -u trip-listener -f
sudo journalctl -u trip-listener --since "10 min ago"
```

## Update workflow
```bash
cd /opt/maestroagent
sudo -u maestro git pull
sudo -u maestro venv/bin/pip install -r requirements.txt
sudo systemctl restart trip-listener
```
