FROM alpine:latest

# add mkbrr repo
RUN apk add --no-cache mkbrr --repository http://dl-cdn.alpinelinux.org/alpine/edge/testing

# install requirements
RUN apk add --no-cache --upgrade ffmpeg mediainfo python3 git py3-pip python3-dev python3-tkinter g++ cargo mktorrent rust bash

# create virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"

# change workdir
WORKDIR /app

# install reqs
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# copy everything
COPY . .

# default entrypoint runs uploadrr; override with `--entrypoint tail` for interactive CLI use
ENTRYPOINT ["python3", "/app/upload.py"]
CMD ["-h"]