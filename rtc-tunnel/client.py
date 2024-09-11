#!/usr/bin/env python3

import argparse
import asyncio
import logging.handlers
import sys
import os

from tunnel import TunnelClient
from tunnel.signaling import IoTSignaling


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.handlers.TimedRotatingFileHandler('/tmp/rtc-client.log', when="midnight", backupCount=3),
        logging.StreamHandler(sys.stdout)
    ])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='RTC Tunneling client')
    parser.add_argument('--destination-port', '-d', help='Destination port', default=22)
    parser.add_argument('--source-port', '-p', help='Source port', default=3334)
    parser.add_argument('--device-id', '-i', help='Device id', default='')
    # add_signaling_arguments(parser)

    args = parser.parse_args()

    iothub_connection_string = os.environ.get('IOTHUB_CONNECTION_STRING')
    method_name = 'webrtc_signaling'

    id_rsa_pub=None
    try:
        with open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_rsa.pub'), 'r') as f:
            id_rsa_pub = f.read()
    except:
        pass
    id_dsa_pub=None
    try:
        with open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_dsa.pub'), 'r') as f:
            id_dsa_pub = f.read()
    except:
        pass

    signal_server = IoTSignaling(iothub_connection_string, args.device_id, method_name, id_rsa_pub, id_dsa_pub)
    client = TunnelClient('', args.source_port, args.destination_port, signal_server)

    loop = asyncio.get_event_loop()
    try:
        print('loop.run_until_complete')
        loop.run_until_complete(client.run_async())
        print('loop.run_until_complete: done')
    except KeyboardInterrupt:  # CTRL+C pressed
        pass
    finally:
        loop.run_until_complete(client.close_async())
        loop.close()
