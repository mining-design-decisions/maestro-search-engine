# Maestro Search Engine
This repository contains the search engine API of Maestro. It can create search indexes, and it can perform search
queries which can be enhanced with deep learning classifiers' predictions.

## Setup
The setup process is described below. Note that, because it uses HTTPS, it needs an SSL certificate (fullchain.pem and
privkey.pem).
```
git clone git@github.com:GWNLekkah/add-search-engine.git
cd maestro-search-engine
sudo cp /etc/letsencrypt/live/issues-db.nl/fullchain.pem pylucene/fullchain.pem
sudo cp /etc/letsencrypt/live/issues-db.nl/privkey.pem pylucene/privkey.pem
sudo docker compose up --build -d
```