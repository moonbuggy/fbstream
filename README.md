# fbstream
Stream a framebuffer device via HTTP.

This is designed to work with [moonbuggy/fbgpsclock][fbgpsclock], but should work
generally with any framebuffer device.

> [!NOTE]
> This is a work in progress. Currently colours go missing so the output is
> greyscale, and the streaming is inefficient if there's multiple clients accessing
> it at once.

## Usage
```
usage: fbstream.py [-h] [-c FILE] [--debug] [-d DEVICE] [-H PIXELS] [-W PIXELS]
                   [-D INT] [--minthreads INT] [--maxthreads INT]

fbstream

options:
  -h, --help            show this help message and exit
  -c FILE, --config_file FILE
                        external configuration file (default: 'fbstream.ini')
  --debug               turn on debug messaging
  -d DEVICE, --device DEVICE
                        path to the framebuffer device (default: 'fb1')
  -H PIXELS, --height PIXELS
                        height of the framebuffer (default: 'auto')
  -W PIXELS, --width PIXELS
                        width of the framebuffer (default: 'auto')
  -D INT, --depth INT   colour depth of the framebuffer (default: 'auto')
  --minthreads INT      minimum server threads (default: '1')
  --maxthreads INT      maximum server threads (default: '4')
```

Any command line parameters take precedence over settings in _fbstream.ini_.

_fbstream_ will try to automatically determine height, width and depth from
_/sys/class/graphics/<DEVICE>/_. Values can be provided manually if this fails.

## Installation
```sh
sudo /usr/bin/install -c -m 755 fbstream.py '/usr/local/bin'
sudo /usr/bin/install -c -m 644 fbstream.ini '/usr/local/etc'
sudo /usr/bin/install -c -m 644 fbstream.service '/lib/systemd/system'
sudo systemctl daemon-reload
sudo systemctl enable fbstream
sudo systemctl start fbstream
```

## Links
*   [moonbuggy/fbgpsclock][fbgpsclock]

[fbgpsclock]: https://github.com/moonbuggy/fbgpsclock
