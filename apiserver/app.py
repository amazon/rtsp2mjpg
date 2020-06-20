#!/usr/bin/env python

import atexit
import time
import docker
import json
from jinja2 import Template
import tarfile
from io import BytesIO

import logging as logger
logger.basicConfig(level="DEBUG")

from flask import Flask, request, render_template
from flask_restplus import Resource, Api, fields

app = Flask(__name__)
api = Api(app, version='1.0', title='ffmpeg API',
    description='Proof of concept API for ffserver')

ns = api.namespace('api', 'Stream management')
stream = api.model('Stream', {
    'name': fields.String(required=True, description='Stream unique identifier'),
    'url': fields.Url(required=True, description='Stream URL'),
    'ffmpegInputOpts': fields.String(required=False, description='ffmpeg command-line options for the input'),
    'ffmpegOutputOpts': fields.String(required=False, description='ffmpeg command-line options for the output'),
    'ffmpegLogLevel':  fields.String(required=False, description='quiet|panic|fatal|error|warning|info|verbose|debug|trace'),
    'ffserverLogLevel':  fields.String(required=False, description='quiet|panic|fatal|error|warning|info|verbose|debug|trace'),
    'ffserverParams': fields.Wildcard(fields.String,
                        description='Dictionary of ffserver stream parameters, see https://www.systutorials.com/docs/linux/man/1-ffserver/#lbAV')
})

class StreamController(object):

    FFMPEG_DOCKER_IMAGE = 'rtsp2mjpg'

    FFMPEG_DOCKER_HEALTHCHECK =  {
        "Test": [
            "CMD-SHELL",
            "curl -f http://localhost:8090/still.jpg --max-time 10 --output /dev/null || (pkill -KILL ffmpeg; exit 10)"
        ],
        "Interval": 15000000000,
        "Timeout": 11000000000,
        "Retries": 1,
        "StartPeriod": 30000000000
    }

    STREAM_STATE_MAP = {
        'exited':    'stopped',
        'running':   'running',
        'healthy':   'online',
        'unhealthy': 'offline',
        'starting':  'offline'
    }

    FFSERVER_DEFAULTS = {
        'Format': 'mpjpeg',
        'VideoFrameRate': '25',
        'VideoSize': '640x360',
        'VideoQMin': '1',
        'VideoQMax': '15',
        'VideoIntraOnly': 'true',
        'NoAudio': '',
        'Strict': '-1',
        'NoDefaults': 'true'
    }
    FFMPEG_INPUT_OPTS = '-use_wallclock_as_timestamps 1'
    FFMPEG_OUTPUT_OPTS = '-async 1 -vsync 1'
    FFSERVER_LOG_LEVEL = 'warning' # one of: quiet, panic, fatal, error, warning, info, verbose, debug, trace
    FFMPEG_LOG_LEVEL = 'warning' # one of: quiet, panic, fatal, error, warning, info, verbose, debug, trace

    def __get_ffserver_conf(self, params_dict):
        template_str = open('templates/ffserver.conf.j2', 'r').read()
        result = Template(template_str).render(params_dict)
        return result

    def __get_tar_from_file_data(self, path, data):
        pw_tarstream = BytesIO()
        pw_tar = tarfile.TarFile(fileobj=pw_tarstream, mode='w')
        file_data = data.encode('utf8')
        tarinfo = tarfile.TarInfo(name=path)
        tarinfo.size = len(file_data)
        tarinfo.mtime = time.time()
        tarinfo.mode = 0o600
        pw_tar.addfile(tarinfo, BytesIO(file_data))
        pw_tar.close()
        pw_tarstream.seek(0)
        return pw_tarstream

    def __get_env_as_dict(self, container):
        env = container.attrs['Config']['Env']
        return dict([item.split('=', 1) for item in env])

    def __get_container_info(self, container):
        env = self.__get_env_as_dict(container)
        logger.info(env)
        try:
            ffserverParams = json.loads(env.get('FFSERVER_PARAMS', {}))
        except json.decoder.JSONDecodeError:
            logger.warning("cannot load ffserver parameters for container {0}".format(container.name))
            ffserverParams = {}
        return {'name': container.labels['io.ecamsecure.stream.name'],
                'url': env['RTSP_URL'],
                'status': self.STREAM_STATE_MAP.get(container.status, 'unknown'),
                'health': self.STREAM_STATE_MAP.get(container.attrs['State'].get('Health', {}).get('Status'), 'unknown'),
                'ffmpegInputOpts': env['FFMPEG_INPUT_OPTS'],
                'ffmpegOutputOpts': env['FFMPEG_OUTPUT_OPTS'],
                'ffserverLogLevel': env['FFSERVER_LOG_LEVEL'],
                'ffmpegLogLevel': env['FFMPEG_LOG_LEVEL'],
                'ffserverParams': ffserverParams
                }

    def __get_container_by_stream_name(self, stream_name):
        client = docker.from_env()
        containers = client.containers.list(
            all=True,
            filters={'label': "io.ecamsecure.stream.name={0}".format(stream_name)}
        )
        if len(containers)>0:
            return containers[0]

    def describe(self, name):
        container = self.__get_container_by_stream_name(name)
        return self.__get_container_info(container)

    def describe_streams(self):
        client = docker.from_env()
        streams = [ self.__get_container_info(container) for container in client.containers.list(
                        all=True,
                        ignore_removed=True,
                        filters={'label': 'io.ecamsecure.app=ffmpeg'})]
        return streams

    def create(self, config):
        logger.info("create new stream with configuration: {0}".format(config))
        name = config.get('name')
        url = config.get('url')
        if not name:
            logger.error("cannot create stream with empty name")
            return {'error': 'empty name'}
        if not url:
            logger.error("cannot create stream with empty url")
            return {'error': 'empty url'}
        env = {
            'RTSP_URL': config.get('url'),
            'FFMPEG_INPUT_OPTS': config.get('ffmpegInputOpts', self.FFMPEG_INPUT_OPTS),
            'FFMPEG_OUTPUT_OPTS': config.get('ffmpegOutputOpts', self.FFMPEG_OUTPUT_OPTS),
            'FFSERVER_LOG_LEVEL': config.get('ffserverLogLevel', self.FFSERVER_LOG_LEVEL),
            'FFMPEG_LOG_LEVEL': config.get('ffmpegLogLevel', self.FFMPEG_LOG_LEVEL)
        }
        merged_params = self.FFSERVER_DEFAULTS.copy()
        params = config.get('ffserverParams', {})
        merged_params.update(params)
        env['FFSERVER_PARAMS'] = json.dumps(merged_params)
        logger.debug("environment for new container: {}".format(env))
        ffserver_conf = self.__get_ffserver_conf({'params': merged_params})
        ffserver_conf_tar = self.__get_tar_from_file_data('ffserver.conf', ffserver_conf)
        client = docker.from_env()
        container = client.containers.create(self.FFMPEG_DOCKER_IMAGE,
                        detach=True,
                        healthcheck = self.FFMPEG_DOCKER_HEALTHCHECK,
                        restart_policy={'Name': 'always'},
                        environment=env,
                        labels={
                            'io.ecamsecure.app': 'ffmpeg',
                            'app': 'ffmpeg',
                            'io.ecamsecure.stream.name': name,
                            'stream.name': name
                        },
                        hostname=name,
                        name="more_rtsp2mjpg_ffmpeg_{0}_1".format(name))

        container.put_archive('/etc/', ffserver_conf_tar)
        container.start()
        logger.debug("container created")
        network = client.networks.get('ffmpeg-network')
        network.connect(container, aliases=['rtsp2mjpg_'+name])
        logger.debug("container attached to rtsp2mjpg network")
        return self.__get_container_info(container)

    def start_all(self):
        for stream in self.describe_streams():
            if stream['status'] != "running":
                self.start(stream['name'])

    def stop_all(self):
        logger.info("shitting down running containers")
        for stream in self.describe_streams():
            self.stop(stream['name'])

    def start(self, name):
        logger.info('starting stream ' + name)
        client = docker.from_env()
        container = self.__get_container_by_stream_name(name)
        network = client.networks.get('ffmpeg-network')
        network.connect(container, aliases=['rtsp2mjpg_'+name])
        container.start()

    def stop(self, name, force=True):
        logger.info('stopping stream ' + name)
        client = docker.from_env()
        container = self.__get_container_by_stream_name(name)
        network = client.networks.get('ffmpeg-network')
        if force:
            container.kill()
        else:
            container.stop()
            container.wait()
            network.disconnect(container, force=True)

    def delete(self, name):
        container = self.__get_container_by_stream_name(name)
        if container is None:
            return False
        logger.info('stopping stream ' + name)
        container.remove(force=True)
        return True

@ns.route('/streams')
class StreamCollectionCollection(Resource):
    def get(self):
        """ Get list of streams.

        Return list of stream objects as JSON array::

            [
                {
                    "name": "stream-1",
                    "url": "rtsp://example.com:5554/camera-1,
                    "state": "on",
                    ffserverParams: {
                        "VideoSize": "640x480",
                        "VideoFrameRate": 24
                    },
                },
                {
                    "name": "stream-2",
                    "url": "rtsp://example.com:5554/camera-2,
                    "state": "on",
                    ffserverParams: {
                        "VideoSize": "640x480",
                        "VideoFrameRate": 24
                    },
                },
                ...
            ]"""
        streams = stream_controller.describe_streams()
        return streams, 200

    @ns.expect(stream)
    def post(self):
        """ Create new stream.

        Stream parameters should be passed as a JSON array::

            {
                "name": "stream-1",
                "url": "rtsp://example.com:5554/camera-1,
                ffserverParams: {
                    "VideoSize": "640x480",
                    "VideoFrameRate": 24
                }
            }"""
        if request.is_json:
            config = request.get_json()
        else:
            name = request.form['name']
            url = request.form['url']
            config = {
                'name': name,
                'url': url
            }
        stream_controller.create(config)
        return {}, 201

@ns.route('/streams/<string:name>')
class Stream(Resource):
    def get(self, name):
        """ Get stream status."""
        return stream_controller.describe(name), 200

    @ns.expect(stream)
    def put(self, name):
        """ Create or update the stream with a given name"""
        stream_controller.delete(name)
        if request.is_json:
            req_json = request.get_json()
            logger.debug(req_json)
            req_json['name'] = name
            res = stream_controller.create(req_json)
            logger.debug("POST result: {}".format(res))
        else:
            return {"message" : "invalid request data"}, 400
        logger.debug("stream created")
        return res, 200

    def delete(sef, name):
        """ Delete the stream with a given name"""
        if stream_controller.delete(name):
            return {}, 204
        else:
            return {'message': "stream not found: {}".format(name)}, 404

@ns.route('/streams/<string:name>/start')
class StreamStart(Resource):
    def post(self, name):
        """Start the stream."""
        stream_controller.start(name)
        return {}, 204

@ns.route('/streams/<string:name>/stop')
class StreamStop(Resource):
    def post(self, name):
        """Stop the stream."""
        stream_controller.stop(name, force=True)
        return {}, 204

@app.route('/demo')
def index():
    return render_template('index.html',
                           streams=stream_controller.describe_streams())

def exit_handler():
    logger.debug("exit_handler(): shutting down")
    stream_controller.stop_all()

stream_controller = StreamController()
atexit.register(exit_handler)

if __name__ == '__main__':
    stream_controller.start_all()
    app.run(debug=True, host='0.0.0.0')
