FROM python:3.10-slim-buster
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY . .
CMD ["python3.10", "-m", "app"]