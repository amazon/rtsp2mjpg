#!/bin/sh

mkdir -f /opt
tar xvzf rtsp2mjpg.tar.gz -C /opt
cd /opt/rtsp2mjpg
docker build . -t rtsp2mjpg
cp rtsp2mjpg.service /etc/systemd/system/
systemctl enable rtsp2mjpg.service
