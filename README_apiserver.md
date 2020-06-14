## Prerequisites

- Linux operating system
- minimum docker version: 17.06.0
- minimum docker-compose version: 1.18.0

## Installation

It is highly recommended to install the software under /opt directory

```
cd /opt
tar xvzf path/to/rtsp2mjpg.tar.gz
cd rtsp2mjpg
docker build -t rtsp2mjpg .
docker-compose build
```

In order to start the service, run
`docker-compose up -d`

to stop the service, run
`docker-compose down`

## Port forwarding

By default, the API server and the demo application are both listenig on port 80. Swagger UI will be available at `http://<host>` address, and demo application will be available under http://<host>/demo

Please note that Swagger UI is hard to get working behind reverse proxy, so if you change nginx port in docker-compose.yaml, be aware that you may not see Swagger UI under `http://ip:port/`. However, `/api/streams.*` endpoints will work. If you cannot use port 80, and still want to access Swagger UI, just uncomment `PORT` section for `apiserver` service in `docker-compose.yaml` and ajust the port number.

## Fine-tuning nginx.conf

It is possible to disable access to demo application and Swagger API, by editing `nginx/default.conf` file and disabling corresponding sections.

## Supported ffserver parameters ##
see https://www.systutorials.com/docs/linux/man/1-ffserver/#lbAV for the full list of parameters. If the parameter does not require a value, use empty "" string as a value to add the parameter or "-" string as a value to remove it, e.g.:
```
ffserverParams: {
  "NoAudio": "-"
}
```
to remove NoAudio option from `<Stream>` section of `ffserver.conf` file.
