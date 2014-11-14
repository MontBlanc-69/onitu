import os
import string
import random
import hashlib
from path import path

from dropbox.session import DropboxSession
from dropbox.client import DropboxClient
from dropbox.rest import ErrorResponse

from tests.utils import driver
from onitu_dropbox.dropbox_driver import (ONITU_APP_KEY,
                                          ONITU_APP_SECRET,
                                          ONITU_ACCESS_TYPE)
from onitu.utils import u, b  # Unicode helpers


class Driver(driver.Driver):
    SPEED_BUMP = 1

    def __init__(self, *args, **options):
        if u'root' not in options:
            rand = u''.join(random.sample(
                string.ascii_letters + string.digits, 10))
            options[u'root'] = u"/{}/".format(rand)
        if u'key' not in options:
            options[u'access_key'] = os.environ[u'ONITU_DROPBOX_KEY']
        if u'secret' not in options:
            options[u'access_secret'] = os.environ[u'ONITU_DROPBOX_SECRET']
        if u'changes_timer' not in options:
            options[u'changes_timer'] = 10
        sess = DropboxSession(ONITU_APP_KEY,
                              ONITU_APP_SECRET,
                              ONITU_ACCESS_TYPE)
        # Use the OAuth access token previously retrieved by the user and typed
        # into Onitu configuration.
        sess.set_token(options[u'access_key'], options[u'access_secret'])
        self.dropbox_client = DropboxClient(sess)
        super(Driver, self).__init__(u'dropbox',
                                     *args,
                                     **options)

    def stringify(self, s):
        if type(s) != str:
            s = b(s)
        return s

    def prefix_root(self, filename):
        root = u(str(self.root))
        filename = u(filename)
        if not filename.startswith(root):
            filename = root + filename
        return filename

    @property
    def root(self):
        root = self.options[u'root']
        if not root.endswith(u'/'):
            root += u'/'
        return path(root)

    def close(self):
        try:
            self.dropbox_client.file_delete(str(self.root))
        # It arrives that we try to delete the root twice.
        # When trying it Dropbox raises a 404 Error because the root
        # is already deleted. Since it isn't a real issue, we can ignore it
        except ErrorResponse:
            pass

    def mkdir(self, subdirs):
        self.dropbox_client.file_create_folder(
            self.stringify(self.prefix_root(subdirs)))

    def rmdir(self, path):
        self.unlink(self.prefix_root(path))

    def write(self, filename, content):
        self.dropbox_client.put_file(
            self.stringify(self.prefix_root(filename)), content)

    def generate(self, filename, size):
        self.write(
            self.stringify(self.prefix_root(filename)), os.urandom(size))

    def exists(self, filename):
        metadata = self.dropbox_client.metadata(
            self.stringify(self.prefix_root(filename)),
            include_deleted=True)
        return not metadata.get(u'is_deleted', False)

    def unlink(self, filename):
        self.dropbox_client.file_delete(
            self.stringify(self.prefix_root(filename)))

    def rename(self, source, target):
        self.dropbox_client.file_move(
            from_path=self.stringify(self.prefix_root(source)),
            to_path=self.stringify(self.prefix_root(target)))

    def checksum(self, filename):
        data = self.dropbox_client.get_file(
            self.stringify(self.prefix_root(filename)))
        return hashlib.md5(data.read()).hexdigest()


class DriverFeatures(driver.DriverFeatures):
    pass
