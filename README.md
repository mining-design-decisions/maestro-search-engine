# Maestro Search Engine
This repository contains the search engine API of Maestro. It can create search indexes, and it can perform search
queries which can be enhanced with deep learning classifiers' predictions.

## Setup
The setup process is described below. Note that, because it uses HTTPS, it needs SSL certificates (fullchain{x}.pem and
privkey{x}.pem):
```
git clone git@github.com:mining-design-decisions/maestro-search-engine.git
cd maestro-search-engine
sudo cp /etc/letsencrypt/live/issues-db.nl/fullchain1.pem pylucene/fullchain.pem
sudo cp /etc/letsencrypt/live/issues-db.nl/privkey1.pem pylucene/privkey.pem
sudo cp /etc/letsencrypt/live/issues-db.nl/fullchain2.pem status_proxy/fullchain.pem
sudo cp /etc/letsencrypt/live/issues-db.nl/privkey2.pem status_proxy/privkey.pem
sudo docker compose up --build -d
```