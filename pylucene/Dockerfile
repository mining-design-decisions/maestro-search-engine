FROM python:3.10-slim-buster
RUN apt-get update \
    && apt-get install -y default-jdk ant
RUN apt-get install -y curl
RUN apt-get install -y build-essential

WORKDIR /usr/lib/jvm/default-java/jre/lib
RUN ln -s ../../lib amd64

WORKDIR /usr/src/pylucene
RUN curl https://dlcdn.apache.org/lucene/pylucene/pylucene-8.11.0-src.tar.gz \
    | tar -xz --strip-components=1
RUN cd jcc \
    && NO_SHARED=1 JCC_JDK=/usr/lib/jvm/default-java python setup.py install
RUN make all install JCC='python -m jcc' ANT=ant PYTHON=python NUM_FILES=8

WORKDIR /usr/src
RUN rm -rf pylucene

WORKDIR /python-docker
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY . .
CMD ["python3.10", "-m", "app"]