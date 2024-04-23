#!/usr/bin/env python3

"""Stream a framebuffer via HTTP."""

import os
import sys
import signal
import logging
import argparse
import configparser
import json

from io import BytesIO
from time import sleep, strftime
from types import SimpleNamespace
from typing import Dict

from bottle import Bottle, response, ServerAdapter  # type: ignore[import-untyped]
from cheroot import wsgi
import numpy
from PIL import Image
import sdnotify  # type: ignore[import-untyped]

CONFIG_FILE = 'fbstream.ini'
CONFIG_PATHS = [os.path.dirname(os.path.realpath(__file__)),
                '/usr/local/etc/', '/etc/', '/conf/']

DEFAULT_LOG_LEVEL = logging.INFO


class Formatter(logging.Formatter):
    """Format logger output."""

    def formatTime(self, record, datefmt=None):
        """Use system timezone and add milliseconds."""
        datefmt = f'%Y-%m-%d %H:%M:%S.{round(record.msecs):03d} ' + strftime('%z')
        return strftime(datefmt, self.converter(record.created))


STDOUT_HANDLER = logging.StreamHandler(sys.stdout)
STDOUT_HANDLER.setFormatter(
    Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))


def signal_handler(sig, _frame):
    """Handle SIGINT cleanly."""
    print('\nCaught signal:', sig, '\n')
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

loggers: Dict[str, str] = {}


def get_logger(class_name, log_level):
    """Get logger objects for individual classes."""
    name = os.path.splitext(os.path.basename(__file__))[0]
    if log_level == logging.DEBUG:
        name = '.'.join([name, class_name])

    if loggers.get(name):
        return loggers.get(name)

    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(STDOUT_HANDLER)
    logger.setLevel(log_level)

    loggers[name] = logger
    return logger


class CherryPyServer(ServerAdapter):
    """Adapt newer CherryPy version for Bottle."""

    def run(self, handler):
        """Override the default method."""
        server = wsgi.Server((self.host, self.port), handler)
        try:
            server.start()
        except SystemExit:
            pass
        finally:
            server.stop()


class StreamHandler(Bottle):
    """Read the framebuffer, output to an HTTP stream."""

    def __init__(self, **kwargs):
        """Initialize configuration."""
        super().__init__()
        self.params = SimpleNamespace(**kwargs)
        self.logger = get_logger(self.__class__.__name__, self.params.log_level)

        self.logger.debug('Framebuffer size (h, w, d): (%s, %s, %s)',
                          self.params.height, self.params.width, self.params.depth)

        self.route('/stream', callback=self.stream)

        self.logger.info('Initialization done. Signalling readiness.')

        # this ignores exceptions unless in debug mode, no need for try/except
        sdnotify.SystemdNotifier().notify("READY=1")

    def stream(self):
        """Convert framebuffer into HTTP image stream."""
        nda = numpy.memmap(f'/dev/{self.params.device}', dtype='uint16', mode='r',
                           shape=(self.params.height, self.params.width,
                                  self.params.depth))

        def get_frame():
            while True:
                # sleep first, so we're not 100msec wrong by the time we display
                sleep(0.1)

                bytesbuf = BytesIO()
                Image.frombuffer('I;16', (self.params.width, self.params.height), nda).save(bytesbuf, 'PNG')
                # getbuffer() prevents the object closing, leaking memory
                # io_image = bytesbuf.getbuffer()
                io_image = bytesbuf.getvalue()

                yield (b'--frame\r\n'
                       b'Content-Type: image/png\r\n\r\n' + io_image + b'\r\n')

        response.set_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        return get_frame()

    def start_server(self):
        """
        Start the web server.

        Clear sys.argv before calling run(), else args get sent to the backend.
        """
        self.logger.info('Starting web server..')

        sys.argv = sys.argv[:1]
        self.run(host='0.0.0.0', port=8808, server=CherryPyServer,
                 minthreads=self.params.minthreads,
                 maxthreads=self.params.maxthreads, debug=True)


class ConfigHandler():
    """Read config files and parse commandline arguments."""

    log_level = DEFAULT_LOG_LEVEL

    def __init__(self):
        """Initialize default config, parse config file and command line args."""
        self.defaults = {
            'log_level': self.log_level,
            'config_file': CONFIG_FILE,
            'device': 'fb1',
            'width': 'auto',
            'height': 'auto',
            'depth': 'auto',
            'minthreads': 1,
            'maxthreads': 4
        }

        self.args = []
        self.config_parser = argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=__doc__, add_help=False)

        self.parse_initial_config()
        self.parse_config_file()
        self.parse_command_line()
        self.check_args()

    def parse_initial_config(self):
        """Just enough argparse to specify a config file and a debug flag."""
        self.config_parser.add_argument(
            '-c', '--config_file', action='store', metavar='FILE',
            help='external configuration file (default: \'%(default)s\')')
        self.config_parser.add_argument(
            '--debug', action='store_true', help='turn on debug messaging')

        self.args = self.config_parser.parse_known_args()[0]

        if self.args.debug:
            self.log_level = logging.DEBUG
            self.defaults['log_level'] = logging.DEBUG

        self.logger = get_logger(self.__class__.__name__, self.log_level)
        self.logger.debug('Initial args: %s', json.dumps(vars(self.args), indent=4))

    def parse_config_file(self):
        """Find and read external configuration files, if they exist."""
        self.logger.debug('self.args.config_file: %s', self.args.config_file)

        # find external configuration if none is specified
        if self.args.config_file is None:
            for config_path in CONFIG_PATHS:
                config_file = os.path.join(config_path, CONFIG_FILE)
                self.logger.debug('Looking for config file: %s', config_file)
                if os.path.isfile(config_file):
                    self.logger.info('Found config file: %s', config_file)
                    self.args.config_file = config_file
                    break

        if self.args.config_file is None:
            self.logger.info('No config file found.')

        # read external configuration if specified and found
        if self.args.config_file is not None:
            if os.path.isfile(self.args.config_file):
                config = configparser.ConfigParser()
                config.read(self.args.config_file)
                self.defaults.update(dict(config.items("general")))
                self.defaults.update(dict(config.items("stream")))
                self.logger.debug('Args from config file: %s',
                                  json.dumps(self.defaults, indent=4))
            else:
                self.logger.error('Config file (%s) does not exist.',
                                  self.args.config_file)

    def parse_command_line(self):
        """
        Parse command line arguments.

        Overwrite the default config and anything found in a config file.
        """
        parser = argparse.ArgumentParser(
            description='fbstream', parents=[self.config_parser])
        parser.set_defaults(**self.defaults)

        parser.add_argument(
            '-d', '--device', action='store', metavar='DEVICE',
            help='path to the framebuffer device (default: \'%(default)s\')')
        parser.add_argument(
            '-H', '--height', action='store', metavar='PIXELS',
            help='height of the framebuffer (default: \'%(default)s\')')
        parser.add_argument(
            '-W', '--width', action='store', metavar='PIXELS',
            help='width of the framebuffer (default: \'%(default)s\')')
        parser.add_argument(
            '-D', '--depth', action='store', metavar='INT',
            help='colour depth of the framebuffer (default: \'%(default)s\')')
        parser.add_argument(
            '--minthreads', action='store', metavar='INT',
            help='minimum server threads (default: \'%(default)s\')')
        parser.add_argument(
            '--maxthreads', action='store', metavar='INT',
            help='maximum server threads (default: \'%(default)s\')')

        self.args = parser.parse_args()
        self.logger.debug('Parsed command line:\n%s',
                          json.dumps(vars(self.args), indent=4))

    def check_args(self):
        """Check we have all the information we need to run."""
        do_exit = False
        if self.args.device == '':
            self.logger.error('No frambuffer device specified.')
            do_exit = True
        if not isinstance(int(self.args.minthreads), int):
            self.logger.error('Invalid minthreads: %s', self.args.minthreads)
            do_exit = True
        if not isinstance(int(self.args.maxthreads), int):
            self.logger.error('Invalid maxthreads: %s', self.args.maxthreads)
            do_exit = True

        if do_exit:
            sys.exit(1)

        sysfs_path = f'/sys/class/graphics/{self.args.device}/'
        self.logger.debug('sysfs_path: %s', sysfs_path)

        if 'auto' in (self.args.width, self.args.height):
            this_file = sysfs_path + 'virtual_size'
            try:
                with open(this_file, 'r', encoding='utf-8') as f:
                    width, height = [int(i) for i in f.read().split(',')]
            except OSError:
                self.logger.warning('Could not read %s', this_file)

            if self.args.width == 'auto':
                self.args.width = width
            if self.args.height == 'auto':
                self.args.height = height

        if self.args.depth == 'auto':
            this_file = sysfs_path + 'bits_per_pixel'
            try:
                with open(this_file, 'r', encoding='utf-8') as f:
                    self.args.depth = int(f.read()[:2])
            except OSError:
                self.logger.warning('Could not read %s', this_file)

    def get_args(self):
        """Return all config parameters."""
        return self.args


def main():
    """Do all the things."""
    config = ConfigHandler()
    args = vars(config.get_args())
    stream_handler = StreamHandler(**args)

    try:
        stream_handler.start_server()
    except SystemExit:
        pass
    finally:
        print('Exiting.')


if __name__ == '__main__':
    main()
