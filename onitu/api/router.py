from threading import Thread

import zmq
from logbook import Logger

class Router(Thread):
    """Thread waiting and for requests concerning a chunk of file"""

    def __init__(self, name, redis, read_chunk):
        super(Router, self).__init__()

        self.name = name
        self.redis = redis
        self.read_chunk = read_chunk

        self.logger = Logger("{} - Router".format(self.name))

        self.context = zmq.Context.instance()

    def run(self):
        self.router = self.context.socket(zmq.ROUTER)
        port = self.router.bind_to_random_port('tcp://*')
        self.redis.set('drivers:{}:router'.format(self.name), port)

        while True:
            self.logger.info("Listening...")
            msg = self.router.recv_multipart()
            self._respond_to(*msg)

    def _respond_to(self, identity, filename, offset, size):
        self.logger.info("Getting chunk of size {} from offset {} in {}".format(size, offset, filename))
        chunk = self.read_chunk(filename, int(offset), int(size))
        self.router.send_multipart((identity, chunk))
        self.logger.info("Chunk sended")